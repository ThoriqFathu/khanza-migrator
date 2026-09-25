import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.domains.migration.domain.models import (
    Approval, BackupMetadata, MigrationExecutionResult
)


class LocalHistoryRepository:
    def __init__(self, root: Path | None = None):
        self.root = root or Path.home() / ".khanza-migrator" / "history"
        self.root.mkdir(parents=True, exist_ok=True)
        self.approval_file = self.root.parent / "approval.json"

    def save_execution(
        self,
        result: MigrationExecutionResult,
        migration_path: Path,
        backup: BackupMetadata | None = None,
    ) -> Path:
        timestamp = result.started_at.astimezone().strftime("%Y%m%d_%H%M%S")
        folder = self.root / result.started_at.strftime("%Y") / result.started_at.strftime("%m") / f"migration_{timestamp}"
        folder.mkdir(parents=True, exist_ok=True)

        (folder / "migration.sql").write_bytes(migration_path.read_bytes())

        payload = {
            "status": result.status.value,
            "migration_hash": result.migration_hash,
            "database": result.database,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "foreign_key_checks": result.foreign_key_checks,
            "statements": [
                {
                    "sequence": item.sequence,
                    "sql": item.sql,
                    "success": item.success,
                    "duration_ms": item.duration_ms,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in result.statements
            ],
        }
        (folder / "result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        metadata = {
            "migration_file": str(migration_path.resolve()),
            "migration_hash": result.migration_hash,
            "database": result.database,
            "status": result.status.value,
            "foreign_key_checks": result.foreign_key_checks,
            "backup": asdict(backup) if backup else None,
        }
        (folder / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return folder

    def save_approval(self, approval: Approval) -> Path:
        self.approval_file.write_text(
            json.dumps(asdict(approval), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return self.approval_file

    def load_approval(self) -> Approval | None:
        if not self.approval_file.is_file():
            return None
        data = json.loads(self.approval_file.read_text(encoding="utf-8"))
        approved_at = datetime.fromisoformat(data["approved_at"])
        return Approval(
            migration_name=data["migration_name"],
            migration_hash=data["migration_hash"],
            test_database=data["test_database"],
            test_backup=data["test_backup"],
            success_count=int(data["success_count"]),
            failed_count=int(data["failed_count"]),
            approved_at=approved_at,
            application_version=data["application_version"],
        )
