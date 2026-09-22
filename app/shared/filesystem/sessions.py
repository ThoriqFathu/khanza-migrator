import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig, StatementResult
from app.domains.migration.domain.session import PreMigrationSession


class LocalSessionRepository:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".khanza-migrator" / "sessions"

    def clear_all(self) -> None:
        if not self.root.exists():
            return
        for path in self.root.iterdir():
            if path.is_symlink() or not path.is_dir():
                path.unlink()
            else:
                shutil.rmtree(path)

    def file(self, session_id: str, name: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("ID sesi tidak valid.")
        if name not in {"session.json", "pending.sql", "attempt.sql", "migration_final.sql"}:
            raise ValueError("Nama file sesi tidak valid.")
        return self.root / session_id / name

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace atomically: never leave half-written JSON after an interrupted write.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, session: PreMigrationSession) -> None:
        payload = {"version": 1, **asdict(session)}
        self._write(self.file(session.session_id, "session.json"), json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    def load(self, session_id: str) -> PreMigrationSession:
        data = json.loads(self.file(session_id, "session.json").read_text(encoding="utf-8"))
        if data.pop("version") != 1 or data["session_id"] != session_id:
            raise ValueError("Format sesi tidak didukung.")
        target = data.pop("target")
        target["environment"] = Environment(target["environment"])
        data["started_at"] = datetime.fromisoformat(data["started_at"])
        if data["finished_at"]:
            data["finished_at"] = datetime.fromisoformat(data["finished_at"])
        data["completed"] = [StatementResult(**item) for item in data["completed"]]
        if data["failed"]:
            data["failed"] = StatementResult(**data["failed"])
        return PreMigrationSession(target=DatabaseConfig(**target), **data)

    def write_sql(self, session_id: str, name: str, sql: str) -> Path:
        path = self.file(session_id, name)
        self._write(path, sql)
        return path
