from datetime import datetime, timezone

from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
from app.domains.migration.domain.models import StatementResult
from app.shared.diagnostic_report import CASCADE_NOTE, render_diagnostic_report


def test_markdown_contains_all_actual_failures_and_summary():
    now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
    result = DiagnosticMigrationResult(now, now, 'restored_test', 'migration.sql', 3, [
        StatementResult(1, 'SELECT successful_statement', True, 1),
        StatementResult(2, 'ALTER TABLE c MODIFY id int', False, 2, 1832, 'ERROR 1832 (HY000): Cannot change column'),
        StatementResult(3, 'ALTER TABLE c ADD FOREIGN KEY (id) REFERENCES p(id)', False, 2, 1452,
                        'ERROR 1452 (23000): Cannot add child row'),
    ])
    report = render_diagnostic_report(result, now)
    for value in ['Generated: 2026-09-25T01:00:00+00:00', 'Environment: TEST', 'Database: restored_test',
                  'Migration: migration.sql', 'Total Statements: 3', 'Success: 1', 'Failed: 2',
                  '## Failure 1', '## Failure 2', 'Statement: #2', 'Statement: #3',
                  'MySQL Error: 1832', 'MySQL Error: 1452', 'Reset/Restore TEST']:
        assert value in report
    for item in result.statements[1:]:
        assert item.sql in report and item.error_message in report
    assert 'successful_statement' not in report
    assert all(line in report for line in CASCADE_NOTE.splitlines())
    assert 'MYSQL_PWD' not in report


def test_unknown_code_and_code_fences_preserve_untrusted_sql_text():
    now = datetime.now(timezone.utc)
    sql = "SELECT '```';\nSELECT '<script>text</script>'"
    result = DiagnosticMigrationResult(now, now, 'test', 'migration.sql', 1, [
        StatementResult(1, sql, False, 1, None, 'unknown actual error ```\nrest')])
    report = render_diagnostic_report(result, now)
    assert f'````sql\n{sql}\n````' in report
    assert 'MySQL Error: Unavailable' in report
    assert 'unknown actual error ```\nrest' in report


def test_partial_run_report_marks_aborted_and_all_success_run_still_warns_reset():
    now = datetime.now(timezone.utc)
    result = DiagnosticMigrationResult(now, now, 'test', 'migration.sql', 3,
        [StatementResult(1, 'SELECT 1', True, 1)], 'mysql executable unavailable')
    report = render_diagnostic_report(result)
    assert 'Unattempted: 2' in report and 'Run: ABORTED' in report
    assert 'mysql executable unavailable' in report
    result.fatal_error = ''
    result.total_statements = 1
    report = render_diagnostic_report(result)
    assert 'Run: COMPLETED' in report and 'Failed: 0' in report
    assert 'Reset/Restore TEST' in report
    assert '## Failure 1' not in report
