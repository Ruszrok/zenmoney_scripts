#!/usr/bin/env python3
"""Detect ZenMoney iOS connection/login failures in redacted Apple logs.

Input is an Apple Console export: tab-separated `level, time, process, message`
lines, with wrapped messages continuing on untabbed lines. For each file the
tool reports the endpoints touched, the HTTP statuses seen, a deduplicated list
of known failure signatures, and a short "likely causes" summary.

Everything the tool knows about log lines lives in `SIGNATURES`: one row per
signature carrying its severity, the label it contributes to the per-connection
timeline, and the prose it contributes to "likely causes". Teaching the tool a
new signature should never require editing anything outside that table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple

# ───────────────────────────── report shaping ──────────────────────────────
MESSAGE_MAX_LEN = 260  # evidence messages are truncated to this in reports
DEDUP_KEY_LEN = 120  # ...but two lines sharing this prefix count as one
DEFAULT_TOP_N = 15  # entries kept per counter
DEFAULT_MAX_EVIDENCE = 18  # noteworthy rows printed per file in markdown

SEVERITY_ORDER = ("high", "medium", "low", "info")
UNKNOWN_SEVERITY_RANK = len(SEVERITY_ORDER)

CONTINUATION = "continuation"  # pseudo-level for wrapped message lines
# Levels worth counting when no signature recognises the line — a rising
# number here means the tool is blind to something the log considers serious.
UNMATCHED_LEVELS = frozenset({"error", "fault"})

# ──────────────────────────── line-level parsing ───────────────────────────
LOG_LINE_RE = re.compile(
    r"^(?P<level>\w+)\t(?P<time>\d\d:\d\d:\d\d\.\d+[+-]\d{4})\t"
    r"(?P<process>[^\t]+)\t(?P<message>.*)$"
)
# Match the whole URL — commas are legal in a query string — then strip the
# punctuation that more likely ends the sentence than the URL.
URL_RE = re.compile(r"https?://\S+")
URL_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"
HOST_TOKEN_RE = re.compile(
    r"\b(?P<kind>Hostname|IPv4|IPv6)#(?P<hash>[0-9a-f]+):(?P<port>\d+)\b"
)
MASK_HASH_RE = re.compile(r"hostname: <mask\.hash: '(?P<hash>[^']+)'>")
URL_HASH_RE = re.compile(r"url hash: (?P<hash>[0-9a-f]+)")
# Network.framework spells connections `[C29 ...]`; CFNetwork spells the same
# thing `Connection 29` or `connection=29`. Both forms normalise to `C29`.
CONNECTION_RE = re.compile(r"\[(?P<conn>C\d+(?:\.\d+)*)[^\]]*\]")
CONNECTION_NUM_RE = re.compile(r"(?:\bConnection |\bconnection=)(?P<num>\d+)\b")
# The single place that knows how this log spells an HTTP status. Both the
# status histogram and the http_error/connection_success evidence derive from
# it, so a new status format is a one-line change here.
STATUS_RE = re.compile(r"(?:httpStatusCode=|response_status=|status )(?P<status>\d{3})\b")


class LogLine(NamedTuple):
    """One physical line of the export, with its header fields resolved."""

    number: int
    level: str
    time: str
    process: str
    message: str

    @property
    def is_continuation(self) -> bool:
        return self.level == CONTINUATION


# ───────────────────────────── signature table ─────────────────────────────
@dataclass(frozen=True)
class Signature:
    """One thing the tool knows how to recognise in a log line.

    kind      stable identifier, used as the key in every report
    pattern   what to look for (always compiled case-insensitively)
    severity  `None` marks a row that only labels the per-connection timeline
              and is never reported as evidence in its own right
    event     label this signature contributes to a connection's timeline
    cause     prose added to "likely causes" when this signature fires
    fallback  a fallback cause is reported only when no primary cause fired
    """

    kind: str
    pattern: re.Pattern[str]
    severity: str | None = None
    event: str | None = None
    cause: str | None = None
    fallback: bool = False


def _signature(
    kind: str,
    regex: str,
    *,
    severity: str | None = None,
    event: str | None = None,
    cause: str | None = None,
    fallback: bool = False,
) -> Signature:
    return Signature(kind, re.compile(regex, re.I), severity, event, cause, fallback)


PEER_RESET_CAUSE = "Remote peer reset an established TLS connection after handshake."

SIGNATURES: tuple[Signature, ...] = (
    _signature(
        "missing_user_details",
        r"Missing user details",
        severity="high",
        cause="App had no user details after login, so poller/sync requests were aborted.",
    ),
    _signature(
        "sqlite_api_violation",
        r"BUG IN CLIENT OF libsqlite3|vnode unlinked while in use",
        severity="high",
        cause=(
            "Local ZenMoney SQLite store was unlinked while in use; "
            "cache/database state is corrupted or mishandled by the app."
        ),
    ),
    _signature(
        "connection_reset",
        r"Connection reset by peer|flags=\[R\.\]",
        severity="medium",
        event="reset_by_peer",
        cause=PEER_RESET_CAUSE,
    ),
    _signature(
        "post_tls_lower_stack_error",
        r"Lower protocol stack error post TLS handshake",
        severity="medium",
        cause=PEER_RESET_CAUSE,
    ),
    _signature(
        "operation_timeout",
        r"Operation timed out|POSIXErrorCode: 60\b",
        severity="medium",
        cause="A connection or read timed out before the peer answered.",
    ),
    _signature(
        "socket_not_connected",
        r"Socket is not connected|unconnected nw_connection",
        severity="medium",
        cause="App/CFNetwork tried to inspect or write to an unconnected/cancelled connection.",
    ),
    _signature(
        "operation_canceled",
        r"Operation canceled|event: flow:failed_connect.*canceled",
        severity="low",
        cause="Connection attempt was cancelled before completion.",
        fallback=True,
    ),
    _signature(
        "data_stall",
        r"client:data_stall|stall recovery",
        severity="low",
        event="data_stall",
        cause="Existing connection stalled, then recovered or retried.",
        fallback=True,
    ),
    _signature(
        "sandbox_deny",
        r"Sandbox: Zenmoney.*deny|System Policy: .*deny",
        severity="low",
    ),
    _signature(
        "tls_success",
        r"TLS connected|Certificate verification result: OK|TLS Trust result 0",
        severity="info",
    ),
    _signature(
        "connection_success",
        r"connected successfully|reporting state ready|TLS handshake complete",
        severity="info",
        event="ready",
    ),
    # Timeline-only rows: too generic to be evidence, useful as connection labels.
    _signature("connection_start", r"\] start$|event: path:start", event="start"),
    _signature(
        "connection_cancelled",
        # `cancel$` is not covered by `cancelled` — it catches `…nw_connection_cancel`.
        r"cancelled|reporting state cancelled|cancel$",
        event="cancelled",
    ),
    # The trailing space in `error ` is load-bearing: it avoids matching the
    # word inside identifiers such as `nw_error_...`.
    _signature("connection_failed", r"failed|error ", event="failed"),
)


# Split once, so neither pass walks rows that cannot apply to it.
EVIDENCE_SIGNATURES = tuple(sig for sig in SIGNATURES if sig.severity)
EVENT_SIGNATURES = tuple(sig for sig in SIGNATURES if sig.event)


class StatusClass(NamedTuple):
    """How one HTTP status maps onto the tool's evidence and event vocabulary."""

    kind: str
    severity: str
    event: str


def classify_status(code: str) -> StatusClass | None:
    """Map an HTTP status code onto a signature kind, or `None` if unremarkable."""
    if not code.isdigit():
        return None
    status = int(code)
    if 400 <= status < 600:
        return StatusClass("http_error", "high", "failed")
    if 200 <= status < 300:
        return StatusClass("connection_success", "info", "ready")
    return None


# ──────────────────────────── collected results ────────────────────────────
@dataclass
class Evidence:
    """A deduplicated signature hit, with how often and where it recurred."""

    kind: str
    severity: str
    count: int
    first_line: int
    last_line: int
    time: str
    process: str
    message: str


@dataclass
class ConnectionInfo:
    endpoints: Counter[str] = field(default_factory=Counter)
    url_hashes: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    events: Counter[str] = field(default_factory=Counter)
    lines: list[int] = field(default_factory=list)


@dataclass
class Analysis:
    path: Path
    lines: int = 0
    levels: Counter[str] = field(default_factory=Counter)
    processes: Counter[str] = field(default_factory=Counter)
    urls: Counter[str] = field(default_factory=Counter)
    endpoint_tokens: Counter[str] = field(default_factory=Counter)
    dns_mask_hashes: Counter[str] = field(default_factory=Counter)
    status_codes: Counter[str] = field(default_factory=Counter)
    # error/fault lines no signature recognised, keyed by level
    unmatched: Counter[str] = field(default_factory=Counter)
    evidence: list[Evidence] = field(default_factory=list)
    # a defaultdict, not a dict: `connections[conn_id]` below creates entries
    connections: defaultdict[str, ConnectionInfo] = field(
        default_factory=lambda: defaultdict(ConnectionInfo)
    )


# ────────────────────────────── parsing helpers ────────────────────────────
def normalize_message(message: str, max_len: int = MESSAGE_MAX_LEN) -> str:
    compact = " ".join(message.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def iter_log_lines(path: Path) -> Iterator[LogLine]:
    """Stream the export, attributing wrapped lines to the header above them."""
    last_time = ""
    last_process = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for number, raw in enumerate(handle, start=1):
            raw = raw.rstrip("\n")
            match = LOG_LINE_RE.match(raw)
            if match:
                last_time = match.group("time")
                last_process = match.group("process")
                yield LogLine(
                    number,
                    match.group("level"),
                    last_time,
                    last_process,
                    match.group("message"),
                )
            else:
                yield LogLine(number, CONTINUATION, last_time, last_process, raw)


def urls(message: str) -> list[str]:
    return [url.rstrip(URL_TRAILING_PUNCTUATION) for url in URL_RE.findall(message)]


def endpoint_tokens(message: str) -> list[str]:
    """`Hostname#ab12:443` tokens — the redacted stand-in for a hostname."""
    return [
        f"{m.group('kind')}#{m.group('hash')}:{m.group('port')}"
        for m in HOST_TOKEN_RE.finditer(message)
    ]


def connection_ids(message: str) -> set[str]:
    """Every connection this line talks about, normalised to `C<n>`."""
    ids = {m.group("conn").split(".")[0] for m in CONNECTION_RE.finditer(message)}
    ids.update(f"C{num}" for num in CONNECTION_NUM_RE.findall(message))
    return ids


# ───────────────────────────────── analysis ────────────────────────────────
def collect_tokens(analysis: Analysis, line: LogLine) -> None:
    """Accumulate the per-file histograms."""
    analysis.lines += 1
    analysis.levels[line.level] += 1
    # Continuation lines inherit the process of the header above them; counting
    # them would inflate the histogram by the wrap rate (~19% on these logs).
    if line.process and not line.is_continuation:
        analysis.processes[line.process] += 1

    analysis.urls.update(urls(line.message))
    analysis.endpoint_tokens.update(endpoint_tokens(line.message))
    analysis.dns_mask_hashes.update(MASK_HASH_RE.findall(line.message))
    analysis.status_codes.update(STATUS_RE.findall(line.message))


def record_connections(analysis: Analysis, line: LogLine) -> None:
    """Attribute this line's endpoints, statuses and events to its connections."""
    conn_ids = connection_ids(line.message)
    if not conn_ids:
        return

    statuses = STATUS_RE.findall(line.message)
    events = {sig.event for sig in EVENT_SIGNATURES if sig.pattern.search(line.message)}
    events.update(
        status.event for status in map(classify_status, statuses) if status is not None
    )

    for conn_id in conn_ids:
        info = analysis.connections[conn_id]
        info.lines.append(line.number)
        info.endpoints.update(endpoint_tokens(line.message))
        info.url_hashes.update(URL_HASH_RE.findall(line.message))
        info.statuses.update(statuses)
        info.events.update(events)


def record_evidence(
    analysis: Analysis, seen: dict[tuple[str, str], Evidence], line: LogLine
) -> None:
    """Record signature hits, collapsing repeats of the same message into a count."""
    hits: dict[str, str] = {
        sig.kind: sig.severity
        for sig in EVIDENCE_SIGNATURES
        if sig.pattern.search(line.message)
    }
    for code in STATUS_RE.findall(line.message):
        status = classify_status(code)
        if status is not None:
            hits.setdefault(status.kind, status.severity)

    if not hits:
        if line.level in UNMATCHED_LEVELS:
            analysis.unmatched[line.level] += 1
        return

    for kind, severity in hits.items():
        key = (kind, normalize_message(line.message, DEDUP_KEY_LEN))
        existing = seen.get(key)
        if existing is not None:
            existing.count += 1
            existing.last_line = line.number
            continue
        evidence = Evidence(
            kind=kind,
            severity=severity,
            count=1,
            first_line=line.number,
            last_line=line.number,
            time=line.time,
            process=line.process,
            message=normalize_message(line.message),
        )
        seen[key] = evidence
        analysis.evidence.append(evidence)


def analyze_file(path: Path) -> Analysis:
    analysis = Analysis(path=path)
    seen: dict[tuple[str, str], Evidence] = {}
    for line in iter_log_lines(path):
        collect_tokens(analysis, line)
        record_connections(analysis, line)
        record_evidence(analysis, seen, line)
    return analysis


def evidence_counts(analysis: Analysis) -> Counter[str]:
    """Occurrences per signature kind — not distinct messages."""
    counts: Counter[str] = Counter()
    for item in analysis.evidence:
        counts[item.kind] += item.count
    return counts


def likely_causes(analysis: Analysis) -> list[str]:
    """Turn the signatures that fired into prose, primary causes first."""
    fired = {item.kind for item in analysis.evidence}
    statuses = {int(code) for code in analysis.status_codes if code.isdigit()}

    primary: list[str] = []
    fallback: list[str] = []
    if any(code >= 500 for code in statuses):
        primary.append("Server returned HTTP 5xx during login/API flow.")
    if any(400 <= code < 500 for code in statuses):
        primary.append("Server returned HTTP 4xx; request/session may be invalid.")
    for sig in SIGNATURES:
        if sig.cause and sig.kind in fired:
            (fallback if sig.fallback else primary).append(sig.cause)

    causes = list(dict.fromkeys(primary or fallback))
    if causes:
        return causes
    if fired & {"tls_success", "connection_success"}:
        return ["No primary network failure detected; TLS and HTTP responses mostly succeeded."]
    return ["No known failure signature detected by this script."]


def severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return UNKNOWN_SEVERITY_RANK


def sorted_evidence(analysis: Analysis) -> list[Evidence]:
    """Most severe first, then in the order the signatures first appeared."""
    return sorted(analysis.evidence, key=lambda item: (severity_rank(item.severity), item.first_line))


def connection_sort_key(conn_id: str) -> tuple[int, str]:
    """Numeric order, so C10 follows C9 instead of C1."""
    digits = conn_id[1:]
    return (int(digits) if digits.isdigit() else sys.maxsize, conn_id)


# ────────────────────────────────── output ─────────────────────────────────
def to_dict(analysis: Analysis, top_n: int = DEFAULT_TOP_N) -> dict[str, object]:
    return {
        "file": str(analysis.path),
        "lines": analysis.lines,
        "levels": dict(analysis.levels.most_common()),
        "processes": dict(analysis.processes.most_common(top_n)),
        "urls": dict(analysis.urls.most_common()),
        "endpoint_tokens": dict(analysis.endpoint_tokens.most_common()),
        "dns_mask_hashes": dict(analysis.dns_mask_hashes.most_common()),
        "status_codes": dict(analysis.status_codes.most_common()),
        "unmatched_error_lines": dict(analysis.unmatched.most_common()),
        "evidence_counts": dict(evidence_counts(analysis).most_common()),
        "likely_causes": likely_causes(analysis),
        "evidence": [asdict(item) for item in sorted_evidence(analysis)],
        "connections": {
            conn_id: {
                "lines": [min(info.lines), max(info.lines)] if info.lines else [],
                "endpoints": dict(info.endpoints.most_common()),
                "url_hashes": dict(info.url_hashes.most_common()),
                "statuses": dict(info.statuses.most_common()),
                "events": dict(info.events.most_common()),
            }
            for conn_id, info in sorted(
                analysis.connections.items(), key=lambda kv: connection_sort_key(kv[0])
            )
        },
    }


def counter_line(
    label: str,
    counter: Counter[str],
    *,
    limit: int | None = None,
    fmt: str = "{key}={value}",
    empty: str = "none",
) -> str:
    items = counter.most_common(limit) if limit else counter.most_common()
    if not items:
        return f"- {label}: {empty}"
    return f"- {label}: " + ", ".join(fmt.format(key=key, value=value) for key, value in items)


def render_markdown(
    analyses: list[Analysis],
    top_n: int = DEFAULT_TOP_N,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
) -> str:
    chunks: list[str] = []
    for analysis in analyses:
        chunks.append(f"## {analysis.path.name}")
        chunks.append(f"- Lines: {analysis.lines}")
        chunks.append(counter_line("Levels", analysis.levels))
        chunks.append(counter_line("HTTP/status codes", analysis.status_codes, empty="none detected"))
        chunks.append(
            counter_line(
                "Literal URLs",
                analysis.urls,
                fmt="{key}",
                empty="none; Apple log privacy redacted hostnames",
            )
        )
        if analysis.endpoint_tokens:
            chunks.append(
                counter_line(
                    "Endpoint tokens", analysis.endpoint_tokens, limit=8, fmt="{key} ({value})"
                )
            )
        if analysis.dns_mask_hashes:
            chunks.append(
                counter_line(
                    "DNS mask hashes", analysis.dns_mask_hashes, limit=5, fmt="{key} ({value})"
                )
            )
        if analysis.unmatched:
            chunks.append(
                counter_line(
                    "Unclassified error/fault lines (no signature matched)", analysis.unmatched
                )
            )
        chunks.append("- Likely causes:")
        for cause in likely_causes(analysis):
            chunks.append(f"  - {cause}")

        noteworthy = [item for item in sorted_evidence(analysis) if item.severity != "info"]
        chunks.append("- Noteworthy evidence:")
        if not noteworthy:
            chunks.append("  - none")
        for item in noteworthy[:max_evidence]:
            where = f"line {item.first_line}"
            if item.count > 1:
                where = f"x{item.count}, lines {item.first_line}-{item.last_line}"
            chunks.append(
                f"  - {item.severity} {item.kind} {where} {item.time} "
                f"{item.process}: {item.message}"
            )
        if len(noteworthy) > max_evidence:
            hidden = len(noteworthy) - max_evidence
            chunks.append(f"  - ... {hidden} more (raise --max-evidence or use --json)")
        chunks.append("")

    return "\n".join(chunks).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect ZenMoney iOS connection/login failures in redacted Apple logs."
    )
    parser.add_argument("logs", nargs="+", type=Path, help="Apple Console export(s) to analyse")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        metavar="N",
        help=f"entries kept per counter (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--max-evidence",
        type=int,
        default=DEFAULT_MAX_EVIDENCE,
        metavar="N",
        help=f"noteworthy rows printed per file (default: {DEFAULT_MAX_EVIDENCE})",
    )
    args = parser.parse_args()

    missing = [str(path) for path in args.logs if not path.is_file()]
    if missing:
        parser.error("log file(s) not found: " + ", ".join(missing))

    analyses = [analyze_file(path) for path in args.logs]
    if args.json:
        payload = [to_dict(analysis, args.top) for analysis in analyses]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(analyses, args.top, args.max_evidence), end="")


if __name__ == "__main__":
    main()
