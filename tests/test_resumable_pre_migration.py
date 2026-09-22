from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.domains.migration.application.use_cases import ApproveMigrationUseCase, RunMigrationUseCase
from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.filesystem.sessions import LocalSessionRepository
from app.shared.sql.parser import SqlMigrationParser
from app.shared.sql.render import render_statements


@pytest.fixture
def session_setup(tmp_path: Path):
    source = tmp_path / 'input.sql'
    source.write_text('\n'.join(f'SELECT {i};' for i in range(1, 17)))
    target = DatabaseConfig('test.invalid', 3306, 'test_db', 'tester', Environment.TEST)
    db = Mock()
    history = Mock()
    store = LocalSessionRepository(tmp_path / 'sessions')
    service = ResumablePreMigration(SqlMigrationParser(), db, history, store)
    return source, target, db, history, store, service


def test_resume_14_preserves_success_prefix_and_exports_approved_file(session_setup):
    source, target, db, history, store, service = session_setup
    original = source.read_bytes()
    session = service.start(source, target, 'backup.sql')
    db.execute.side_effect = [None] * 13 + [RuntimeError('cannot modify column pegawai')]
    failed = service.run(session.session_id, target, 'password')
    assert failed.failed.sequence == 14
    assert len(failed.completed) == 13
    assert failed.pending == ['SELECT 14', 'SELECT 15', 'SELECT 16']
    assert [s.sql for s in store.load(session.session_id).completed] == [f'SELECT {i}' for i in range(1, 14)]
    assert not service.output_file(failed).exists()
    assert 'password' not in service.checkpoint_file(failed).read_text()
    db.execute.reset_mock(side_effect=True)
    completed = service.run(session.session_id, target, 'password', 'SELECT 140; SELECT 15; SELECT 16;')
    assert [c.args[2] for c in db.execute.call_args_list] == ['SELECT 140', 'SELECT 15', 'SELECT 16']
    expected = [f'SELECT {i}' for i in range(1, 14)] + ['SELECT 140', 'SELECT 15', 'SELECT 16']
    output = service.output_file(completed)
    assert output.name == 'migration_final.sql'
    assert [s.sql for s in SqlMigrationParser().parse_file(output)] == expected
    assert source.read_bytes() == original
    result = service.result(completed)
    assert result.status is MigrationStatus.SUCCESS
    assert (result.success_count, result.failed_count) == (16, 0)
    assert [s.sequence for s in result.statements] == list(range(1, 17))
    history.save_execution.assert_called_with(result, output)
    approval = ApproveMigrationUseCase(history).execute(output, target.database, 'backup.sql', result)
    assert approval.migration_hash == result.migration_hash
    assert approval.migration_name == 'migration_final.sql'
    # The existing production runner must see the full corrected sequence, not only the tail.
    production_db = Mock()
    final = RunMigrationUseCase(SqlMigrationParser(), production_db, Mock()).execute(
        output, replace(target, environment=Environment.PRODUCTION), 'pw')
    assert final.status is MigrationStatus.SUCCESS
    assert [c.args[2] for c in production_db.execute.call_args_list] == expected
    db.execute.reset_mock()
    service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()


def test_multiple_failures_advance_only_after_success(session_setup):
    source, target, db, history, store, service = session_setup
    session = service.start(source, target, '')
    db.execute.side_effect = RuntimeError('bad first SQL')
    first = service.run(session.session_id, target, 'pw')
    assert first.completed == [] and first.failed.sequence == 1
    db.execute.side_effect = [None, RuntimeError('bad second SQL')]
    second = service.run(session.session_id, target, 'pw', 'SELECT 100; SELECT 200; SELECT 300;')
    assert [s.sql for s in second.completed] == ['SELECT 100']
    assert second.failed.sequence == 2
    db.execute.reset_mock(side_effect=True)
    third = service.run(session.session_id, target, 'pw', 'SELECT 201; SELECT 300;')
    assert [c.args[2] for c in db.execute.call_args_list] == ['SELECT 201', 'SELECT 300']
    assert [s.sql for s in third.completed] == ['SELECT 100', 'SELECT 201', 'SELECT 300']


@pytest.mark.parametrize('edit', ['', '-- comment only\n'])
def test_empty_tail_is_rejected_before_database(session_setup, edit):
    source, target, db, _, _, service = session_setup
    session = service.start(source, target, '')
    with pytest.raises(ValueError, match='tidak boleh kosong'):
        service.run(session.session_id, target, 'pw', edit)
    db.execute.assert_not_called()
    assert len(service.load(session.session_id).pending) == 16


@pytest.mark.parametrize('field,value', [('host', 'another'), ('database', 'another'), ('port', 3307), ('environment', Environment.PRODUCTION)])
def test_resume_rejects_changed_target(session_setup, field, value):
    source, target, db, _, _, service = session_setup
    session = service.start(source, target, '')
    with pytest.raises(ValueError, match='TEST'):
        service.run(session.session_id, replace(target, **{field: value}), 'pw')
    db.execute.assert_not_called()


def test_start_rejects_production_and_empty_sql(session_setup):
    source, target, db, _, _, service = session_setup
    with pytest.raises(ValueError, match='TEST'):
        service.start(source, replace(target, environment=Environment.PRODUCTION), '')
    source.write_text('')
    with pytest.raises(ValueError, match='tidak berisi'):
        service.start(source, target, '')
    db.execute.assert_not_called()


def test_reset_invalidates_session(session_setup):
    source, target, db, _, store, service = session_setup
    session = service.start(source, target, '')
    service.invalidate(session.session_id)
    assert store.load(session.session_id).invalidated
    with pytest.raises(ValueError, match='reset'):
        service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()


def test_uncertain_checkpoint_blocks_reexecution(session_setup):
    source, target, db, _, store, service = session_setup
    session = service.start(source, target, '')
    session.in_flight = True
    store.save(session)
    with pytest.raises(ValueError, match='tidak pasti'):
        service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()


def test_success_checkpoint_failure_does_not_allow_replay(session_setup, monkeypatch):
    source, target, db, _, store, service = session_setup
    session = service.start(source, target, '')
    save = store.save
    def fail_after_sql(value):
        if value.completed:
            raise OSError('disk full')
        save(value)
    monkeypatch.setattr(store, 'save', fail_after_sql)
    with pytest.raises(OSError, match='disk full'):
        service.run(session.session_id, target, 'pw')
    assert db.execute.call_count == 1
    monkeypatch.setattr(store, 'save', save)
    with pytest.raises(ValueError, match='tidak pasti'):
        service.run(session.session_id, target, 'pw')
    assert db.execute.call_count == 1


def test_checkpoint_failure_before_sql_never_runs_database(session_setup, monkeypatch):
    source, target, db, _, store, service = session_setup
    session = service.start(source, target, '')
    monkeypatch.setattr(store, 'save', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()


def test_export_failure_can_retry_without_reexecuting_sql(session_setup, monkeypatch):
    source, target, db, _, store, service = session_setup
    session = service.start(source, target, '')
    write_sql = store.write_sql
    monkeypatch.setattr(store, 'write_sql', Mock(side_effect=OSError('read only')))
    with pytest.raises(OSError):
        service.run(session.session_id, target, 'pw')
    assert len(service.load(session.session_id).completed) == 16
    monkeypatch.setattr(store, 'write_sql', write_sql)
    db.execute.reset_mock()
    completed = service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()
    assert service.result(completed).success_count == 16


def test_modified_export_cannot_be_approved(session_setup):
    source, target, _, _, _, service = session_setup
    session = service.start(source, target, '')
    completed = service.run(session.session_id, target, 'pw')
    service.output_file(completed).write_text('SELECT 999;')
    with pytest.raises(ValueError, match='berubah'):
        service.result(completed)


def test_export_preserves_procedure_strings_delimiters_and_trailing_comment(tmp_path):
    statements = ["SELECT 'a;b $$KHANZA$$'", "CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END", 'SELECT 3 -- tail']
    path = tmp_path / 'migration_final.sql'
    path.write_text(render_statements(statements))
    assert [s.sql for s in SqlMigrationParser().parse_file(path)] == statements


def test_session_roundtrip_and_no_directory_traversal(session_setup):
    source, target, _, _, store, service = session_setup
    session = service.start(source, target, 'backup.sql')
    assert store.load(session.session_id) == session
    with pytest.raises(ValueError):
        store.file('../escape', 'session.json')


def test_history_failure_can_retry_export_without_replaying_sql(session_setup):
    source, target, db, history, _, service = session_setup
    session = service.start(source, target, '')
    history.save_execution.side_effect = OSError('history unavailable')
    with pytest.raises(OSError):
        service.run(session.session_id, target, 'pw')
    assert service.load(session.session_id).pending == []
    assert service.load(session.session_id).finished_at is None
    db.execute.reset_mock()
    history.save_execution.side_effect = None
    completed = service.run(session.session_id, target, 'pw')
    db.execute.assert_not_called()
    assert service.result(completed).success_count == 16


def test_connection_failure_preserves_edited_pending_sql(session_setup):
    source, target, db, _, _, service = session_setup
    session = service.start(source, target, '')
    db.test_connection.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError, match='offline'):
        service.run(session.session_id, target, 'pw', 'SELECT 100;')
    db.execute.assert_not_called()
    assert service.load(session.session_id).pending == ['SELECT 100']


def test_clear_all_sessions_removes_artifacts_without_touching_other_files(session_setup):
    source, target, _, _, store, service = session_setup
    first = service.start(source, target, '')
    second = service.start(source, target, '')
    service.run(first.session_id, target, 'pw')
    store.write_sql(second.session_id, 'pending.sql', 'SELECT 100;')
    outside = source.parent / 'keep.sql'
    outside.write_text('SELECT 999;')
    (store.root / 'external-link').symlink_to(outside)
    service.clear_all_sessions()
    assert list(store.root.iterdir()) == []
    assert outside.read_text() == 'SELECT 999;'
    assert source.is_file()
    service.clear_all_sessions()
    new = service.start(source, target, '')
    assert service.load(new.session_id) == new


def test_clear_sessions_when_directory_does_not_exist(tmp_path):
    store = LocalSessionRepository(tmp_path / 'missing')
    store.clear_all()
    assert not store.root.exists()
