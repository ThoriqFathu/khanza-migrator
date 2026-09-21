from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget, QCheckBox
)

from app.domains.migration.application.ports import (
    BackupPort, DatabasePort, MigrationParserPort,
)
from app.domains.migration.application.use_cases import (
    CreateProductionBackupUseCase, RunFinalMigrationUseCase,
    RunMigrationUseCase, ValidateFinalMigrationUseCase
)
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.hashing.sha256 import sha256_file
from .history_tab import HistoryTab
from .pre_migration_tab import PreMigrationTab
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

        self.last_backup = None

        self._active_threads = []

        self.tabs = QTabWidget()
        self.pre_tab = PreMigrationTab(
            db=self.db,
            history=self.history,
            parser=self.parser,
            run_worker=self._run_worker,
            migration_selected=self._set_final_migration,
            update_final_state=self._update_final_state,
        )
        self.tabs.addTab(self.pre_tab, "Pre-Migration")
        self.tabs.addTab(self._final_tab(), "Final Migration")
        self.tabs.addTab(HistoryTab(self.history), "History")
        self.setCentralWidget(self.tabs)

        self._update_final_state()

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

    def _set_final_migration(self, path: str) -> None:
        self.final_migration.setText(path)

    def _browse_file(self, target: QLineEdit, filter_text: str):
        path, _ = QFileDialog.getOpenFileName(self, "Pilih file", "", filter_text)
        if path:
            target.setText(path)
            self._update_final_state()

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
    def _final_config(self):
        return DatabaseConfig(
            host=self.final_host.text().strip(),
            port=int(self.final_port.text()),
            database=self.final_database.text().strip(),
            username=self.final_username.text().strip(),
            environment=Environment.PRODUCTION,
        )

    def _update_final_state(self):
        migration = Path(self.final_migration.text() or self.pre_tab.migration_path())
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
