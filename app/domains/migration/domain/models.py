from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import Environment, MigrationStatus, StatementType


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    username: str
    environment: Environment


@dataclass(frozen=True)
class MigrationStatement:
    sequence: int
    sql: str
    type: StatementType
    description: str = ""


@dataclass
class StatementResult:
    sequence: int
    sql: str
    success: bool
    duration_ms: float
    error_code: int | None = None
    error_message: str | None = None


@dataclass
class MigrationExecutionResult:
    status: MigrationStatus
    migration_hash: str
    database: str
    started_at: datetime
    finished_at: datetime
    statements: list[StatementResult] = field(default_factory=list)
    foreign_key_checks: bool = True

    @property
    def success_count(self) -> int:
        return sum(1 for x in self.statements if x.success)

    @property
    def failed_count(self) -> int:
        return sum(1 for x in self.statements if not x.success)

    @property
    def failed_statement(self) -> StatementResult | None:
        return next((x for x in self.statements if not x.success), None)


@dataclass(frozen=True)
class Approval:
    migration_name: str
    migration_hash: str
    test_database: str
    test_backup: str
    success_count: int
    failed_count: int
    approved_at: datetime
    application_version: str


@dataclass(frozen=True)
class BackupMetadata:
    database: str
    host: str
    created_at: datetime
    backup_file: str
    file_size: int
    backup_sha256: str
    migration_sha256: str
    application_version: str


@dataclass(frozen=True)
class SafetyCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class SafetyReport:
    checks: list[SafetyCheck]

    @property
    def passed(self) -> bool:
        return all(x.passed for x in self.checks)
