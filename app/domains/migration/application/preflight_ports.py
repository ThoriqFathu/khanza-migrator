from typing import Protocol

from app.domains.migration.domain.models import DatabaseConfig, MigrationStatement
from app.domains.migration.domain.preflight import ForeignKey, PreflightOperation


class PreflightDatabasePort(Protocol):
    """Only read operations on TEST; no migration SQL execution capability."""

    def test_connection(self, config: DatabaseConfig, password: str) -> None: ...

    def count_orphans(self, config: DatabaseConfig, password: str, foreign_key: ForeignKey) -> int: ...

    def get_foreign_keys(self, config: DatabaseConfig, password: str,
                         table: str, column: str) -> list[ForeignKey]: ...


class PreflightExtractorPort(Protocol):
    def extract(self, statements: list[MigrationStatement]) -> list[PreflightOperation]: ...
