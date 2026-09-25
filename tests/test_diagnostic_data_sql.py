from datetime import datetime, timezone

from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
from app.domains.migration.domain.models import StatementResult
from app.shared.diagnostic_data_sql import (
    parse_1452_foreign_key,
    render_diagnostic_data_audit,
    render_diagnostic_data_cleanup,
)


def result_with(*statements):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    return DiagnosticMigrationResult(now, now, 'test', 'migration.sql', len(statements), list(statements))


def failure(sequence, sql, code):
    return StatementResult(sequence, sql, False, 1, code, f'ERROR {code}')


def test_parse_single_column_1452_fk():
    item = failure(12, 'ALTER TABLE `aturan_pakai` ADD CONSTRAINT `aturan_pakai_ibfk_1` '
                  'FOREIGN KEY (`no_rawat`) REFERENCES `reg_periksa` (`no_rawat`) ON DELETE CASCADE', 1452)
    spec = parse_1452_foreign_key(item)
    assert spec is not None
    assert spec.child_table == 'aturan_pakai'
    assert spec.child_columns == ('no_rawat',)
    assert spec.parent_table == 'reg_periksa'
    assert spec.parent_columns == ('no_rawat',)
    assert spec.constraint_name == 'aturan_pakai_ibfk_1'


def test_audit_is_select_only_and_cleanup_contains_executable_delete():
    item = failure(25, 'ALTER TABLE `detail_pemberian_obat` ADD CONSTRAINT `detail_pemberian_obat_ibfk_3` '
                  'FOREIGN KEY (`kode_brng`) REFERENCES `databarang` (`kode_brng`)', 1452)
    result = result_with(item)
    audit = render_diagnostic_data_audit(result)
    cleanup = render_diagnostic_data_cleanup(result)
    assert 'SELECT c.*' in audit
    assert 'GROUP BY c.`kode_brng`' in audit
    assert 'DELETE ' not in audit
    assert 'DELETE c FROM `detail_pemberian_obat` AS c' in cleanup
    assert 'p.`kode_brng` = c.`kode_brng`' in cleanup
    assert 'Diagnostic statement #25' in cleanup


def test_cleanup_ignores_non_1452_failures():
    result = result_with(
        failure(1, 'ALTER TABLE x ADD UNIQUE KEY u (id)', 1062),
        failure(2, 'ALTER TABLE x MODIFY COLUMN name varchar(10)', 1265),
        failure(3, 'ALTER TABLE x ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES p(id)', 1005),
    )
    cleanup = render_diagnostic_data_cleanup(result)
    assert 'DELETE c FROM' not in cleanup
    assert 'No parseable ERROR 1452' in cleanup


def test_composite_fk_requires_all_child_columns_non_null_and_matches_tuple():
    item = failure(7, 'ALTER TABLE `child` ADD CONSTRAINT `fk_pair` FOREIGN KEY (`a`, `b`) '
                  'REFERENCES `parent` (`x`, `y`)', 1452)
    result = result_with(item)
    cleanup = render_diagnostic_data_cleanup(result)
    assert 'c.`a` IS NOT NULL' in cleanup
    assert 'c.`b` IS NOT NULL' in cleanup
    assert 'p.`x` = c.`a`' in cleanup
    assert 'p.`y` = c.`b`' in cleanup


def test_unparseable_1452_is_not_given_destructive_sql():
    item = failure(9, 'INSERT INTO child VALUES (1)', 1452)
    result = result_with(item)
    assert parse_1452_foreign_key(item) is None
    assert 'DELETE c FROM' not in render_diagnostic_data_cleanup(result)
