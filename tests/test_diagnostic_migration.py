from dataclasses import replace
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from app.domains.migration.application.diagnostic_migration import DiagnosticMigration
from app.domains.migration.application.ports import DatabasePort
from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.filesystem.sessions import LocalSessionRepository
from app.shared.sql.parser import SqlMigrationParser


@pytest.fixture
def diagnostic(tmp_path):
    parser = SqlMigrationParser()
    db = create_autospec(DatabasePort, instance=True, spec_set=True)
    target = DatabaseConfig('invalid', 3306, 'diagnostic_test', 'tester', Environment.TEST)
    source = tmp_path / 'migration.sql'
    source.write_text('SELECT 1; SELECT 2; SELECT 3; SELECT 4; SELECT 5;')
    return DiagnosticMigration(parser, db), db, target, source


def actual_error(code, text):
    error = RuntimeError(text.strip())
    error.stderr = text
    error.errno = code
    return error


def test_all_success_uses_existing_execution_exactly_once(diagnostic):
    service, db, target, source = diagnostic
    progress = []
    result = service.execute(source, target, 'secret', lambda *args: progress.append(args))
    assert (result.total_statements, result.success_count, result.failure_count) == (5, 5, 0)
    assert result.completed and result.unattempted_count == 0
    assert result.started_at <= result.finished_at
    assert result.database == target.database and result.migration_name == source.name
    assert [call.args for call in db.execute.call_args_list] == [
        (target, 'secret', f'SELECT {n}') for n in range(1, 6)]
    assert [s.sequence for s in result.statements] == [1, 2, 3, 4, 5]
    assert progress[-1][1:] == (5, 5)
    assert all(s.error_message is None for s in result.statements)
    db.create_database.assert_not_called()
    db.drop_database.assert_not_called()
    db.import_sql.assert_not_called()


def test_failures_captured_and_every_later_statement_runs(diagnostic):
    service, db, target, source = diagnostic
    raw1832 = "ERROR 1832 (HY000) at line 1: Cannot change column 'id'\n"
    raw1452 = 'ERROR 1452 (23000): Cannot add child row\n'
    db.execute.side_effect = [None, actual_error(1832, raw1832), None, actual_error(1452, raw1452), None]
    progress = []
    result = service.execute(source, target, '', lambda *args: progress.append(args))
    assert result.completed
    assert (result.total_statements, result.success_count, result.failure_count) == (5, 3, 2)
    assert db.execute.call_count == 5
    assert [(s.sequence, s.sql, s.error_code, s.error_message) for s in result.statements if not s.success] == [
        (2, 'SELECT 2', 1832, raw1832), (4, 'SELECT 4', 1452, raw1452)]
    assert [args[1] for args in progress if ' — ' in args[0]] == [1, 2, 3, 4, 5]


def test_all_failures_and_unknown_format_are_preserved(diagnostic):
    service, db, target, source = diagnostic
    db.execute.side_effect = [RuntimeError(f'unknown actual error {i}') for i in range(1, 6)]
    result = service.execute(source, target, '')
    assert (result.success_count, result.failure_count) == (0, 5)
    assert result.completed
    assert [s.error_message for s in result.statements] == [f'unknown actual error {i}' for i in range(1, 6)]
    assert all(s.error_code is None for s in result.statements)


def test_non_test_rejected_before_read_or_connection(diagnostic):
    service, db, target, source = diagnostic
    source.unlink()
    with pytest.raises(ValueError, match='TEST'):
        service.execute(source, replace(target, environment=Environment.PRODUCTION), '')
    assert not db.mock_calls


@pytest.mark.parametrize('stage', ['read', 'parse', 'connection', 'empty'])
def test_fatal_initial_failure_runs_no_statement(diagnostic, monkeypatch, stage):
    service, db, target, source = diagnostic
    if stage == 'read':
        source.unlink()
    elif stage == 'parse':
        monkeypatch.setattr(service.parser, 'parse_file', lambda _: (_ for _ in ()).throw(ValueError('parse failed')))
    elif stage == 'connection':
        db.test_connection.side_effect = RuntimeError('unreachable')
    else:
        source.write_text('-- only comment')
    with pytest.raises(RuntimeError):
        service.execute(source, target, '')
    db.execute.assert_not_called()


def test_os_failure_retains_partial_result_and_unattempted_count(diagnostic):
    service, db, target, source = diagnostic
    db.execute.side_effect = [None, FileNotFoundError(2, 'mysql unavailable')]
    result = service.execute(source, target, '')
    assert not result.completed
    assert (result.success_count, result.failure_count, result.unattempted_count) == (1, 1, 3)
    assert result.fatal_error == result.statements[-1].error_message
    assert result.statements[-1].error_code is None  # OS errno 2 is not a MySQL error.
    assert db.execute.call_count == 2


def test_password_redacted_before_artifact_but_sql_executed_unchanged(diagnostic):
    service, db, target, source = diagnostic
    source.write_text("SELECT 'connection-password';")
    raw = 'ERROR 1005 (HY000): echoed connection-password\n'
    db.execute.side_effect = actual_error(1005, raw)
    result = service.execute(source, target, 'connection-password')
    assert 'connection-password' not in repr(result)
    assert '[REDACTED]' in result.statements[0].error_message
    assert db.execute.call_args.args[2] == "SELECT 'connection-password'"


def test_fatal_password_redacted(diagnostic):
    service, db, target, source = diagnostic
    db.test_connection.side_effect = RuntimeError('password=secret-token')
    with pytest.raises(RuntimeError) as error:
        service.execute(source, target, 'secret-token')
    assert 'secret-token' not in str(error.value)


def test_initial_diagnostic_and_pre_have_same_sql_and_sequences(diagnostic, tmp_path: Path):
    service, db, target, source = diagnostic
    source.write_text('''/* comment-only statement */;
SELECT 'semicolon;literal';
DELIMITER $$
CREATE PROCEDURE demo() BEGIN SELECT 1; SELECT 2; END$$
DELIMITER ;
SELECT 3;
-- trailing comment''')
    result = service.execute(source, target, '')
    resume = ResumablePreMigration(service.parser, db, LocalHistoryRepository(tmp_path / 'history'),
                                   LocalSessionRepository(tmp_path / 'sessions'))
    session = resume.start(source, target, 'backup.sql')
    assert [s.sql for s in result.statements] == session.pending
    session = resume.run(session.session_id, target, '')
    assert [(s.sequence, s.sql) for s in result.statements] == [(s.sequence, s.sql) for s in session.completed]
