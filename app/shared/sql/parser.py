from pathlib import Path
import re

from app.domains.migration.domain.enums import StatementType
from app.domains.migration.domain.models import MigrationStatement


class SqlMigrationParser:
    """
    Parser SQL ringan yang mempertahankan boundary parser terpisah dari executor.

    Tidak melakukan semantic SQL parsing. Tujuannya memecah statement dengan aman
    terhadap quote/comment dan DELIMITER dasar yang umum pada dump/migration MySQL.
    """

    def parse_file(self, path: Path) -> list[MigrationStatement]:
        text = path.read_text(encoding="utf-8-sig")
        return self.parse(text)

    def parse(self, text: str) -> list[MigrationStatement]:
        delimiter = ";"
        buffer: list[str] = []
        statements: list[str] = []
        i = 0
        n = len(text)
        quote: str | None = None
        line_comment = False
        block_comment = False

        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""

            if line_comment:
                buffer.append(ch)
                if ch == "\n":
                    line_comment = False
                i += 1
                continue

            if block_comment:
                buffer.append(ch)
                if ch == "*" and nxt == "/":
                    buffer.append(nxt)
                    i += 2
                    block_comment = False
                else:
                    i += 1
                continue

            if quote:
                buffer.append(ch)
                if ch == "\\" and i + 1 < n:
                    buffer.append(text[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue

            if ch in ("'", '"', "`"):
                quote = ch
                buffer.append(ch)
                i += 1
                continue

            if ch == "#":
                line_comment = True
                buffer.append(ch)
                i += 1
                continue

            if ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2].isspace()):
                line_comment = True
                buffer.extend([ch, nxt])
                i += 2
                continue

            if ch == "/" and nxt == "*":
                block_comment = True
                buffer.extend([ch, nxt])
                i += 2
                continue

            if ch == "\n":
                line = "".join(buffer).strip()
                delimiter_match = re.match(r"^DELIMITER\s+(.+)$", line, re.I)
                if delimiter_match:
                    buffer.clear()
                    delimiter = delimiter_match.group(1).strip()
                    i += 1
                    continue

            if text.startswith(delimiter, i):
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer.clear()
                i += len(delimiter)
                continue

            buffer.append(ch)
            i += 1

        tail = "".join(buffer).strip()
        if tail:
            statements.append(tail)

        result: list[MigrationStatement] = []
        for seq, sql in enumerate(statements, start=1):
            cleaned = self._strip_leading_comments(sql).strip()
            if not cleaned:
                continue
            result.append(
                MigrationStatement(
                    sequence=seq,
                    sql=cleaned,
                    type=self._detect_type(cleaned),
                    description=self._description(cleaned),
                )
            )
        return result

    @staticmethod
    def _strip_leading_comments(sql: str) -> str:
        previous = None
        while previous != sql:
            previous = sql
            sql = re.sub(r"^\s*(?:--[^\n]*\n|#[^\n]*\n|/\*.*?\*/\s*)", "", sql, flags=re.S)
        return sql

    @staticmethod
    def _detect_type(sql: str) -> StatementType:
        normalized = re.sub(r"\s+", " ", sql.strip().upper())
        if normalized.startswith("ALTER TABLE"):
            if " ADD CONSTRAINT " in f" {normalized} ":
                return StatementType.CREATE_CONSTRAINT
            if " DROP CONSTRAINT " in f" {normalized} ":
                return StatementType.DROP_CONSTRAINT
            if " ADD INDEX " in f" {normalized} " or " ADD KEY " in f" {normalized} ":
                return StatementType.CREATE_INDEX
            if " DROP INDEX " in f" {normalized} ":
                return StatementType.DROP_INDEX
            return StatementType.ALTER_TABLE
        if normalized.startswith("CREATE TABLE"):
            return StatementType.CREATE_TABLE
        if normalized.startswith("DROP TABLE"):
            return StatementType.DROP_TABLE
        if normalized.startswith("CREATE INDEX") or normalized.startswith("CREATE UNIQUE INDEX"):
            return StatementType.CREATE_INDEX
        if normalized.startswith("DROP INDEX"):
            return StatementType.DROP_INDEX
        if normalized.startswith("CREATE VIEW"):
            return StatementType.CREATE_VIEW
        if normalized.startswith("DROP VIEW"):
            return StatementType.DROP_VIEW
        if normalized.startswith("INSERT"):
            return StatementType.INSERT
        if normalized.startswith("UPDATE"):
            return StatementType.UPDATE
        if normalized.startswith("DELETE"):
            return StatementType.DELETE
        return StatementType.OTHER

    @staticmethod
    def _description(sql: str) -> str:
        match = re.search(r"\b(?:ALTER|CREATE|DROP)\s+(?:TABLE|INDEX|VIEW)?\s*`?([A-Za-z0-9_$-]+)", sql, re.I)
        return match.group(1) if match else ""
