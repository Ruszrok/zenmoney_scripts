"""Command-line entry point for the finance warehouse."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finance")
    parser.add_argument(
        "--db", type=Path, default=db.DEFAULT_DB_PATH, help="database path"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the database and apply the schema")

    args = parser.parse_args(argv)
    if args.command == "init":
        conn = db.connect(args.db)
        db.migrate(conn)
        print(f"initialised {args.db}")
        return 0
    return 1
