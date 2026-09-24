import subprocess
from unittest.mock import Mock

import pytest

from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.database.mysql_client import MySqlClient


@pytest.mark.parametrize(('raw', 'code'), [
    ('ERROR 1832 (HY000): Cannot change column\n', 1832),
    ('ERROR 1452 (23000) at line 1: Cannot add child row\n', 1452),
    ('Warning: something\nERROR 1005 (HY000) at line 1: create failed\n', 1005),
    ('ERROR 1061 (42000): Duplicate key name\n', 1061),
    ('some database error\n', None),
    ('message mentions ERROR 1832 but is not a CLI error header', None),
    ('ERROR 1832 (invalid): unknown format', None),
])
def test_actual_cli_error_code_and_unmodified_raw_stderr(monkeypatch, raw, code):
    run = Mock(return_value=subprocess.CompletedProcess([], 1, '', raw))
    monkeypatch.setattr(subprocess, 'run', run)
    target = DatabaseConfig('invalid', 3306, 'test', 'tester', Environment.TEST)
    with pytest.raises(RuntimeError) as error:
        MySqlClient().execute(target, 'private-password', 'SELECT 1')
    assert error.value.errno == code
    assert error.value.stderr == raw
    assert str(error.value) == raw.strip()
    assert 'private-password' not in repr(vars(error.value))
    command = run.call_args.args[0]
    assert command[-1] == 'SELECT 1'
    assert 'private-password' not in command
    assert run.call_args.kwargs['env']['MYSQL_PWD'] == 'private-password'


def test_stdout_fallback_unknown_format(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 1, 'actual output\n', '')))
    with pytest.raises(RuntimeError) as error:
        MySqlClient._run(['mysql'], '')
    assert error.value.stderr == 'actual output\n' and error.value.errno is None


def test_cli_output_flows_through_diagnostic_into_markdown(monkeypatch, tmp_path):
    from app.domains.migration.application.diagnostic_migration import DiagnosticMigration
    from app.shared.sql.parser import SqlMigrationParser
    from app.shared.diagnostic_report import render_diagnostic_report
    raw = "ERROR 1452 (23000) at line 1: actual server detail 'secret-token'\n"
    process = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, '1\n', ''),
        subprocess.CompletedProcess([], 1, '', raw),
        subprocess.CompletedProcess([], 0, '', ''),
    ])
    monkeypatch.setattr(subprocess, 'run', process)
    source = tmp_path / 'migration.sql'
    source.write_text('INSERT INTO c VALUES (42); SELECT 2;')
    target = DatabaseConfig('invalid', 3306, 'test', 'tester', Environment.TEST)
    result = DiagnosticMigration(SqlMigrationParser(), MySqlClient()).execute(source, target, 'secret-token')
    assert result.completed and result.failure_count == 1 and result.success_count == 1
    assert [call.args[0][-1] for call in process.call_args_list] == [
        'SELECT 1', 'INSERT INTO c VALUES (42)', 'SELECT 2']
    report = render_diagnostic_report(result)
    assert "ERROR 1452 (23000) at line 1: actual server detail '[REDACTED]'\n" in report
    assert 'MySQL Error: 1452' in report
    assert 'secret-token' not in report and 'MYSQL_PWD' not in report
