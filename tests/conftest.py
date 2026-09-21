"""Data baseline; tidak mengimpor Qt atau adapter database sungguhan."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, create_autospec

import pytest

from app.domains.migration.application.ports import DatabasePort, HistoryPort, MigrationParserPort
from app.domains.migration.domain.enums import Environment, MigrationStatus, StatementType
from app.domains.migration.domain.models import (
    Approval, BackupMetadata, DatabaseConfig, MigrationExecutionResult,
    MigrationStatement, StatementResult,
)


@pytest.fixture
def migration_file(tmp_path: Path) -> Path:
    path = tmp_path / "migration.sql"
    path.write_bytes(b"SELECT 1;\nSELECT 2;\nSELECT 3;\n")
    return path


@pytest.fixture
def target() -> DatabaseConfig:
    return DatabaseConfig("db.invalid", 3306, "baseline_db", "tester", Environment.PRODUCTION)


@pytest.fixture
def execution(migration_file: Path) -> MigrationExecutionResult:
    return MigrationExecutionResult(
        status=MigrationStatus.SUCCESS,
        migration_hash=sha256(migration_file.read_bytes()).hexdigest(),
        database="baseline_test",
        started_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 2, 10, 1, tzinfo=timezone.utc),
        statements=[StatementResult(1, "SELECT 'berhasil ✓'", True, 1.25)],
    )


@pytest.fixture
def approval(execution: MigrationExecutionResult) -> Approval:
    return Approval(
        "migration.sql", execution.migration_hash, execution.database,
        "test-backup.sql", 1, 0, execution.finished_at, "0.1.0",
    )


@pytest.fixture
def backup(tmp_path: Path, target: DatabaseConfig, approval: Approval) -> BackupMetadata:
    path = tmp_path / "backup.sql"
    path.write_bytes(b"-- baseline backup\n")
    return BackupMetadata(
        target.database, target.host, approval.approved_at, str(path),
        path.stat().st_size, sha256(path.read_bytes()).hexdigest(),
        approval.migration_hash, "0.1.0",
    )


@pytest.fixture
def db() -> Mock:
    stub = create_autospec(DatabasePort, instance=True, spec_set=True)
    stub.execute.return_value = None
    stub.test_connection.return_value = None
    return stub


@pytest.fixture
def history(approval: Approval, tmp_path: Path) -> Mock:
    stub = create_autospec(HistoryPort, instance=True, spec_set=True)
    stub.load_approval.return_value = approval
    stub.save_execution.return_value = tmp_path / "unused-execution"
    stub.save_approval.return_value = tmp_path / "unused-approval.json"
    return stub


@pytest.fixture
def parser() -> Mock:
    stub = create_autospec(MigrationParserPort, instance=True, spec_set=True)
    stub.parse_file.return_value = [
        MigrationStatement(i, f"SELECT {i}", StatementType.OTHER)
        for i in range(1, 4)
    ]
    return stub
