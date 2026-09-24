"""Read-only mysql CLI adapter for TEST preflight queries."""
import subprocess

from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.domain.preflight import ForeignKey
from .mysql_client import MySqlClient


def quote_identifier(value: str) -> str:
    if not value or '\x00' in value:
        raise ValueError("Identifier kosong atau mengandung NUL")
    return '`' + value.replace('`', '``') + '`'


def _literal(value: str) -> str:
    # Hex avoids SQL mode dependent backslash/string escaping.
    return "CONVERT(X'" + value.encode('utf-8').hex() + "' USING utf8mb4)"


class MySqlPreflightReader:
    def __init__(self, client: MySqlClient) -> None:
        self.client = client

    @staticmethod
    def _require_test(config: DatabaseConfig) -> None:
        if config.environment is not Environment.TEST or not config.database:
            raise ValueError("Preflight hanya untuk database TEST")

    def test_connection(self, config: DatabaseConfig, password: str) -> None:
        self._require_test(config)
        self.client.test_connection(config, password)

    def _read(self, config: DatabaseConfig, password: str, sql: str) -> str:
        self._require_test(config)
        result = subprocess.run(
            self.client._base(config) + ['--database', config.database, '--batch',
                '--raw', '--binary-mode', '--skip-column-names', '--default-character-set=utf8mb4', '--execute', sql],
            env=self.client._env(password), capture_output=True, text=True, encoding='utf-8',
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Query preflight gagal")
        return result.stdout.strip()

    def count_orphans(self, config: DatabaseConfig, password: str, foreign_key: ForeignKey) -> int:
        self._require_test(config)
        fk = foreign_key
        if any(s and s != config.database for s in (fk.child_schema, fk.parent_schema)):
            raise ValueError("Preflight tidak melakukan query ke schema lain")
        if not fk.child_columns or len(fk.child_columns) != len(fk.parent_columns):
            raise ValueError("Kolom FK tidak valid")
        schema = quote_identifier(config.database)
        nonnull = ' AND '.join(f'c.{quote_identifier(c)} IS NOT NULL' for c in fk.child_columns)
        matches = ' AND '.join(f'p.{quote_identifier(p)} = c.{quote_identifier(c)}'
                               for c, p in zip(fk.child_columns, fk.parent_columns))
        sql = (f'SELECT COUNT(*) FROM {schema}.{quote_identifier(fk.child_table)} AS c '
               f'WHERE {nonnull} AND NOT EXISTS (SELECT 1 FROM '
               f'{schema}.{quote_identifier(fk.parent_table)} AS p WHERE {matches})')
        count = int(self._read(config, password, sql))
        if count < 0:
            raise ValueError("Orphan count negatif")
        return count

    def get_foreign_keys(self, config: DatabaseConfig, password: str,
                         table: str, column: str) -> list[ForeignKey]:
        self._require_test(config)
        schema, table_literal = _literal(config.database), _literal(table)
        # Fetch all components of each composite FK, including incoming references.
        # HEX keeps tabs/newlines/backticks in identifiers unambiguous in CLI output.
        sql = (
            'SELECT HEX(TABLE_SCHEMA), HEX(TABLE_NAME), HEX(CONSTRAINT_NAME), '
            'HEX(COLUMN_NAME), HEX(REFERENCED_TABLE_SCHEMA), HEX(REFERENCED_TABLE_NAME), '
            'HEX(REFERENCED_COLUMN_NAME), ORDINAL_POSITION '
            'FROM information_schema.KEY_COLUMN_USAGE '
            'WHERE REFERENCED_TABLE_NAME IS NOT NULL AND '
            f'((TABLE_SCHEMA = {schema} AND TABLE_NAME = {table_literal}) OR '
            f'(REFERENCED_TABLE_SCHEMA = {schema} AND REFERENCED_TABLE_NAME = {table_literal})) '
            'ORDER BY TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION'
        )
        grouped: dict[tuple[str, ...], list[tuple[int, str, str]]] = {}
        for line in self._read(config, password, sql).splitlines():
            parts = line.split('\t')
            if len(parts) != 8:
                raise ValueError("Metadata FK tidak valid")
            child_schema, child, name, child_col, parent_schema, parent, parent_col = (
                bytes.fromhex(p).decode('utf-8') for p in parts[:7])
            grouped.setdefault((child_schema, child, name, parent_schema, parent), []).append(
                (int(parts[7]), child_col, parent_col))
        keys = []
        for (child_schema, child, name, parent_schema, parent), components in grouped.items():
            components.sort()
            if [x[0] for x in components] != list(range(1, len(components) + 1)):
                raise ValueError("Urutan metadata composite FK tidak lengkap")
            children, parents = tuple(x[1] for x in components), tuple(x[2] for x in components)
            if ((child_schema == config.database and child.casefold() == table.casefold() and column.casefold() in [c.casefold() for c in children])
                    or (parent_schema == config.database and parent.casefold() == table.casefold() and column.casefold() in [c.casefold() for c in parents])):
                keys.append(ForeignKey(name, child, children, parent, parents, child_schema, parent_schema))
        return keys
