"""Characterization tests: keterbatasan existing sengaja tidak diperbaiki."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, call, create_autospec

import pytest

from app.domains.migration.application.use_cases import (
    ApproveMigrationUseCase, RunFinalMigrationUseCase, RunMigrationUseCase,
    ValidateFinalMigrationUseCase,
)
from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import (
    Approval, BackupMetadata, DatabaseConfig, MigrationExecutionResult,
    SafetyCheck, SafetyReport, StatementResult,
)


@pytest.mark.parametrize("environment", [Environment.TEST, Environment.PRODUCTION])
def test_run_success_persists_result_and_reports_progress(
    migration_file: Path, target: DatabaseConfig, parser: Mock, db: Mock,
    history: Mock, environment: Environment,
) -> None:
    config = replace(target, environment=environment)
    progress = Mock()
    result = RunMigrationUseCase(parser, db, history).execute(
        migration_file, config, "password", progress,
    )

    parser.parse_file.assert_called_once_with(migration_file)
    assert db.execute.call_args_list == [
        call(config, "password", f"SELECT {i}") for i in range(1, 4)
    ]
    assert result.status is MigrationStatus.SUCCESS
    assert result.database == config.database
    assert result.migration_hash == sha256(migration_file.read_bytes()).hexdigest()
    assert (result.success_count, result.failed_count, result.failed_statement) == (3, 0, None)
    assert [(s.sequence, s.sql, s.success) for s in result.statements] == [
        (i, f"SELECT {i}", True) for i in range(1, 4)
    ]
    assert all(s.duration_ms >= 0 and s.error_code is None and s.error_message is None
               for s in result.statements)
    assert result.started_at.utcoffset().total_seconds() == 0
    assert result.finished_at.utcoffset().total_seconds() == 0
    history.save_execution.assert_called_once_with(result, migration_file)
    assert progress.call_args_list == [
        call("Executing #001 OTHER", 0, 3),
        call("Executing #002 OTHER", 1, 3),
        call("Executing #003 OTHER", 2, 3),
        call("Migration completed", 3, 3),
    ]


@pytest.mark.parametrize("failure_index", [0, 1])
@pytest.mark.parametrize("errno", [None, 1064])
def test_run_stops_at_first_failure_and_persists_partial_result(
    migration_file: Path, target: DatabaseConfig, parser: Mock, db: Mock,
    history: Mock, failure_index: int, errno: int | None,
) -> None:
    error = RuntimeError("SQL gagal")
    if errno is not None:
        error.errno = errno
    db.execute.side_effect = [None] * failure_index + [error]
    progress = Mock()
    result = RunMigrationUseCase(parser, db, history).execute(
        migration_file, target, "password", progress,
    )

    assert db.execute.call_args_list == [
        call(target, "password", f"SELECT {i}") for i in range(1, failure_index + 2)
    ]
    assert result.status is MigrationStatus.FAILED
    assert (result.success_count, result.failed_count) == (failure_index, 1)
    assert len(result.statements) == failure_index + 1
    failed = result.failed_statement
    assert failed is result.statements[-1]
    assert (failed.sequence, failed.sql, failed.success) == (
        failure_index + 1, f"SELECT {failure_index + 1}", False,
    )
    assert (failed.error_code, failed.error_message) == (errno, "SQL gagal")
    history.save_execution.assert_called_once_with(result, migration_file)
    assert progress.call_args == call("Migration FAILED", failure_index + 1, 3)


def test_run_empty_statements_is_success_baseline(
    migration_file: Path, target: DatabaseConfig, parser: Mock, db: Mock, history: Mock,
) -> None:
    # Baseline: nol statement dianggap SUCCESS, walau pre-flight menolak count nol.
    parser.parse_file.return_value = []
    result = RunMigrationUseCase(parser, db, history).execute(migration_file, target, "pw")
    assert result.status is MigrationStatus.SUCCESS
    assert result.statements == []
    assert (result.success_count, result.failed_count) == (0, 0)
    db.execute.assert_not_called()
    history.save_execution.assert_called_once_with(result, migration_file)


def test_run_missing_file_has_no_execution_or_history(
    tmp_path: Path, target: DatabaseConfig, parser: Mock, db: Mock, history: Mock,
) -> None:
    with pytest.raises(FileNotFoundError):
        RunMigrationUseCase(parser, db, history).execute(tmp_path / "missing.sql", target, "pw")
    parser.parse_file.assert_not_called()
    db.execute.assert_not_called()
    history.save_execution.assert_not_called()


def test_run_parser_error_propagates_without_execution(
    migration_file: Path, target: DatabaseConfig, parser: Mock, db: Mock, history: Mock,
) -> None:
    parser.parse_file.side_effect = ValueError("parse failed")
    with pytest.raises(ValueError, match="parse failed"):
        RunMigrationUseCase(parser, db, history).execute(migration_file, target, "pw")
    db.execute.assert_not_called()
    history.save_execution.assert_not_called()


def test_run_history_error_propagates_after_sql_execution(
    migration_file: Path, target: DatabaseConfig, parser: Mock, db: Mock, history: Mock,
) -> None:
    history.save_execution.side_effect = OSError("history unavailable")
    with pytest.raises(OSError, match="history unavailable"):
        RunMigrationUseCase(parser, db, history).execute(migration_file, target, "pw")
    assert db.execute.call_count == 3
    history.save_execution.assert_called_once()


def test_approve_success_persists_approval(
    migration_file: Path, execution: MigrationExecutionResult, history: Mock,
) -> None:
    approval = ApproveMigrationUseCase(history).execute(
        migration_file, "test_db", "test_backup.sql", execution,
    )
    assert (approval.migration_name, approval.migration_hash) == (
        migration_file.name, execution.migration_hash,
    )
    assert (approval.test_database, approval.test_backup) == ("test_db", "test_backup.sql")
    assert (approval.success_count, approval.failed_count) == (1, 0)
    assert approval.application_version == "0.1.0"
    assert approval.approved_at.utcoffset().total_seconds() == 0
    history.save_approval.assert_called_once_with(approval)


@pytest.mark.parametrize("status", [s for s in MigrationStatus if s is not MigrationStatus.SUCCESS])
def test_approve_rejects_non_success(
    migration_file: Path, execution: MigrationExecutionResult, history: Mock,
    status: MigrationStatus,
) -> None:
    with pytest.raises(ValueError, match="Hanya pre-migration SUCCESS"):
        ApproveMigrationUseCase(history).execute(
            migration_file, "test", "backup.sql", replace(execution, status=status),
        )
    history.save_approval.assert_not_called()


def test_approve_rejects_changed_file(
    migration_file: Path, execution: MigrationExecutionResult, history: Mock,
) -> None:
    migration_file.write_text("SELECT 'changed';", encoding="utf-8")
    with pytest.raises(ValueError, match="berubah setelah execution"):
        ApproveMigrationUseCase(history).execute(migration_file, "test", "backup.sql", execution)
    history.save_approval.assert_not_called()


@pytest.mark.parametrize("statements", [[], [StatementResult(1, "bad SQL", False, 0.0)]])
def test_approve_trusts_success_status_without_checking_counts_baseline(
    migration_file: Path, execution: MigrationExecutionResult, history: Mock,
    statements: list[StatementResult],
) -> None:
    # Baseline: approval memeriksa status/hash, bukan konsistensi status dengan count.
    result = replace(execution, statements=statements)
    approval = ApproveMigrationUseCase(history).execute(migration_file, "test", "backup.sql", result)
    assert (approval.success_count, approval.failed_count) == (0, len(statements))
    history.save_approval.assert_called_once_with(approval)


def test_validate_passes_with_existing_requirements(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata, db: Mock, history: Mock,
) -> None:
    report = ValidateFinalMigrationUseCase(db, history).execute(migration_file, target, "pw", backup)
    assert report.passed
    assert [c.name for c in report.checks] == [
        "Migration exists", "Migration readable", "Migration hash matches approval",
        "Pre-migration successful", "Approval exists", "Target is PRODUCTION",
        "Target connection", "Production backup verified",
    ]
    history.load_approval.assert_called_once_with()
    db.test_connection.assert_called_once_with(target, "pw")
    db.execute.assert_not_called()


@pytest.mark.parametrize("scenario, failed_names", [
    ("no_approval", {"Approval exists", "Migration hash matches approval", "Pre-migration successful"}),
    ("hash_changed", {"Migration hash matches approval"}),
    ("zero_success", {"Pre-migration successful"}),
    ("negative_success", {"Pre-migration successful"}),
    ("failed_statements", {"Pre-migration successful"}),
    ("negative_failures", {"Pre-migration successful"}),
    ("test_target", {"Target is PRODUCTION"}),
    ("connection_error", {"Target connection"}),
    ("no_backup", {"Production backup verified"}),
    ("missing_file", {"Migration exists", "Migration readable", "Migration hash matches approval"}),
])
def test_validate_failed_gates_baseline(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
    approval: Approval, db: Mock, history: Mock, scenario: str, failed_names: set[str],
) -> None:
    candidate_backup = backup
    if scenario == "no_approval":
        history.load_approval.return_value = None
    elif scenario == "hash_changed":
        migration_file.write_text("SELECT 'changed';", encoding="utf-8")
    elif scenario in {"zero_success", "negative_success", "failed_statements", "negative_failures"}:
        counts = {"zero_success": (0, 0), "negative_success": (-1, 0),
                  "failed_statements": (1, 1), "negative_failures": (1, -1)}
        successes, failures = counts[scenario]
        history.load_approval.return_value = replace(approval, success_count=successes, failed_count=failures)
    elif scenario == "test_target":
        target = replace(target, environment=Environment.TEST)
    elif scenario == "connection_error":
        db.test_connection.side_effect = RuntimeError("connection denied")
    elif scenario == "no_backup":
        candidate_backup = None
    elif scenario == "missing_file":
        migration_file = migration_file.parent / "missing.sql"

    report = ValidateFinalMigrationUseCase(db, history).execute(
        migration_file, target, "pw", candidate_backup,
    )
    assert not report.passed
    assert {c.name for c in report.checks if not c.passed} == failed_names
    if scenario == "test_target":
        db.test_connection.assert_not_called()
        # Baseline: target TEST menghasilkan tujuh checks, tanpa Target connection.
        assert "Target connection" not in [c.name for c in report.checks]
    else:
        # Baseline: gate lain gagal tidak menghentikan pemeriksaan koneksi PRODUCTION.
        db.test_connection.assert_called_once_with(target, "pw")
    if scenario == "connection_error":
        assert next(c.detail for c in report.checks if c.name == "Target connection") == "connection denied"


@pytest.mark.parametrize("mismatch", ["database", "host", "migration_sha256", "backup_sha256", "file_size", "missing_file"])
def test_validate_accepts_backup_metadata_without_verifying_it_baseline(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
    db: Mock, history: Mock, mismatch: str,
) -> None:
    # Baseline existing: backup is considered valid when metadata exists.
    if mismatch == "missing_file":
        backup = replace(backup, backup_file=str(migration_file.parent / "missing-backup.sql"))
    elif mismatch == "file_size":
        backup = replace(backup, file_size=0)
    else:
        backup = replace(backup, **{mismatch: "does-not-match"})
    report = ValidateFinalMigrationUseCase(db, history).execute(migration_file, target, "pw", backup)
    assert report.passed


@pytest.mark.parametrize("status", [MigrationStatus.SUCCESS, MigrationStatus.FAILED])
def test_final_validates_before_running_and_returns_result_unchanged(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
    execution: MigrationExecutionResult, status: MigrationStatus,
) -> None:
    validator = create_autospec(ValidateFinalMigrationUseCase, instance=True, spec_set=True)
    runner = create_autospec(RunMigrationUseCase, instance=True, spec_set=True)
    validator.execute.return_value = SafetyReport([SafetyCheck("gate", True, "ok")])
    result = replace(execution, status=status)
    runner.execute.return_value = result
    events = Mock()
    events.attach_mock(validator, "validator")
    events.attach_mock(runner, "runner")
    progress = Mock()

    actual = RunFinalMigrationUseCase(runner, validator).execute(
        migration_file, target, "pw", backup, progress,
    )
    assert actual is result  # Baseline: SUCCESS tidak diubah menjadi COMPLETED.
    assert events.mock_calls == [
        call.validator.execute(migration_file, target, "pw", backup),
        call.runner.execute(migration_file, target, "pw", progress),
    ]


def test_final_blocks_execution_and_reports_only_failed_checks(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
) -> None:
    validator = create_autospec(ValidateFinalMigrationUseCase, instance=True, spec_set=True)
    runner = create_autospec(RunMigrationUseCase, instance=True, spec_set=True)
    validator.execute.return_value = SafetyReport([
        SafetyCheck("approval", False, "missing"), SafetyCheck("connection", True, "ok"),
        SafetyCheck("backup", False, "missing"),
    ])
    with pytest.raises(PermissionError) as caught:
        RunFinalMigrationUseCase(runner, validator).execute(migration_file, target, "pw", backup)
    assert str(caught.value) == "Final migration diblokir:\n- approval: missing\n- backup: missing"
    validator.execute.assert_called_once_with(migration_file, target, "pw", backup)
    runner.execute.assert_not_called()


def test_final_validator_exception_prevents_execution(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
) -> None:
    validator = create_autospec(ValidateFinalMigrationUseCase, instance=True, spec_set=True)
    runner = create_autospec(RunMigrationUseCase, instance=True, spec_set=True)
    validator.execute.side_effect = OSError("approval unreadable")
    with pytest.raises(OSError, match="approval unreadable"):
        RunFinalMigrationUseCase(runner, validator).execute(migration_file, target, "pw", backup)
    runner.execute.assert_not_called()


def test_final_with_real_use_cases_does_not_pass_backup_to_history_baseline(
    migration_file: Path, target: DatabaseConfig, backup: BackupMetadata,
    parser: Mock, db: Mock, history: Mock,
) -> None:
    # Baseline: metadata backup hanya dipakai validasi, tidak masuk execution history.
    use_case = RunFinalMigrationUseCase(
        RunMigrationUseCase(parser, db, history), ValidateFinalMigrationUseCase(db, history),
    )
    result = use_case.execute(migration_file, target, "pw", backup)
    assert result.status is MigrationStatus.SUCCESS
    history.save_execution.assert_called_once_with(result, migration_file)
