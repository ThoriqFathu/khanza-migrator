import argparse
import sys
from pathlib import Path

from app.shared.hashing.sha256 import sha256_file
from app.shared.sql.parser import SqlMigrationParser


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="khanza-migrator",
        description="Khanza Migrator CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_hash = sub.add_parser("hash", help="Hitung SHA-256 migration")
    p_hash.add_argument("--migration", required=True)

    p_parse = sub.add_parser("parse", help="Parse migration menjadi statement")
    p_parse.add_argument("--migration", required=True)

    args = parser.parse_args()

    if args.command == "hash":
        path = Path(args.migration)
        if not path.is_file():
            print(f"File tidak ditemukan: {path}", file=sys.stderr)
            return 1
        print(sha256_file(path))
        return 0

    if args.command == "parse":
        path = Path(args.migration)
        if not path.is_file():
            print(f"File tidak ditemukan: {path}", file=sys.stderr)
            return 1
        statements = SqlMigrationParser().parse_file(path)
        for statement in statements:
            print(f"#{statement.sequence:03d} [{statement.type}]")
            print(statement.sql)
            print()
        print(f"Total: {len(statements)} statement")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
