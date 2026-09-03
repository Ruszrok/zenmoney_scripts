"""Command-line entry point for the finance warehouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db


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

    args = parser.parse_args(argv)
    if args.command == "init":
        conn = db.connect(args.db)
        db.migrate(conn)
        print(f"initialised {args.db}")
        return 0
    return 1
