from dataclasses import replace
import sqlite3
import subprocess
from unittest.mock import Mock

import pytest

from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.domain.preflight import ForeignKey
from app.shared.database.mysql_client import MySqlClient
from app.shared.database.preflight import MySqlPreflightReader


@pytest.fixture
def config():
    return DatabaseConfig('invalid', 3306, 'test', 'tester', Environment.TEST)


@pytest.mark.parametrize('composite', [False, True])
def test_generated_count_query_null_and_composite_semantics(config, monkeypatch, composite):
    # Execute the generated standard NOT EXISTS expression locally, never a MySQL server.
    connection = sqlite3.connect(':memory:')
    connection.execute("ATTACH DATABASE ':memory:' AS test")
    connection.execute('CREATE TABLE test.child(a int, b int)')
    connection.execute('CREATE TABLE test.parent(a int, b int)')
    connection.executemany('INSERT INTO test.parent VALUES (?,?)', [(1, 1), (2, 2)])
    connection.executemany('INSERT INTO test.child VALUES (?,?)',
                           [(1, 1), (1, 2), (3, 3), (None, 9), (9, None), (None, None)])
    calls = []
    def read(command, **kwargs):
        sql = command[-1]
        calls.append(sql)
        assert sql.startswith('SELECT COUNT(*)')
        assert 'secret' not in command
        assert kwargs['env']['MYSQL_PWD'] == 'secret'
        count = connection.execute(sql).fetchone()[0]
        return subprocess.CompletedProcess(command, 0, str(count), '')
    monkeypatch.setattr(subprocess, 'run', read)
    columns = ('a', 'b') if composite else ('a',)
    fk = ForeignKey('fk', 'child', columns, 'parent', columns)
    try:
        assert MySqlPreflightReader(MySqlClient()).count_orphans(config, 'secret', fk) == 2
        assert 'c.`a` IS NOT NULL' in calls[0]
        if composite:
            assert 'c.`b` IS NOT NULL' in calls[0]
            assert 'p.`a` = c.`a` AND p.`b` = c.`b`' in calls[0]
    finally:
        connection.close()


def encode_row(child, name, child_col, parent, parent_col, ordinal, schema='test'):
    values = [schema, child, name, child_col, 'test', parent, parent_col]
    return '\t'.join([v.encode().hex() for v in values] + [str(ordinal)])


def test_metadata_both_directions_all_relations_and_composite(config, monkeypatch):
    rows = [encode_row('a', 'fk1', 'x', 'parent', 'id', 1),
            encode_row('a', 'fk1', 'y', 'parent', 'other', 2),
            encode_row('b', 'fk2', 'z', 'parent', 'id', 1),
            encode_row('parent', 'out', 'id', 'root', 'key', 1)]
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '\n'.join(rows), ''))
    monkeypatch.setattr(subprocess, 'run', run)
    result = MySqlPreflightReader(MySqlClient()).get_foreign_keys(config, '', 'parent', 'id')
    assert [fk.constraint_name for fk in result] == ['fk1', 'fk2', 'out']
    assert result[0].child_columns == ('x', 'y')
    assert result[0].parent_columns == ('id', 'other')
    sql = run.call_args.args[0][-1]
    assert sql.startswith('SELECT ')
    assert 'information_schema.KEY_COLUMN_USAGE' in sql
    assert 'REFERENCED_TABLE_SCHEMA =' in sql and 'TABLE_SCHEMA =' in sql
    assert "CONVERT(X'74657374' USING utf8mb4)" in sql


def test_identifier_escaping_and_metadata_literal(config, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '0', ''))
    monkeypatch.setattr(subprocess, 'run', run)
    reader = MySqlPreflightReader(MySqlClient())
    fk = ForeignKey('f', 'c`; DROP TABLE p; --', ('i`d',), 'p', ('id',))
    reader.count_orphans(config, '', fk)
    sql = run.call_args.args[0][-1]
    assert '`test`.`c``; DROP TABLE p; --`' in sql
    assert 'c.`i``d` IS NOT NULL' in sql
    run.return_value = subprocess.CompletedProcess([], 0, '', '')
    reader.get_foreign_keys(config, '', "x' OR 1=1 --", 'id')
    assert "x' OR 1=1" not in run.call_args.args[0][-1]


@pytest.mark.parametrize('method', ['test_connection', 'count_orphans', 'get_foreign_keys'])
def test_reader_rejects_production_without_subprocess(config, monkeypatch, method):
    run = Mock(side_effect=AssertionError('No process allowed'))
    monkeypatch.setattr(subprocess, 'run', run)
    reader = MySqlPreflightReader(MySqlClient())
    args = [] if method == 'test_connection' else (
        [ForeignKey('fk', 'c', ('id',), 'p', ('id',))] if method == 'count_orphans' else ['c', 'id'])
    with pytest.raises(ValueError, match='TEST'):
        getattr(reader, method)(replace(config, environment=Environment.PRODUCTION), '', *args)
    run.assert_not_called()


def test_external_schema_never_queried(config, monkeypatch):
    run = Mock()
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(ValueError, match='schema'):
        MySqlPreflightReader(MySqlClient()).count_orphans(
            config, '', ForeignKey('f', 'c', ('id',), 'p', ('id',), parent_schema='production'))
    run.assert_not_called()


@pytest.mark.parametrize('output', ['invalid', '61\t62', encode_row('c', 'f', 'id', 'p', 'id', 2)])
def test_malformed_metadata_raises(config, monkeypatch, output):
    monkeypatch.setattr(subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 0, output, '')))
    with pytest.raises(ValueError):
        MySqlPreflightReader(MySqlClient()).get_foreign_keys(config, '', 'c', 'id')


def test_query_failure_raises(config, monkeypatch):
    monkeypatch.setattr(subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 1, '', 'denied')))
    with pytest.raises(RuntimeError, match='denied'):
        MySqlPreflightReader(MySqlClient()).get_foreign_keys(config, '', 'c', 'id')
