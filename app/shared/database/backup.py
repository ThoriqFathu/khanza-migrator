import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.domains.migration.domain.models import BackupMetadata, DatabaseConfig


class MySqlDumpBackupProvider:
    def create_backup(
        self,
        config: DatabaseConfig,
        password: str,
        output_file: Path,
        migration_hash: str,
    ) -> BackupMetadata:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "mysqldump",
            "--host", config.host,
            "--port", str(config.port),
            "--user", config.username,
            "--protocol=tcp",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--databases", config.database,
        ]

        with output_file.open("wb") as output:
            result = subprocess.run(
                command,
                env={**os.environ, "MYSQL_PWD": password},
                stdout=output,
                stderr=subprocess.PIPE,
            )

        if result.returncode != 0:
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            raise RuntimeError(
                result.stderr.decode(errors="replace").strip() or "mysqldump gagal."
            )

        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError("Backup selesai tetapi file kosong.")

        return BackupMetadata(
            database=config.database,
            host=config.host,
            created_at=datetime.now(timezone.utc),
            backup_file=str(output_file),
            file_size=output_file.stat().st_size,
            backup_sha256=self._sha256(output_file),
            migration_sha256=migration_hash,
            application_version="0.1.0",
        )

    def verify_backup(self, metadata: BackupMetadata) -> None:
        path = Path(metadata.backup_file)
        if not path.is_file():
            raise RuntimeError("File backup tidak ditemukan.")
        size = path.stat().st_size
        if size != metadata.file_size or size <= 0:
            raise RuntimeError("Ukuran backup berubah atau tidak valid.")
        digest = self._sha256(path)
        if digest != metadata.backup_sha256:
            raise RuntimeError("SHA-256 backup tidak cocok.")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
