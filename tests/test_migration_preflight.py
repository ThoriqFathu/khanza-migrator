from dataclasses import replace
from unittest.mock import create_autospec

import pytest

from app.domains.migration.application.migration_preflight import MigrationPreflight
from app.domains.migration.application.preflight_ports import PreflightDatabasePort
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.domain.preflight import ForeignKey, PreflightStatus as Status
from app.shared.sql.parser import SqlMigrationParser
from app.shared.sql.preflight_extractor import MigrationPreflightExtractor


@pytest.fixture
def audit(tmp_path):
    db = create_autospec(PreflightDatabasePort, instance=True, spec_set=True)
    db.count_orphans.return_value = 0
    db.get_foreign_keys.return_value = []
    config = DatabaseConfig('invalid', 3306, 'test', 'tester', Environment.TEST)
    service = MigrationPreflight(SqlMigrationParser(), MigrationPreflightExtractor(), db)
    def run(sql, target=config):
        source = tmp_path / 'migration.sql'
        source.write_text(sql)
        progress = []
        result = service.execute(source, target, 'secret', lambda *args: progress.append(args))
        assert source.read_text() == sql
        assert progress[-1][0] == 'Migration preflight completed'
        return result
    return db, config, service, run


def fk(name='fk', child='c', parent='p'):
    return ForeignKey(name, child, ('id',), parent, ('id',), 'test', 'test')


def test_orphans_best_effort_and_combined_summary(audit):
    db, _, _, run = audit
    db.count_orphans.side_effect = [0, 12, RuntimeError('missing table'), 0]
    result = run(''.join(f'ALTER TABLE c ADD CONSTRAINT fk{i} FOREIGN KEY (id) REFERENCES p(id);'
                         for i in range(4)) + 'ALTER TABLE c MODIFY id int;')
    assert [f.status for f in result.findings] == [Status.SAFE, Status.BLOCKER, Status.ERROR, Status.SAFE,
                                                Status.BLOCKER, Status.BLOCKER, Status.BLOCKER, Status.BLOCKER]
    assert result.total_statements == 5
    assert result.fk_candidates == 4
    assert result.safe_fk_count == 2
    assert result.orphan_fk_count == 1
    assert result.column_modifications == 1
    assert result.dependency_issue_count == 4
    assert result.audit_error_count == 1
    assert result.findings[1].orphan_count == 12
    assert result.findings[2].error == 'missing table'
    assert db.count_orphans.call_count == 4


def test_no_dependency_safe(audit):
    *_, run = audit
    result = run('ALTER TABLE c MODIFY id int;')
    assert result.findings[0].status == Status.SAFE


@pytest.mark.parametrize('table', ['c', 'p'])
def test_child_and_parent_dependency(audit, table):
    db, _, _, run = audit
    db.get_foreign_keys.return_value = [fk()]
    result = run(f'ALTER TABLE {table} MODIFY COLUMN id int;')
    assert result.dependency_issue_count == 1
    assert result.findings[0].foreign_key == fk()
    assert '1832' in result.findings[0].problem


@pytest.mark.parametrize(('sql', 'blockers'), [
    ('ALTER TABLE c DROP FOREIGN KEY fk; ALTER TABLE p MODIFY id int;', 0),
    ('ALTER TABLE p MODIFY id int; ALTER TABLE c DROP FOREIGN KEY fk;', 1),
    ('ALTER TABLE other DROP FOREIGN KEY fk; ALTER TABLE p MODIFY id int;', 1),
    ('ALTER TABLE c DROP FOREIGN KEY FK; ALTER TABLE p MODIFY id int;', 0),
    ('ALTER TABLE other_db.c DROP FOREIGN KEY fk; ALTER TABLE p MODIFY id int;', 1),
    ('ALTER TABLE c DROP FOREIGN KEY fk; ALTER TABLE c ADD CONSTRAINT fk FOREIGN KEY(id) REFERENCES p(id); ALTER TABLE p MODIFY id int;', 1),
])
def test_drop_order_and_owning_table(audit, sql, blockers):
    db, _, _, run = audit
    db.get_foreign_keys.return_value = [fk()]
    result = run(sql)
    assert result.dependency_issue_count == blockers


def test_all_parent_dependencies_and_partial_drop(audit):
    db, _, _, run = audit
    db.get_foreign_keys.return_value = [fk(child='a'), fk(child='b'), fk(child='c')]
    result = run('ALTER TABLE b DROP FOREIGN KEY fk; ALTER TABLE p CHANGE COLUMN id new_id int;')
    assert result.dependency_issue_count == 2
    assert [f.foreign_key.child_table for f in result.findings] == ['a', 'c']


def test_metadata_failure_continues(audit):
    db, _, _, run = audit
    db.get_foreign_keys.side_effect = [RuntimeError('denied'), [], [fk()]]
    result = run('ALTER TABLE a MODIFY x int; ALTER TABLE b MODIFY y int; ALTER TABLE c MODIFY id int;')
    assert [f.status for f in result.findings] == [Status.ERROR, Status.SAFE, Status.BLOCKER]
    assert result.audit_error_count == 1


def test_same_alter_drop_modify_is_explicitly_unsupported(audit):
    db, _, _, run = audit
    db.get_foreign_keys.return_value = [fk()]
    result = run('ALTER TABLE c DROP FOREIGN KEY fk, MODIFY id int;')
    assert result.findings[0].status == Status.ERROR


def test_other_schema_and_unsupported_are_never_queried(audit):
    db, _, _, run = audit
    result = run('''ALTER TABLE production.c ADD CONSTRAINT fk FOREIGN KEY(id) REFERENCES p(id);
        ALTER TABLE c ADD CONSTRAINT fk FOREIGN KEY(id) REFERENCES production.p(id);
        ALTER TABLE production.c MODIFY id int;
        ALTER TABLE c ADD CONSTRAINT fk FOREIGN KEY(id) REFERENCES p(id) MATCH FULL;''')
    assert result.audit_error_count == 4
    db.count_orphans.assert_not_called()
    db.get_foreign_keys.assert_not_called()


def test_non_test_rejected_before_any_io(audit, tmp_path):
    db, config, service, _ = audit
    with pytest.raises(ValueError, match='TEST'):
        service.execute(tmp_path / 'does-not-exist', replace(config, environment=Environment.PRODUCTION), '')
    db.test_connection.assert_not_called()


def test_fatal_read_or_connection_errors(audit, tmp_path):
    db, config, service, run = audit
    with pytest.raises(FileNotFoundError):
        service.execute(tmp_path / 'missing.sql', config, '')
    db.test_connection.assert_not_called()
    db.test_connection.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError, match='offline'):
        run('ALTER TABLE c MODIFY id int;')
    db.get_foreign_keys.assert_not_called()


def test_later_column_after_change_is_not_assumed_safe(audit):
    *_, run = audit
    result = run('ALTER TABLE c CHANGE id new_id int; ALTER TABLE c MODIFY new_id bigint;')
    assert result.findings[-1].status == Status.ERROR


def test_multiple_unnamed_adds_remain_distinct_dependencies(audit):
    *_, run = audit
    result = run('ALTER TABLE c ADD FOREIGN KEY (id) REFERENCES a(id), '
                 'ADD FOREIGN KEY (id) REFERENCES b(id); ALTER TABLE c MODIFY id int;')
    assert result.dependency_issue_count == 2


def test_table_case_difference_keeps_dependency_conservatively(audit):
    db, _, _, run = audit
    db.get_foreign_keys.return_value = [fk()]
    assert run('ALTER TABLE C MODIFY id int;').dependency_issue_count == 1
