"""Baseline format persistence menggunakan filesystem sementara saja."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.domains.migration.domain.enums import MigrationStatus
from app.domains.migration.domain.models import (
    Approval, BackupMetadata, MigrationExecutionResult, StatementResult,
)
from app.shared.filesystem.history import LocalHistoryRepository


@pytest.mark.parametrize("with_backup", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_execution_persistence_preserves_existing_schema(
    tmp_path: Path, migration_file: Path, execution: MigrationExecutionResult,
    backup: BackupMetadata, with_backup: bool, failed: bool,
) -> None:
    repository = LocalHistoryRepository(tmp_path / "history")
    if failed:
        execution = replace(
            execution, status=MigrationStatus.FAILED,
            statements=[*execution.statements, StatementResult(2, "INVALID SQL", False, 2.5, 1064, "syntax error")],
        )
    folder = repository.save_execution(execution, migration_file, backup if with_backup else None)

    # Baseline: tahun/bulan dari started_at; timestamp folder memakai timezone lokal.
    expected_folder = (
        tmp_path / "history" / "2026" / "01"
        / f"migration_{execution.started_at.astimezone():%Y%m%d_%H%M%S}"
    )
    assert folder == expected_folder
    assert {p.name for p in folder.iterdir()} == {"migration.sql", "result.json", "metadata.json"}
    assert (folder / "migration.sql").read_bytes() == migration_file.read_bytes()
    expected_statements = [{
        "sequence": 1, "sql": "SELECT 'berhasil ✓'", "success": True,
        "duration_ms": 1.25, "error_code": None, "error_message": None,
    }]
    if failed:
        expected_statements.append({
            "sequence": 2, "sql": "INVALID SQL", "success": False,
            "duration_ms": 2.5, "error_code": 1064, "error_message": "syntax error",
        })
    payload_text = (folder / "result.json").read_text(encoding="utf-8")
    assert "✓" in payload_text
    assert json.loads(payload_text) == {
        "status": "FAILED" if failed else "SUCCESS",
        "migration_hash": execution.migration_hash,
        "database": "baseline_test",
        "started_at": "2026-01-02T10:00:00+00:00",
        "finished_at": "2026-01-02T10:01:00+00:00",
        "success_count": 1, "failed_count": int(failed), "statements": expected_statements,
    }
    expected_backup = None
    if with_backup:
        expected_backup = {
            "database": backup.database, "host": backup.host,
            "created_at": "2026-01-02 10:01:00+00:00",
            "backup_file": backup.backup_file, "file_size": backup.file_size,
            "backup_sha256": backup.backup_sha256,
            "migration_sha256": backup.migration_sha256, "application_version": "0.1.0",
        }
    assert json.loads((folder / "metadata.json").read_text(encoding="utf-8")) == {
        "migration_file": str(migration_file.resolve()),
        "migration_hash": execution.migration_hash, "database": "baseline_test",
        "status": "FAILED" if failed else "SUCCESS", "backup": expected_backup,
    }


def test_approval_persistence_schema_and_round_trip(tmp_path: Path, approval: Approval) -> None:
    root = tmp_path / "history"
    repository = LocalHistoryRepository(root)
    path = repository.save_approval(approval)
    assert path == tmp_path / "approval.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "migration_name": "migration.sql", "migration_hash": approval.migration_hash,
        "test_database": "baseline_test", "test_backup": "test-backup.sql",
        "success_count": 1, "failed_count": 0,
        # Baseline: approval datetime memakai str(datetime), execution memakai isoformat().
        "approved_at": "2026-01-02 10:01:00+00:00", "application_version": "0.1.0",
    }
    assert LocalHistoryRepository(root).load_approval() == approval


def test_missing_approval_returns_none(tmp_path: Path) -> None:
    repository = LocalHistoryRepository(tmp_path / "history")
    assert repository.root.is_dir()
    assert repository.load_approval() is None


def test_saving_approval_replaces_previous_approval_baseline(tmp_path: Path, approval: Approval) -> None:
    repository = LocalHistoryRepository(tmp_path / "history")
    first_path = repository.save_approval(approval)
    newer = replace(approval, migration_name="new.sql", migration_hash="new-hash")
    # Baseline: hanya satu approval aktif, bukan append-only history approval.
    assert repository.save_approval(newer) == first_path
    assert LocalHistoryRepository(repository.root).load_approval() == newer


def test_corrupt_approval_json_propagates_error(tmp_path: Path) -> None:
    repository = LocalHistoryRepository(tmp_path / "history")
    repository.approval_file.write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        repository.load_approval()
