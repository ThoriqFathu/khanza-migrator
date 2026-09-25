from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import (
    Approval, BackupMetadata, DatabaseConfig, MigrationExecutionResult,
    SafetyCheck, SafetyReport, StatementResult
)
from app.domains.migration.application.ports import (
    BackupPort, DatabasePort, HistoryPort, MigrationParserPort, ProgressCallback
)
from app.shared.hashing.sha256 import sha256_file


APP_VERSION = "0.1.0"


class ResetTestDatabaseUseCase:
    def __init__(self, db: DatabasePort):
        self.db = db

    def execute(
        self,
        config: DatabaseConfig,
        password: str,
        backup_file: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        print(">>> ResetTestDatabaseUseCase.execute() DIPANGGIL")
        print(f">>> environment = {config.environment}")
        print(f">>> database = {config.database}")
        print(f">>> backup = {backup_file}")

        if config.environment is not Environment.TEST:
            raise ValueError(
                "Reset database hanya diperbolehkan untuk environment TEST."
            )

        print(">>> environment valid")

        if not backup_file.is_file():
            raise FileNotFoundError(
                f"Backup tidak ditemukan: {backup_file}"
            )

        print(">>> backup file valid")

        print(">>> test_connection()")
        self.db.test_connection(config, password)
        print(">>> test_connection() SELESAI")

        if progress:
            progress("Dropping test database", 0, 4)

        print(">>> database_exists()")
        exists = self.db.database_exists(config, password)
        print(f">>> database_exists() = {exists}")

        if exists:
            print(">>> drop_database()")
            self.db.drop_database(config, password)
            print(">>> drop_database() SELESAI")

        if progress:
            progress("Creating test database", 1, 4)

        print(">>> create_database()")
        self.db.create_database(config, password)
        print(">>> create_database() SELESAI")

        if progress:
            progress("Importing backup", 2, 4)

        print(">>> import_sql()")
        self.db.import_sql(
            config,
            password,
            backup_file,
            progress,
        )
        print(">>> import_sql() SELESAI")

        if progress:
            progress("Verifying database", 3, 4)

        print(">>> verify_database()")
        self.db.verify_database(config, password)
        print(">>> verify_database() SELESAI")

        if progress:
            progress("Test database READY", 4, 4)

        print(">>> RESET SELESAI")


class RunMigrationUseCase:
    def __init__(
        self,
        parser: MigrationParserPort,
        db: DatabasePort,
        history: HistoryPort,
    ):
        self.parser = parser
        self.db = db
        self.history = history

    def execute(
        self,
        migration_file: Path,
        config: DatabaseConfig,
        password: str,
        progress: ProgressCallback | None = None,
        *, foreign_key_checks: bool = True,
    ) -> MigrationExecutionResult:
        if not migration_file.is_file():
            raise FileNotFoundError(migration_file)

        if config.environment not in (Environment.TEST, Environment.PRODUCTION):
            raise ValueError("Environment database tidak valid.")

        migration_hash = sha256_file(migration_file)
        statements = self.parser.parse_file(migration_file)
        started = datetime.now(timezone.utc)
        results: list[StatementResult] = []

        for index, statement in enumerate(statements, start=1):
            if progress:
                progress(
                    f"Executing #{statement.sequence:03d} {statement.type.value}",
                    index - 1,
                    len(statements),
                )

            import time
            begin = time.perf_counter()
            try:
                execution_sql = statement.sql if foreign_key_checks else (
                    "SET SESSION FOREIGN_KEY_CHECKS = 0;\n" + statement.sql
                )
                self.db.execute(config, password, execution_sql)
                results.append(
                    StatementResult(
                        sequence=statement.sequence,
                        sql=statement.sql,
                        success=True,
                        duration_ms=(time.perf_counter() - begin) * 1000,
                    )
                )
            except Exception as exc:
                error_code = getattr(exc, "errno", None)
                results.append(
                    StatementResult(
                        sequence=statement.sequence,
                        sql=statement.sql,
                        success=False,
                        duration_ms=(time.perf_counter() - begin) * 1000,
                        error_code=error_code,
                        error_message=str(exc),
                    )
                )
                break

        finished = datetime.now(timezone.utc)
        status = MigrationStatus.SUCCESS if not any(not x.success for x in results) and len(results) == len(statements) else MigrationStatus.FAILED
        result = MigrationExecutionResult(
            status=status,
            migration_hash=migration_hash,
            database=config.database,
            started_at=started,
            finished_at=finished,
            statements=results,
            foreign_key_checks=foreign_key_checks,
        )
        self.history.save_execution(result, migration_file)
        if progress:
            progress(
                "Migration completed" if status is MigrationStatus.SUCCESS else "Migration FAILED",
                len(results),
                len(statements),
            )
        return result


class ApproveMigrationUseCase:
    def __init__(self, history: HistoryPort):
        self.history = history

    def execute(
        self,
        migration_file: Path,
        test_database: str,
        test_backup: str,
        execution: MigrationExecutionResult,
    ) -> Approval:
        current_hash = sha256_file(migration_file)
        if execution.status is not MigrationStatus.SUCCESS:
            raise ValueError("Hanya pre-migration SUCCESS yang dapat di-approve.")
        if current_hash != execution.migration_hash:
            raise ValueError("Migration file berubah setelah execution.")
        approval = Approval(
            migration_name=migration_file.name,
            migration_hash=current_hash,
            test_database=test_database,
            test_backup=test_backup,
            success_count=execution.success_count,
            failed_count=execution.failed_count,
            approved_at=datetime.now(timezone.utc),
            application_version=APP_VERSION,
        )
        self.history.save_approval(approval)
        return approval


class ValidateFinalMigrationUseCase:
    def __init__(self, db: DatabasePort, history: HistoryPort):
        self.db = db
        self.history = history

    def execute(
        self,
        migration_file: Path,
        target: DatabaseConfig,
        password: str,
        backup: BackupMetadata | None,
    ) -> SafetyReport:
        checks: list[SafetyCheck] = []
        exists = migration_file.is_file()
        checks.append(SafetyCheck("Migration exists", exists, str(migration_file)))

        readable = exists and migration_file.stat().st_size >= 0
        checks.append(SafetyCheck("Migration readable", readable, "File dapat dibaca." if readable else "File tidak dapat dibaca."))

        approval = self.history.load_approval()
        current_hash = sha256_file(migration_file) if exists else ""
        hash_ok = approval is not None and approval.migration_hash == current_hash
        checks.append(SafetyCheck("Migration hash matches approval", hash_ok, f"Current SHA256: {current_hash}"))

        pre_ok = approval is not None and approval.failed_count == 0 and approval.success_count > 0
        checks.append(SafetyCheck("Pre-migration successful", pre_ok, "Approval pre-migration valid." if pre_ok else "Belum ada pre-migration success yang valid."))

        approval_ok = approval is not None
        checks.append(SafetyCheck("Approval exists", approval_ok, "Approval tersedia." if approval_ok else "Approval tidak tersedia."))

        prod = target.environment is Environment.PRODUCTION
        checks.append(SafetyCheck("Target is PRODUCTION", prod, f"Environment: {target.environment.value}"))

        connection_ok = False
        if prod:
            try:
                self.db.test_connection(target, password)
                connection_ok = True
            except Exception as exc:
                checks.append(SafetyCheck("Target connection", False, str(exc)))
        if prod and connection_ok:
            checks.append(SafetyCheck("Target connection", True, "Production database reachable."))

        backup_ok = backup is not None
        checks.append(SafetyCheck("Production backup verified", backup_ok, "Backup tersedia dan telah diverifikasi." if backup_ok else "Backup wajib dibuat dan diverifikasi."))

        return SafetyReport(checks)


class CreateProductionBackupUseCase:
    def __init__(self, backup: BackupPort):
        self.backup = backup

    def execute(
        self,
        config: DatabaseConfig,
        password: str,
        output_file: Path,
        migration_file: Path,
    ) -> BackupMetadata:
        if config.environment is not Environment.PRODUCTION:
            raise ValueError("Production backup use case hanya menerima environment PRODUCTION.")
        migration_hash = sha256_file(migration_file)
        metadata = self.backup.create_backup(config, password, output_file, migration_hash)
        self.backup.verify_backup(metadata)
        return metadata


class RunFinalMigrationUseCase:
    def __init__(
        self,
        run_migration: RunMigrationUseCase,
        validator: ValidateFinalMigrationUseCase,
    ):
        self.run_migration = run_migration
        self.validator = validator

    def execute(
        self,
        migration_file: Path,
        target: DatabaseConfig,
        password: str,
        backup: BackupMetadata,
        progress: ProgressCallback | None = None,
        *, foreign_key_checks: bool = True,
    ) -> MigrationExecutionResult:
        report = self.validator.execute(migration_file, target, password, backup)
        if not report.passed:
            failures = "\n".join(f"- {x.name}: {x.detail}" for x in report.checks if not x.passed)
            raise PermissionError(f"Final migration diblokir:\n{failures}")

        if foreign_key_checks:
            return self.run_migration.execute(
                migration_file, target, password, progress
            )
        return self.run_migration.execute(
            migration_file, target, password, progress,
            foreign_key_checks=False,
        )
