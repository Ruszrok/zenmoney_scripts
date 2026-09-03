"""Command-line entry point for the finance warehouse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import accounts, db, fx, ingest, report, verify


def main(argv: list[str] | None = None) -> int:
    # `--db` lives on this shared parent parser, not the top-level parser, so
    # it is accepted *after* the subcommand (`finance init --db X`), matching
    # every documented invocation. Every subparser added here or in a later
    # task MUST include `parents=[common]` or it will silently reject `--db`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db", type=Path, default=db.DEFAULT_DB_PATH, help="database path"
    )

    parser = argparse.ArgumentParser(prog="finance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "init", parents=[common], help="create the database and apply the schema"
    )

    ingest_cmd = sub.add_parser(
        "ingest", parents=[common], help="load CSV dumps into the warehouse"
    )
    ingest_cmd.add_argument(
        "--from", dest="folder", type=Path, default=ingest.DEFAULT_DUMP_DIR
    )
    ingest_cmd.add_argument("--file", dest="files", type=Path, action="append")

    accounts_cmd = sub.add_parser(
        "accounts", parents=[common], help="manage account classification"
    )
    accounts_cmd.add_argument("--seed", action="store_true")
    accounts_cmd.add_argument("--apply", action="store_true")
    accounts_cmd.add_argument("--path", type=Path, default=Path("accounts.toml"))

    fx_cmd = sub.add_parser(
        "fx", parents=[common], help="refresh exchange rates and EUR amounts"
    )
    fx_cmd.add_argument("--refresh", action="store_true")

    sub.add_parser(
        "verify", parents=[common], help="report coverage and FX precision"
    )
    query_cmd = sub.add_parser(
        "query", parents=[common], help="run ad-hoc SQL against the warehouse"
    )
    query_cmd.add_argument("sql")

    report_cmd = sub.add_parser(
        "report", parents=[common], help="write the advisory report"
    )
    report_cmd.add_argument("--months", type=int, default=report.DEFAULT_MONTHS)
    report_cmd.add_argument("--json", action="store_true")
    report_cmd.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command == "init":
        conn = db.connect(args.db)
        db.migrate(conn)
        print(f"initialised {args.db}")
        return 0
    if args.command == "ingest":
        conn = db.connect(args.db)
        db.migrate(conn)
        paths = args.files or ingest.discover(args.folder)
        if not paths:
            print(f"no CSV files found in {args.folder}")
            return 1
        result = ingest.ingest_paths(conn, paths)
        print(
            f"seen={result.rows_seen} new={result.rows_new} "
            f"updated={result.rows_updated} deleted={result.rows_deleted}"
        )
        return 0
    if args.command == "accounts":
        conn = db.connect(args.db)
        db.migrate(conn)
        if args.seed:
            args.path.write_text(accounts.seed_toml(conn), encoding="utf-8")
            print(f"wrote {args.path}")
        if args.apply:
            count = accounts.apply_toml(
                conn, args.path.read_text(encoding="utf-8")
            )
            print(f"applied {count} account(s)")
        return 0
    if args.command == "fx":
        conn = db.connect(args.db)
        db.migrate(conn)
        if not args.refresh:
            parser.error("finance fx requires --refresh")
        counts = fx.refresh(conn)
        for key, value in counts.items():
            print(f"{key}={value}")
        return 0
    if args.command == "verify":
        conn = db.connect(args.db)
        db.migrate(conn)
        cov = verify.coverage(conn)
        print(f"months: {cov.months} ({cov.first_month} → {cov.last_month})")
        print(f"missing: {', '.join(cov.missing) if cov.missing else 'none'}")
        for source, share in sorted(
            verify.fx_precision(conn).items(), key=lambda kv: -kv[1]
        ):
            print(f"  fx {source}: {share:.1%}")
        return 0
    if args.command == "query":
        conn = db.connect(args.db)
        for row in conn.execute(args.sql):
            print("\t".join("" if v is None else str(v) for v in tuple(row)))
        return 0
    if args.command == "report":
        conn = db.connect(args.db)
        db.migrate(conn)
        payload = report.build(conn, months=args.months)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        text = report.to_markdown(payload)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0
    return 1
