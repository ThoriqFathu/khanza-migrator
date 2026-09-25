"""Render reviewable SQL artifacts from Diagnostic Migration failures.

The audit artifact is read-only.  The cleanup artifact intentionally contains
executable DELETE statements, but only for parseable ERROR 1452 foreign-key
orphan failures.  Neither artifact is executed by Khanza Migrator.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
from app.domains.migration.domain.models import StatementResult


_IDENT = r"`(?:``|[^`])+`|[A-Za-z0-9_$]+"
_ADD_FK_RE = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?P<child>{_IDENT})\s+"
    rf"ADD\s+(?:CONSTRAINT\s+(?P<constraint>{_IDENT})\s+)?"
    rf"FOREIGN\s+KEY\s*\((?P<child_cols>[^)]+)\)\s+"
    rf"REFERENCES\s+(?P<parent>{_IDENT})\s*\((?P<parent_cols>[^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ForeignKeyOrphanSpec:
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]
    constraint_name: str | None = None


def _unquote_identifier(value: str) -> str:
    value = value.strip()
    if value.startswith('`') and value.endswith('`'):
        return value[1:-1].replace('``', '`')
    return value


def _quote_identifier(value: str) -> str:
    return '`' + value.replace('`', '``') + '`'


def _parse_column_list(value: str) -> tuple[str, ...] | None:
    parts = tuple(_unquote_identifier(part) for part in value.split(','))
    if not parts or any(not part or not re.fullmatch(r"[A-Za-z0-9_$]+", part) for part in parts):
        return None
    return parts


def parse_1452_foreign_key(statement: StatementResult) -> ForeignKeyOrphanSpec | None:
    """Return FK metadata only when a 1452 statement is safe to understand."""
    if statement.error_code != 1452:
        return None
    match = _ADD_FK_RE.search(statement.sql.rstrip().rstrip(';'))
    if not match:
        return None
    child_columns = _parse_column_list(match.group('child_cols'))
    parent_columns = _parse_column_list(match.group('parent_cols'))
    if not child_columns or not parent_columns or len(child_columns) != len(parent_columns):
        return None
    return ForeignKeyOrphanSpec(
        child_table=_unquote_identifier(match.group('child')),
        child_columns=child_columns,
        parent_table=_unquote_identifier(match.group('parent')),
        parent_columns=parent_columns,
        constraint_name=_unquote_identifier(match.group('constraint')) if match.group('constraint') else None,
    )


def _header(title: str) -> list[str]:
    return [
        '-- ============================================================',
        f'-- KHANZA MIGRATOR - {title}',
        '-- ============================================================',
        '-- Generated from Diagnostic Migration results.',
        '-- Khanza Migrator DOES NOT execute this file automatically.',
        '-- ============================================================',
        '',
    ]


def _section_lines(number: int, statement: StatementResult, spec: ForeignKeyOrphanSpec) -> list[str]:
    child = ', '.join(spec.child_columns)
    parent = ', '.join(spec.parent_columns)
    return [
        '-- ============================================================',
        f'-- ORPHAN #{number} | Diagnostic statement #{statement.sequence}',
        '-- MySQL Error : 1452',
        f'-- Constraint  : {spec.constraint_name or "(unnamed)"}',
        f'-- Child       : {spec.child_table}({child})',
        f'-- Parent      : {spec.parent_table}({parent})',
        '-- ============================================================',
    ]


def _orphan_predicate(spec: ForeignKeyOrphanSpec) -> str:
    non_null = ' AND\n  '.join(
        f'c.{_quote_identifier(column)} IS NOT NULL' for column in spec.child_columns
    )
    join = ' AND\n        '.join(
        f'p.{_quote_identifier(parent)} = c.{_quote_identifier(child)}'
        for child, parent in zip(spec.child_columns, spec.parent_columns)
    )
    return (
        f'{non_null}\n'
        '  AND NOT EXISTS (\n'
        f'      SELECT 1 FROM {_quote_identifier(spec.parent_table)} AS p\n'
        f'      WHERE {join}\n'
        '  )'
    )


def render_diagnostic_data_audit(result: DiagnosticMigrationResult) -> str:
    """Render read-only orphan inspection SQL for parseable 1452 failures."""
    lines = _header('DIAGNOSTIC DATA AUDIT (READ ONLY)')
    lines += [
        '-- Safe to run for inspection: this artifact contains SELECT only.',
        '-- The matching destructive proposals are in diagnostic_data_cleanup.sql.',
        '',
    ]
    count = 0
    for statement in result.statements:
        spec = parse_1452_foreign_key(statement)
        if spec is None:
            continue
        count += 1
        lines += _section_lines(count, statement, spec)
        child_cols = ', '.join(f'c.{_quote_identifier(c)}' for c in spec.child_columns)
        lines += [
            '-- Distinct orphan keys and row counts',
            f'SELECT {child_cols}, COUNT(*) AS `orphan_rows`',
            f'FROM {_quote_identifier(spec.child_table)} AS c',
            'WHERE ' + _orphan_predicate(spec),
            'GROUP BY ' + child_cols,
            'ORDER BY `orphan_rows` DESC;',
            '',
            '-- Complete child rows that currently violate the FK',
            'SELECT c.*',
            f'FROM {_quote_identifier(spec.child_table)} AS c',
            'WHERE ' + _orphan_predicate(spec) + ';',
            '',
        ]
    if count == 0:
        lines += ['-- No parseable ERROR 1452 orphan failures were found.', '']
    return '\n'.join(lines)


def render_diagnostic_data_cleanup(result: DiagnosticMigrationResult) -> str:
    """Render executable DELETE proposals for parseable 1452 orphan failures only."""
    lines = _header('DIAGNOSTIC DATA CLEANUP')
    lines += [
        '-- !!! DESTRUCTIVE SQL !!!',
        '-- REVIEW EVERY DELETE BEFORE EXECUTION.',
        '-- Remove any cleanup section you do not approve.',
        '-- Create a fresh database backup before manual execution.',
        '-- This file intentionally contains cleanup ONLY for ERROR 1452 orphan rows.',
        '',
    ]
    count = 0
    for statement in result.statements:
        spec = parse_1452_foreign_key(statement)
        if spec is None:
            continue
        count += 1
        lines += _section_lines(count, statement, spec)
        lines += [
            '-- ACTION: delete child rows whose referenced parent key does not exist.',
            f'DELETE c FROM {_quote_identifier(spec.child_table)} AS c',
            'WHERE ' + _orphan_predicate(spec) + ';',
            '',
        ]
    if count == 0:
        lines += ['-- No parseable ERROR 1452 orphan failures were found.', '']
    return '\n'.join(lines)
