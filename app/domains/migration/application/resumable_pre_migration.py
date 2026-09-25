from datetime import datetime, timezone
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

from app.domains.migration.application.ports import DatabasePort, HistoryPort, MigrationParserPort, ProgressCallback
from app.domains.migration.application.session_ports import SessionStorePort
from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import DatabaseConfig, MigrationExecutionResult, StatementResult
from app.domains.migration.domain.session import PreMigrationSession
from app.shared.hashing.sha256 import sha256_file
from app.shared.sql.render import render_statements


class ResumablePreMigration:
    """TEST-only execution with a durable successful prefix and editable pending SQL."""

    def __init__(self, parser: MigrationParserPort, db: DatabasePort,
                 history: HistoryPort, sessions: SessionStorePort) -> None:
        self.parser = parser
        self.db = db
        self.history = history
        self.sessions = sessions

    def start(self, migration: Path, target: DatabaseConfig, test_backup: str, *,
              foreign_key_checks: bool = True) -> PreMigrationSession:
        if target.environment is not Environment.TEST:
            raise ValueError("Sesi pre-migration hanya untuk TEST.")
        pending = self._parse_pending(migration)
        if not pending:
            raise ValueError("Migration tidak berisi statement SQL.")
        session = PreMigrationSession(
            uuid4().hex, str(migration.resolve()), test_backup,
            target, datetime.now(timezone.utc), pending,
            foreign_key_checks=foreign_key_checks,
        )
        self.sessions.save(session)
        return session

    def _parse_pending(self, path: Path) -> list[str]:
        # The legacy parser can return a trailing comment as a statement.
        comment_only = r"(?:\s|--[^\n]*(?:\n|$)|#[^\n]*(?:\n|$)|/\*.*?\*/)*"
        return [item.sql for item in self.parser.parse_file(path)
                if not re.fullmatch(comment_only, item.sql, flags=re.S)]

    def load(self, session_id: str) -> PreMigrationSession:
        return self.sessions.load(session_id)

    def clear_all_sessions(self) -> None:
        self.sessions.clear_all()

    def invalidate(self, session_id: str) -> None:
        session = self.load(session_id)
        session.invalidated = True
        self.sessions.save(session)

    def output_file(self, session: PreMigrationSession) -> Path:
        return self.sessions.file(session.session_id, "migration_final.sql")

    def checkpoint_file(self, session: PreMigrationSession) -> Path:
        return self.sessions.file(session.session_id, "session.json")

    def run(self, session_id: str, target: DatabaseConfig, password: str,
            edited_pending: str | None = None,
            progress: ProgressCallback | None = None, *,
            foreign_key_checks: bool = True) -> PreMigrationSession:
        session = self.load(session_id)
        if target.environment is not Environment.TEST or target != session.target:
            raise ValueError("Lanjutkan hanya pada konfigurasi database TEST sesi yang sama.")
        if session.invalidated:
            raise ValueError("Sesi dibatalkan karena reset database. Mulai sesi baru.")
        if session.in_flight:
            raise ValueError("Statement terakhir berstatus tidak pasti. Reset TEST dari backup sebelum memulai sesi baru.")
        if session.finished_at:
            return session
        if edited_pending is not None:
            path = self.sessions.write_sql(session_id, "pending.sql", edited_pending)
            pending = self._parse_pending(path)
            if not pending:
                raise ValueError("SQL yang belum selesai tidak boleh kosong.")
            session.pending = pending
        if session.foreign_key_checks != foreign_key_checks:
            raise ValueError(
                "Mode Foreign Key Validation harus sama selama satu sesi PRE. "
                "Reset/Restore TEST untuk memulai sesi dengan mode berbeda."
            )
        self.sessions.save(session)
        self.db.test_connection(target, password)
        session.failed = None
        self.sessions.save(session)
        while session.pending:
            sql = session.pending[0]
            sequence = len(session.completed) + 1
            if progress:
                progress(f"Executing #{sequence:03d}", sequence - 1,
                         len(session.completed) + len(session.pending))
            # Persist intent BEFORE touching the database. Interrupted execution must not auto-retry.
            session.in_flight = True
            self.sessions.save(session)
            begin = perf_counter()
            try:
                execution_sql = sql if foreign_key_checks else (
                    "SET SESSION FOREIGN_KEY_CHECKS = 0;\n" + sql
                )
                self.db.execute(target, password, execution_sql)
            except Exception as exc:
                session.in_flight = False
                session.failed = StatementResult(sequence, sql, False,
                    (perf_counter() - begin) * 1000, getattr(exc, "errno", None), str(exc))
                self.sessions.save(session)
                self._save_attempt(session)
                return session
            session.completed.append(StatementResult(sequence, sql, True, (perf_counter() - begin) * 1000))
            session.pending.pop(0)
            session.in_flight = False
            self.sessions.save(session)
        output = self.sessions.write_sql(session_id, "migration_final.sql",
                                        render_statements(item.sql for item in session.completed))
        # Verify export can be consumed by the same production parser without splitting routines.
        if [item.sql for item in self.parser.parse_file(output)] != [item.sql for item in session.completed]:
            raise ValueError("Export SQL tidak mempertahankan statement. File belum dapat di-approve.")
        session.output_hash = sha256_file(output)
        session.finished_at = datetime.now(timezone.utc)
        self.history.save_execution(self.result(session), output)
        self.sessions.save(session)
        if progress:
            progress("Migration selesai; migration_final.sql dibuat", len(session.completed), len(session.completed))
        return session

    def result(self, session: PreMigrationSession) -> MigrationExecutionResult:
        if not session.finished_at or not session.output_hash or session.pending:
            raise ValueError("Sesi belum selesai.")
        if sha256_file(self.output_file(session)) != session.output_hash:
            raise ValueError("migration_final.sql berubah setelah pengujian.")
        return MigrationExecutionResult(MigrationStatus.SUCCESS, session.output_hash,
            session.target.database, session.started_at, session.finished_at, list(session.completed),
            foreign_key_checks=session.foreign_key_checks)

    def _save_attempt(self, session: PreMigrationSession) -> None:
        path = self.sessions.write_sql(session.session_id, "attempt.sql", render_statements(
            [item.sql for item in session.completed] + session.pending))
        result = MigrationExecutionResult(MigrationStatus.FAILED, sha256_file(path), session.target.database,
            datetime.now(timezone.utc), datetime.now(timezone.utc),
            [*session.completed, session.failed] if session.failed else list(session.completed),
            foreign_key_checks=session.foreign_key_checks)
        self.history.save_execution(result, path)
