"""Command-line entry point for the finance warehouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db, ingest


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
    return 1
