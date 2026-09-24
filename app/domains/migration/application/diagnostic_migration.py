"""Actual TEST execution, continue after SQL errors; no PRE/session/history writes."""
from datetime import datetime, timezone
from pathlib import Path
import re
from time import perf_counter

from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig, StatementResult
from .ports import DatabasePort, MigrationParserPort, ProgressCallback


def redact_diagnostic_text(text: str, password: str) -> str:
    """Keep actual text except occurrences of the connection secret we know."""
    return text.replace(password, '[REDACTED]') if password else text


def diagnostic_error_text(exc: Exception, password: str) -> str:
    # CLI stderr preserves whitespace and server detail; don't stringify a command
    # invocation when a subprocess exception exposes its stderr separately.
    raw = getattr(exc, 'stderr', None)
    if not isinstance(raw, str) or not raw:
        raw = str(exc)
    return redact_diagnostic_text(raw, password)


class DiagnosticMigration:
    def __init__(self, parser: MigrationParserPort, db: DatabasePort) -> None:
        self.parser = parser
        self.db = db

    def execute(self, migration: Path, target: DatabaseConfig, password: str,
                progress: ProgressCallback | None = None) -> DiagnosticMigrationResult:
        if target.environment is not Environment.TEST:
            raise ValueError("Diagnostic Migration hanya diizinkan pada environment TEST.")
        if not target.database:
            raise ValueError("Nama database TEST wajib diisi.")
        started = datetime.now(timezone.utc)
        try:
            parsed = self.parser.parse_file(migration)
            # Match ResumablePreMigration._parse_pending: omit comment-only tails,
            # then number actual executions from 1 like the initial PRE session.
            # Statement splitting remains entirely the existing parser's job.
            comment_only = r"(?:\s|--[^\n]*(?:\n|$)|#[^\n]*(?:\n|$)|/\*.*?\*/)*"
            statements = [s for s in parsed if not re.fullmatch(comment_only, s.sql, flags=re.S)]
            if not statements:
                raise ValueError("Migration tidak berisi statement SQL.")
            self.db.test_connection(target, password)
        except Exception as exc:
            raise RuntimeError(diagnostic_error_text(exc, password)) from None

        result = DiagnosticMigrationResult(started, started,
            redact_diagnostic_text(target.database, password),
            redact_diagnostic_text(migration.name, password), len(statements))
        for sequence, statement in enumerate(statements, 1):
            if progress:
                progress(f"Diagnostic migration {sequence}/{len(statements)}", sequence - 1, len(statements))
            begin = perf_counter()
            failure = ''
            code = None
            success = False
            try:
                # No rewrite, retry, FK-check override, or rollback.
                self.db.execute(target, password, statement.sql)
                success = True
            except Exception as exc:
                failure = diagnostic_error_text(exc, password)
                if isinstance(exc, OSError):
                    # Missing executable/OS execution failure: keep the partial report.
                    # OSError.errno is NOT a MySQL error number.
                    result.fatal_error = failure or "Execution infrastructure unavailable"
                else:
                    available_code = getattr(exc, 'errno', None)
                    code = available_code if type(available_code) is int else None
            result.statements.append(StatementResult(
                sequence, redact_diagnostic_text(statement.sql, password), success,
                (perf_counter() - begin) * 1000, code, failure or None,
            ))
            if progress:
                progress(f"Diagnostic {sequence}/{len(statements)} — {'SUCCESS' if success else 'FAILED'}",
                         sequence, len(statements))
            if result.fatal_error:
                break
        result.finished_at = datetime.now(timezone.utc)
        return result
