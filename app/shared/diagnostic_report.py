"""Markdown rendering only: no Qt, filesystem access, or execution."""
from datetime import datetime, timezone
import re

from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult


CASCADE_NOTE = (
    "Diagnostic Migration continues after statement failures.\n"
    "A failure may therefore be a secondary/cascade failure caused by an earlier unsuccessful statement.\n"
    "The report records actual database execution results and does not determine root cause."
)
DIRTY_NOTE = "TEST database has been modified. Reset/Restore TEST before running PRE Migration."


def _fence(text: str, language: str = 'text') -> str:
    # SQL/errors can contain Markdown fences. Preserve their actual text safely.
    width = max([2, *(len(m.group()) for m in re.finditer(r'`+', text))]) + 1
    marker = '`' * width
    return f'{marker}{language}\n{text}\n{marker}'


def render_diagnostic_report(result: DiagnosticMigrationResult,
                             generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now(timezone.utc)
    parts = [
        '# Diagnostic Migration Report',
        _fence(f'Generated: {generated.isoformat()}\nStarted: {result.started_at.isoformat()}\n'
               f'Finished: {result.finished_at.isoformat()}\nEnvironment: TEST\n'
               f'Database: {result.database}\nMigration: {result.migration_name}\n'
               f'Foreign Key Validation: {"ENABLED" if result.foreign_key_checks else "SKIPPED"}'),
        '## Summary',
        f'Total Statements: {result.total_statements}\n\nSuccess: {result.success_count}\n\n'
        f'Failed: {result.failure_count}\n\nUnattempted: {result.unattempted_count}\n\n'
        f"Run: {'COMPLETED' if result.completed else 'ABORTED'}",
        '> Diagnostic Migration performs actual SQL execution against the TEST database.\n> ' + DIRTY_NOTE,
        '\n'.join('> ' + line for line in CASCADE_NOTE.splitlines()),
        '> Known connection-password occurrences are redacted; no root-cause analysis or recommendations are generated.',
    ]
    if result.fatal_error:
        parts.extend(['## Fatal diagnostic failure', _fence(result.fatal_error)])
    failures = [item for item in result.statements if not item.success]
    if not failures:
        parts.append('No statement failures recorded.')
    for number, item in enumerate(failures, 1):
        parts.extend([
            '---', f'## Failure {number}', f'Statement: #{item.sequence}',
            f'MySQL Error: {item.error_code if item.error_code is not None else "Unavailable"}',
            '### SQL', _fence(item.sql, 'sql'),
            '### Actual Error', _fence(item.error_message or ''),
        ])
    return '\n\n'.join(parts) + '\n'
