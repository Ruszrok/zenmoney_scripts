#!/usr/bin/env python3
"""Detect ZenMoney iOS connection/login failures in redacted Apple logs."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


LOG_LINE_RE = re.compile(
    r"^(?P<level>\w+)\t(?P<time>\d\d:\d\d:\d\d\.\d+[+-]\d{4})\t"
    r"(?P<process>[^\t]+)\t(?P<message>.*)$"
)
URL_RE = re.compile(r"https?://[^\s,)>}\]]+")
HOST_TOKEN_RE = re.compile(r"\b(?P<kind>Hostname|IPv4)#(?P<hash>[0-9a-f]+):(?P<port>\d+)\b")
MASK_HASH_RE = re.compile(r"hostname: <mask\.hash: '(?P<hash>[^']+)'>")
CONNECTION_RE = re.compile(r"\[(?P<conn>C\d+(?:\.\d+)*)[^\]]*\]")
URL_HASH_RE = re.compile(r"url hash: (?P<hash>[0-9a-f]+)")
STATUS_RE = re.compile(
    r"(?:httpStatusCode=|response_status=|received response, status |status )(?P<status>\d{3})"
)
TASK_RE = re.compile(r"Task <(?P<task>[^>]+)>\.<(?P<num>\d+)>")


@dataclass
class Evidence:
    kind: str
    severity: str
    line: int
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
    file: str
    lines: int = 0
    levels: Counter[str] = field(default_factory=Counter)
    processes: Counter[str] = field(default_factory=Counter)
    urls: Counter[str] = field(default_factory=Counter)
    endpoint_tokens: Counter[str] = field(default_factory=Counter)
    dns_mask_hashes: Counter[str] = field(default_factory=Counter)
    status_codes: Counter[str] = field(default_factory=Counter)
    evidence: list[Evidence] = field(default_factory=list)
    connections: dict[str, ConnectionInfo] = field(default_factory=lambda: defaultdict(ConnectionInfo))


CLASSIFIERS: list[tuple[str, str, re.Pattern[str]]] = [
    ("http_error", "high", re.compile(r"httpStatusCode=[45]\d\d|response_status=[45]\d\d|status [45]\d\d", re.I)),
    ("missing_user_details", "high", re.compile(r"Missing user details", re.I)),
    ("sqlite_api_violation", "high", re.compile(r"BUG IN CLIENT OF libsqlite3|vnode unlinked while in use", re.I)),
    ("connection_reset", "medium", re.compile(r"Connection reset by peer|flags=\[R\.\]", re.I)),
    ("post_tls_lower_stack_error", "medium", re.compile(r"Lower protocol stack error post TLS handshake", re.I)),
    ("socket_not_connected", "medium", re.compile(r"Socket is not connected|unconnected nw_connection", re.I)),
    ("operation_canceled", "low", re.compile(r"Operation canceled|event: flow:failed_connect.*canceled", re.I)),
    ("data_stall", "low", re.compile(r"client:data_stall|stall recovery", re.I)),
    ("sandbox_deny", "low", re.compile(r"Sandbox: Zenmoney.*deny|System Policy: .*deny", re.I)),
    ("tls_success", "info", re.compile(r"TLS connected|Certificate verification result: OK|TLS Trust result 0", re.I)),
    ("connection_success", "info", re.compile(r"connected successfully|reporting state ready|response_status=200|received response, status 200", re.I)),
]


def normalize_message(message: str, max_len: int = 260) -> str:
    compact = " ".join(message.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def iter_log_lines(path: Path) -> Iterable[tuple[int, str, str, str, str]]:
    last_time = ""
    last_process = ""
    for line_no, raw in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        match = LOG_LINE_RE.match(raw)
        if match:
            level = match.group("level")
            time = match.group("time")
            process = match.group("process")
            message = match.group("message")
            last_time = time
            last_process = process
            yield line_no, level, time, process, message
        else:
            yield line_no, "continuation", last_time, last_process, raw


def connection_ids(message: str) -> set[str]:
    ids = {match.group("conn").split(".")[0] for match in CONNECTION_RE.finditer(message)}
    extra = re.findall(r"\bConnection (?P<num>\d+)\b", message)
    ids.update(f"C{num}" for num in extra)
    return ids


def add_connection_detail(analysis: Analysis, conn_ids: set[str], line_no: int, message: str) -> None:
    if not conn_ids:
        return

    endpoint_tokens = [f"{m.group('kind')}#{m.group('hash')}:{m.group('port')}" for m in HOST_TOKEN_RE.finditer(message)]
    url_hashes = [m.group("hash") for m in URL_HASH_RE.finditer(message)]
    statuses = [m.group("status") for m in STATUS_RE.finditer(message)]

    events: list[str] = []
    for label, pattern in (
        ("start", r"\] start$|event: path:start"),
        ("ready", r"connected successfully|reporting state ready|TLS handshake complete|response_status=200|received response, status 200"),
        ("cancelled", r"cancelled|reporting state cancelled|cancel$"),
        ("failed", r"failed|error "),
        ("reset_by_peer", r"Connection reset by peer|flags=\[R\.\]"),
        ("data_stall", r"client:data_stall|stall recovery"),
    ):
        if re.search(pattern, message, re.I):
            events.append(label)

    for conn_id in conn_ids:
        info = analysis.connections[conn_id]
        info.lines.append(line_no)
        for endpoint in endpoint_tokens:
            info.endpoints[endpoint] += 1
        for url_hash in url_hashes:
            info.url_hashes[url_hash] += 1
        for status in statuses:
            info.statuses[status] += 1
        for event in events:
            info.events[event] += 1


def analyze_file(path: Path) -> Analysis:
    analysis = Analysis(file=str(path))
    seen_evidence: set[tuple[str, str]] = set()

    for line_no, level, time, process, message in iter_log_lines(path):
        analysis.lines += 1
        analysis.levels[level] += 1
        if process:
            analysis.processes[process] += 1

        for url in URL_RE.findall(message):
            analysis.urls[url] += 1
        for token in HOST_TOKEN_RE.finditer(message):
            analysis.endpoint_tokens[f"{token.group('kind')}#{token.group('hash')}:{token.group('port')}"] += 1
        for dns_hash in MASK_HASH_RE.finditer(message):
            analysis.dns_mask_hashes[dns_hash.group("hash")] += 1
        for status in STATUS_RE.finditer(message):
            analysis.status_codes[status.group("status")] += 1

        conn_ids = connection_ids(message)
        task = TASK_RE.search(message)
        if task and re.search(r"connection=(\d+)|now using Connection \d+|done using Connection \d+", message):
            for conn_num in re.findall(r"Connection (\d+)|connection=(\d+)", message):
                num = conn_num[0] or conn_num[1]
                if num:
                    conn_ids.add(f"C{num}")
        add_connection_detail(analysis, conn_ids, line_no, message)

        for kind, severity, pattern in CLASSIFIERS:
            if not pattern.search(message):
                continue
            key = (kind, normalize_message(message, 120))
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            analysis.evidence.append(
                Evidence(
                    kind=kind,
                    severity=severity,
                    line=line_no,
                    time=time,
                    process=process,
                    message=normalize_message(message),
                )
            )

    return analysis


def evidence_counts(analysis: Analysis) -> Counter[str]:
    return Counter(e.kind for e in analysis.evidence)


def likely_causes(analysis: Analysis) -> list[str]:
    counts = evidence_counts(analysis)
    statuses = {int(code): count for code, count in analysis.status_codes.items() if code.isdigit()}
    causes: list[str] = []

    if any(code >= 500 for code in statuses):
        causes.append("Server returned HTTP 5xx during login/API flow.")
    if any(400 <= code < 500 for code in statuses):
        causes.append("Server returned HTTP 4xx; request/session may be invalid.")
    if counts["missing_user_details"]:
        causes.append("App had no user details after login, so poller/sync requests were aborted.")
    if counts["sqlite_api_violation"]:
        causes.append("Local ZenMoney SQLite store was unlinked while in use; cache/database state is corrupted or mishandled by the app.")
    if counts["connection_reset"] or counts["post_tls_lower_stack_error"]:
        causes.append("Remote peer reset an established TLS connection after handshake.")
    if counts["socket_not_connected"]:
        causes.append("App/CFNetwork tried to inspect or write to an unconnected/cancelled connection.")
    if counts["operation_canceled"] and not causes:
        causes.append("Connection attempt was cancelled before completion.")
    if counts["data_stall"] and not causes:
        causes.append("Existing connection stalled, then recovered or retried.")
    if not causes and (counts["tls_success"] or counts["connection_success"]):
        causes.append("No primary network failure detected; TLS and HTTP responses mostly succeeded.")
    if not causes:
        causes.append("No known failure signature detected by this script.")

    return causes


def severity_rank(severity: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "info": 3}.get(severity, 4)


def to_dict(analysis: Analysis) -> dict:
    return {
        "file": analysis.file,
        "lines": analysis.lines,
        "levels": dict(analysis.levels.most_common()),
        "processes": dict(analysis.processes.most_common(15)),
        "urls": dict(analysis.urls.most_common()),
        "endpoint_tokens": dict(analysis.endpoint_tokens.most_common()),
        "dns_mask_hashes": dict(analysis.dns_mask_hashes.most_common()),
        "status_codes": dict(analysis.status_codes.most_common()),
        "evidence_counts": dict(evidence_counts(analysis).most_common()),
        "likely_causes": likely_causes(analysis),
        "evidence": [
            {
                "kind": e.kind,
                "severity": e.severity,
                "line": e.line,
                "time": e.time,
                "process": e.process,
                "message": e.message,
            }
            for e in sorted(analysis.evidence, key=lambda item: (severity_rank(item.severity), item.line))
        ],
        "connections": {
            conn_id: {
                "lines": [min(info.lines), max(info.lines)] if info.lines else [],
                "endpoints": dict(info.endpoints.most_common()),
                "url_hashes": dict(info.url_hashes.most_common()),
                "statuses": dict(info.statuses.most_common()),
                "events": dict(info.events.most_common()),
            }
            for conn_id, info in sorted(analysis.connections.items())
        },
    }


def render_markdown(analyses: list[Analysis]) -> str:
    chunks: list[str] = []
    for analysis in analyses:
        data = to_dict(analysis)
        chunks.append(f"## {Path(analysis.file).name}")
        chunks.append(f"- Lines: {analysis.lines}")
        chunks.append(f"- Levels: {', '.join(f'{k}={v}' for k, v in analysis.levels.most_common()) or 'none'}")
        if analysis.status_codes:
            chunks.append(f"- HTTP/status codes: {', '.join(f'{k}={v}' for k, v in analysis.status_codes.most_common())}")
        else:
            chunks.append("- HTTP/status codes: none detected")
        if analysis.urls:
            chunks.append(f"- Literal URLs: {', '.join(analysis.urls.keys())}")
        else:
            chunks.append("- Literal URLs: none; Apple log privacy redacted hostnames")
        if analysis.endpoint_tokens:
            endpoints = ", ".join(f"{token} ({count})" for token, count in analysis.endpoint_tokens.most_common(8))
            chunks.append(f"- Endpoint tokens: {endpoints}")
        if analysis.dns_mask_hashes:
            dns = ", ".join(f"{token} ({count})" for token, count in analysis.dns_mask_hashes.most_common(5))
            chunks.append(f"- DNS mask hashes: {dns}")
        chunks.append("- Likely causes:")
        for cause in data["likely_causes"]:
            chunks.append(f"  - {cause}")

        noteworthy = [
            e
            for e in sorted(analysis.evidence, key=lambda item: (severity_rank(item.severity), item.line))
            if e.severity != "info"
        ][:18]
        chunks.append("- Noteworthy evidence:")
        if noteworthy:
            for e in noteworthy:
                chunks.append(f"  - {e.severity} {e.kind} line {e.line} {e.time} {e.process}: {e.message}")
        else:
            chunks.append("  - none")
        chunks.append("")

    return "\n".join(chunks).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    analyses = [analyze_file(path) for path in args.logs]
    if args.json:
        print(json.dumps([to_dict(analysis) for analysis in analyses], indent=2, ensure_ascii=False))
    else:
        print(render_markdown(analyses), end="")


if __name__ == "__main__":
    main()
