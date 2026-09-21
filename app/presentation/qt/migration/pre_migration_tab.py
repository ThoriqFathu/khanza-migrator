from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app import config
from app.domains.migration.application.ports import DatabasePort, HistoryPort, MigrationParserPort
from app.domains.migration.application.use_cases import (
    ApproveMigrationUseCase, ResetTestDatabaseUseCase, RunMigrationUseCase,
)
from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import DatabaseConfig


class PreMigrationTab(QWidget):
    def __init__(
        self,
        db: DatabasePort,
        history: HistoryPort,
        parser: MigrationParserPort,
        run_worker: Callable[..., None],
        migration_selected: Callable[[str], None],
        update_final_state: Callable[[], None],
    ) -> None:
        super().__init__()
        self.db = db
        self.history = history
        self.parser = parser
        self._run_worker = run_worker
        self._migration_selected = migration_selected
        self._update_final_state = update_final_state
        self.last_pre_result = None
        self._build_ui()

    def migration_path(self) -> str:
        return self.pre_migration.text()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        file_box = QGroupBox("Migration & Backup")
        form = QFormLayout(file_box)

        self.pre_migration = QLineEdit(config.PRE_MIGRATION_FILE)
        browse_migration = QPushButton("Browse")
        browse_migration.clicked.connect(lambda: self._browse_file(self.pre_migration, "Migration SQL (*.sql)"))
        row = QHBoxLayout()
        row.addWidget(self.pre_migration)
        row.addWidget(browse_migration)
        form.addRow("Migration File", row)

        self.pre_backup = QLineEdit(config.PRE_BACKUP_FILE)
        browse_backup = QPushButton("Browse")
        browse_backup.clicked.connect(lambda: self._browse_file(self.pre_backup, "SQL Backup (*.sql)"))
        row = QHBoxLayout()
        row.addWidget(self.pre_backup)
        row.addWidget(browse_backup)
        form.addRow("Production Backup", row)

        layout.addWidget(file_box)

        db_box = QGroupBox("Test Database")
        dbform = QFormLayout(db_box)
        self.pre_host = QLineEdit(config.PRE_DB_HOST)
        self.pre_port = QLineEdit(str(config.PRE_DB_PORT))
        self.pre_database = QLineEdit(config.PRE_DB_DATABASE)
        self.pre_username = QLineEdit(config.PRE_DB_USERNAME)

        self.pre_password = QLineEdit(config.PRE_DB_PASSWORD)
        self.pre_password.setEchoMode(QLineEdit.Password)

        for label, widget in [
            ("Host", self.pre_host), ("Port", self.pre_port),
            ("Database", self.pre_database), ("Username", self.pre_username),
            ("Password", self.pre_password)
        ]:
            dbform.addRow(label, widget)
        layout.addWidget(db_box)

        buttons = QHBoxLayout()
        self.test_connection_btn = QPushButton("Test Connection")
        self.reset_btn = QPushButton("Reset Test Database")
        self.run_pre_btn = QPushButton("Run Migration")
        self.approve_btn = QPushButton("Approve for Final Migration")
        self.test_connection_btn.clicked.connect(self._test_pre_connection)
        self.reset_btn.clicked.connect(self._reset_test)
        self.run_pre_btn.clicked.connect(self._run_pre)
        self.approve_btn.clicked.connect(self._approve)
        for b in [self.test_connection_btn, self.reset_btn, self.run_pre_btn, self.approve_btn]:
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self.pre_progress = QProgressBar()
        layout.addWidget(self.pre_progress)
        self.pre_status = QLabel("Status: belum ada test.")
        layout.addWidget(self.pre_status)

        self.pre_log = QPlainTextEdit()
        self.pre_log.setReadOnly(True)
        layout.addWidget(self.pre_log, 1)


    def _browse_file(self, target: QLineEdit, filter_text: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pilih file", "", filter_text)
        if path:
            target.setText(path)
            if target is self.pre_migration:
                self._migration_selected(path)
            self._update_final_state()

    def _test_pre_connection(self):
        config = self._pre_config()
        password = self.pre_password.text()

        if not config.database:
            QMessageBox.warning(
                self,
                "Invalid",
                "Nama test database wajib diisi.",
            )
            return

        try:
            exists = self.db.database_exists(config, password)

            if not exists:
                answer = QMessageBox.question(
                    self,
                    "Database belum ada",
                    (
                        f"Database TEST `{config.database}` belum ada.\n\n"
                        "Apakah ingin membuat database tersebut sekarang?"
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )

                if answer != QMessageBox.Yes:
                    self._set_pre_status(
                        f"Database `{config.database}` belum dibuat."
                    )
                    return

                self.db.create_database(config, password)

            self.db.test_connection(config, password)

            self._set_pre_status(
                "Status: ✓ Test database connection berhasil."
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Connection gagal",
                str(exc),
            )

    def _reset_test(self):
        migration = Path(self.pre_migration.text())
        backup = Path(self.pre_backup.text())

        if not self.pre_database.text():
            QMessageBox.warning(
                self,
                "Invalid",
                "Nama test database wajib diisi.",
            )
            return

        answer = QMessageBox.warning(
            self,
            "Reset TEST database",
            f"Database TEST `{self.pre_database.text()}` akan DROP dan CREATE ulang.\n\n"
            "Lanjutkan?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        # Semua akses QWidget dilakukan di GUI thread.
        config = self._pre_config()
        password = self.pre_password.text()

        print(">>> RESET TEST")
        print(f">>> database = {config.database}")
        print(f">>> backup = {backup}")

        use_case = ResetTestDatabaseUseCase(self.db)

        self._run_worker(
            lambda progress: use_case.execute(
                config,
                password,
                backup,
                progress,
            ),
            self.pre_log,
            self.pre_progress,
            lambda _: self._set_pre_status("✓ Test database READY"),
        )

    # def _run_pre(self):
    #     migration = Path(self.pre_migration.text())
    #     if not migration.is_file():
    #         QMessageBox.warning(self, "Migration", "Pilih migration.sql terlebih dahulu.")
    #         return

    #     use_case = RunMigrationUseCase(self.parser, self.db, self.history)
    #     self._run_worker(
    #         lambda progress: use_case.execute(
    #             migration, self._pre_config(), self.pre_password.text(), progress
    #         ),
    #         self.pre_log,
    #         self.pre_progress,
    #         self._pre_finished,
    #     )

    def _handle_pre_migration_result(self, result):
        # Simpan hasil PRE terbaru
        self.last_pre_result = result

        if result.status is MigrationStatus.SUCCESS:
            self._set_pre_status("✓ Migration berhasil")
            return

        self._set_pre_status("✗ Migration gagal")

        failed = next(
            (
                item
                for item in result.statements
                if not item.success
            ),
            None,
        )

        if failed:
            self.pre_log.appendPlainText("")
            self.pre_log.appendPlainText("=" * 40)
            self.pre_log.appendPlainText(
                f"FAILED STATEMENT : #{failed.sequence}"
            )
            self.pre_log.appendPlainText(
                f"ERROR CODE       : {failed.error_code}"
            )
            self.pre_log.appendPlainText(
                f"ERROR MESSAGE    : {failed.error_message}"
            )
            self.pre_log.appendPlainText("SQL:")
            self.pre_log.appendPlainText(failed.sql)
            self.pre_log.appendPlainText("=" * 40)
    def _run_pre(self):
        print(">>> _run_pre DIPANGGIL")
        self.last_pre_result = None
        migration = Path(self.pre_migration.text())

        if not migration.is_file():
            QMessageBox.warning(
                self,
                "Migration",
                "Pilih migration.sql terlebih dahulu.",
            )
            return

        config = self._pre_config()
        password = self.pre_password.text()

        print(">>> membuat RunMigrationUseCase")

        use_case = RunMigrationUseCase(
            self.parser,
            self.db,
            self.history,
        )

        self._run_worker(
            lambda progress: use_case.execute(
                migration,
                config,
                password,
                progress,
            ),
            self.pre_log,
            self.pre_progress,
            self._handle_pre_migration_result,
        )

    def _pre_finished(self, result):
        self.last_pre_result = result
        if result.status.value == "SUCCESS":
            self._set_pre_status(
                f"PRE-MIGRATION SUCCESS | {result.success_count} statement | "
                f"SHA256 {result.migration_hash}"
            )
        else:
            failed = result.failed_statement
            self._set_pre_status(
                f"PRE-MIGRATION FAILED | statement #{failed.sequence if failed else '?'}"
            )
            if failed:
                self.pre_log.appendPlainText(
                    f"\nFAILED SQL:\n{failed.sql}\n\nERROR:\n{failed.error_message}"
                )
        self._update_final_state()

    def _approve(self):
        if self.last_pre_result is None:
            QMessageBox.warning(
                self,
                "Approval",
                "Jalankan pre-migration terlebih dahulu.",
            )
            return

        if self.last_pre_result.status is not MigrationStatus.SUCCESS:
            QMessageBox.warning(
                self,
                "Approval",
                "Pre-migration belum berhasil. Migration tidak dapat di-approve.",
            )
            return

        try:
            approval = ApproveMigrationUseCase(self.history).execute(
                Path(self.pre_migration.text()),
                self.pre_database.text(),
                self.pre_backup.text(),
                self.last_pre_result,
            )

            self.pre_log.appendPlainText(
                f"\nAPPROVED\nHash: {approval.migration_hash}\n"
                f"Approved: {approval.approved_at}"
            )

            self._update_final_state()

            QMessageBox.information(
                self,
                "Approved",
                "Migration berhasil di-approve.",
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Approval gagal",
                str(exc),
            )

    def _pre_config(self) -> DatabaseConfig:
        return DatabaseConfig(
            host=self.pre_host.text().strip(),
            port=int(self.pre_port.text()),
            database=self.pre_database.text().strip(),
            username=self.pre_username.text().strip(),
            environment=Environment.TEST,
        )

    def _set_pre_status(self, text: str) -> None:
        self.pre_status.setText(text)

