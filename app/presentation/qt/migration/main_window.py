from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget, QProgressBar, QCheckBox
)

from app.domains.migration.application.ports import (
    BackupPort, DatabasePort, MigrationParserPort,
)
from app.domains.migration.application.use_cases import (
    ApproveMigrationUseCase, CreateProductionBackupUseCase,
    ResetTestDatabaseUseCase, RunFinalMigrationUseCase,
    RunMigrationUseCase, ValidateFinalMigrationUseCase
)
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.enums import MigrationStatus
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.hashing.sha256 import sha256_file
from .history_tab import HistoryTab
from .workers import Worker


from app import config

class MainWindow(QMainWindow):
    worker_progress = Signal(object, object, str, int, int)

    def __init__(
        self,
        db: DatabasePort,
        backup_provider: BackupPort,
        history: LocalHistoryRepository,
        parser: MigrationParserPort,
    ) -> None:
        super().__init__()

        self.worker_progress.connect(self._handle_worker_progress)
        self.setWindowTitle("Khanza Migrator")
        self.resize(1100, 720)

        self.db = db
        self.backup_provider = backup_provider
        self.history = history
        self.parser = parser

        self.last_pre_result = None
        self.last_backup = None

        self._active_threads = []

        self.tabs = QTabWidget()
        self.tabs.addTab(self._pre_tab(), "Pre-Migration")
        self.tabs.addTab(self._final_tab(), "Final Migration")
        self.tabs.addTab(HistoryTab(self.history), "History")
        self.setCentralWidget(self.tabs)

        self._update_final_state()

    def _pre_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

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

        return page

    def _final_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        box = QGroupBox("Production Migration")
        form = QFormLayout(box)
        self.final_migration = QLineEdit()
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda: self._browse_file(self.final_migration, "Migration SQL (*.sql)"))
        row = QHBoxLayout()
        row.addWidget(self.final_migration)
        row.addWidget(browse)
        form.addRow("Migration", row)

        self.final_host = QLineEdit(config.FINAL_DB_HOST)
        self.final_port = QLineEdit(str(config.FINAL_DB_PORT))
        self.final_database = QLineEdit(config.FINAL_DB_DATABASE)
        self.final_username = QLineEdit(config.FINAL_DB_USERNAME)

        self.final_password = QLineEdit(config.FINAL_DB_PASSWORD)
        self.final_password.setEchoMode(QLineEdit.Password)
        for label, widget in [
            ("Host", self.final_host), ("Port", self.final_port),
            ("Database", self.final_database), ("Username", self.final_username),
            ("Password", self.final_password)
        ]:
            form.addRow(label, widget)
        layout.addWidget(box)

        self.safety_labels = []
        safety_box = QGroupBox("Safety Gate")
        safety_layout = QVBoxLayout(safety_box)
        for _ in range(8):
            label = QLabel("○ belum diperiksa")
            self.safety_labels.append(label)
            safety_layout.addWidget(label)
        layout.addWidget(safety_box)

        actions = QHBoxLayout()
        self.validate_final_btn = QPushButton("Validate Pre-Flight")
        self.backup_btn = QPushButton("Create Production Backup")
        self.execute_final_btn = QPushButton("EXECUTE PRODUCTION MIGRATION")
        self.validate_final_btn.clicked.connect(self._validate_final)
        self.backup_btn.clicked.connect(self._create_backup)
        self.execute_final_btn.clicked.connect(self._execute_final)
        actions.addWidget(self.validate_final_btn)
        actions.addWidget(self.backup_btn)
        actions.addWidget(self.execute_final_btn)
        layout.addLayout(actions)

        self.final_status = QLabel("Final migration belum siap.")
        layout.addWidget(self.final_status)
        self.final_log = QPlainTextEdit()
        self.final_log.setReadOnly(True)
        layout.addWidget(self.final_log, 1)

        return page

    def _browse_file(self, target: QLineEdit, filter_text: str):
        path, _ = QFileDialog.getOpenFileName(self, "Pilih file", "", filter_text)
        if path:
            target.setText(path)
            if target is self.pre_migration:
                self.final_migration.setText(path)
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

    def _validate_final(self):
        try:
            report = ValidateFinalMigrationUseCase(self.db, self.history).execute(
                Path(self.final_migration.text()),
                self._final_config(),
                self.final_password.text(),
                self.last_backup,
            )
            for label, check in zip(self.safety_labels, report.checks):
                label.setText(("✓ " if check.passed else "✗ ") + check.name + " — " + check.detail)
            self.final_status.setText(
                "Safety gate: ✓ PASSED" if report.passed else "Safety gate: ✗ BLOCKED"
            )
            self.execute_final_btn.setEnabled(report.passed)
            return report.passed
        except Exception as exc:
            QMessageBox.critical(self, "Pre-flight gagal", str(exc))
            return False

    def _create_backup(self):
        migration = Path(self.final_migration.text())
        config = self._final_config()

        if config.environment is not Environment.PRODUCTION:
            QMessageBox.warning(
                self,
                "Safety",
                "Target final harus PRODUCTION.",
            )
            return

        if not migration.is_file():
            QMessageBox.warning(
                self,
                "Migration",
                "Pilih migration.sql terlebih dahulu.",
            )
            return

        default = Path.home() / "khanza-migrator-backups" / (
            f"{config.database}_before_migration_"
            f"{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        )

        default.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output, _ = QFileDialog.getSaveFileName(
            self,
            "Simpan production backup",
            str(default),
            "SQL Backup (*.sql)",
        )

        if not output:
            return

        output_file = Path(output)

        if output_file.exists():
            answer = QMessageBox.question(
                self,
                "File sudah ada",
                (
                    f"File backup berikut sudah ada:\n\n"
                    f"{output_file}\n\n"
                    "Apakah ingin menimpanya?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                return

        password = self.final_password.text()

        use_case = CreateProductionBackupUseCase(
            self.backup_provider
        )

        def create_backup(progress):
            progress(
                "Membuat FULL production backup (structure + data)...",
                0,
                0,
            )

            metadata = use_case.execute(
                config,
                password,
                output_file,
                migration,
            )

            progress(
                "Backup selesai dan berhasil diverifikasi.",
                1,
                1,
            )

            return metadata

        self._run_worker(
            create_backup,
            self.final_log,
            None,
            self._backup_finished,
            busy_button=self.backup_btn,
        )

    def _backup_finished(self, metadata):
        self.last_backup = metadata

        self.final_log.appendPlainText(
            ""
        )
        self.final_log.appendPlainText(
            "=========================================="
        )
        self.final_log.appendPlainText(
            "FULL BACKUP VERIFIED"
        )
        self.final_log.appendPlainText(
            f"File   : {metadata.backup_file}"
        )
        self.final_log.appendPlainText(
            f"Size   : {metadata.file_size:,} bytes"
        )
        self.final_log.appendPlainText(
            f"SHA256 : {metadata.backup_sha256}"
        )
        self.final_log.appendPlainText(
            "=========================================="
        )

        self._validate_final()

    def _execute_final(self):
        if not self._validate_final():
            return
        confirmation = QMessageBox.warning(
            self,
            "EXECUTE PRODUCTION MIGRATION",
            f"Anda akan menjalankan migration terhadap:\n\n"
            f"Environment: PRODUCTION\n"
            f"Host: {self.final_host.text()}\n"
            f"Database: {self.final_database.text()}\n"
            f"Migration: {Path(self.final_migration.text()).name}\n\n"
            "Pastikan backup sudah diverifikasi.\n"
            "Jika migration gagal, database dapat berada dalam kondisi partially modified.\n\n"
            "LANJUTKAN?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return

        run = RunMigrationUseCase(self.parser, self.db, self.history)
        validator = ValidateFinalMigrationUseCase(self.db, self.history)
        use_case = RunFinalMigrationUseCase(run, validator)

        self._run_worker(
            lambda progress: use_case.execute(
                Path(self.final_migration.text()),
                self._final_config(),
                self.final_password.text(),
                self.last_backup,
                progress,
            ),
            self.final_log,
            None,
            self._final_finished,
        )

    def _final_finished(self, result):
        if result.status.value == "SUCCESS":
            self.final_status.setText("FINAL MIGRATION COMPLETED")
            QMessageBox.information(self, "Completed", "Final migration selesai.")
        else:
            self.final_status.setText("FINAL MIGRATION FAILED")
            QMessageBox.critical(self, "Failed", "Final migration gagal. Tidak dilakukan automatic restore.")


    def _run_worker(
        self,
        function,
        log_widget,
        progress_bar,
        success_callback,
        busy_button=None,
    ):
        if busy_button:
            busy_button.setEnabled(False)

        thread = QThread(self)
        worker = Worker(function)

        self._active_threads.append((thread, worker))

        worker.moveToThread(thread)

        thread.started.connect(worker.run)

        # ============================================================
        # PROGRESS
        # ============================================================
        #
        # Worker thread TIDAK menyentuh QWidget secara langsung.
        #
        # Worker hanya mengirim data ke signal MainWindow.
        # Signal tersebut kemudian diterima oleh _handle_worker_progress()
        # di GUI thread.
        #
        def forward_progress(message, current, total):
            self.worker_progress.emit(
                log_widget,
                progress_bar,
                message,
                current,
                total,
            )

        worker.progress.connect(forward_progress)

        # ============================================================
        # SUCCESS
        # ============================================================

        worker.succeeded.connect(success_callback)

        # ============================================================
        # ERROR
        # ============================================================

        worker.failed.connect(
            lambda message: QMessageBox.critical(
                self,
                "Operasi gagal",
                message,
            )
        )

        # ============================================================
        # FINISH
        # ============================================================

        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)

        def cleanup():
            try:
                self._active_threads.remove((thread, worker))
            except ValueError:
                pass

            if busy_button:
                busy_button.setEnabled(True)

        thread.finished.connect(cleanup)
        thread.finished.connect(thread.deleteLater)

        thread.start()
    def _pre_config(self):
        return DatabaseConfig(
            host=self.pre_host.text().strip(),
            port=int(self.pre_port.text()),
            database=self.pre_database.text().strip(),
            username=self.pre_username.text().strip(),
            environment=Environment.TEST,
        )

    def _final_config(self):
        return DatabaseConfig(
            host=self.final_host.text().strip(),
            port=int(self.final_port.text()),
            database=self.final_database.text().strip(),
            username=self.final_username.text().strip(),
            environment=Environment.PRODUCTION,
        )

    def _set_pre_status(self, text):
        self.pre_status.setText(text)

    def _update_final_state(self):
        migration = Path(self.final_migration.text() or self.pre_migration.text())
        approval = self.history.load_approval()
        hash_ok = False
        if migration.is_file() and approval:
            hash_ok = sha256_file(migration) == approval.migration_hash
        self.execute_final_btn.setEnabled(False)
        self.final_status.setText(
            "Approval/hash valid. Buat dan verifikasi production backup untuk melanjutkan."
            if hash_ok else
            "Final migration terkunci sampai pre-migration success + approval + backup valid."
        )
    @Slot(object, object, str, int, int)
    def _handle_worker_progress(
        self,
        log_widget,
        progress_bar,
        message,
        current,
        total,
    ):
        log_widget.appendPlainText(message)

        if progress_bar and total:
            progress_bar.setMaximum(total)
            progress_bar.setValue(current)
