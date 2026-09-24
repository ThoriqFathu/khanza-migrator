from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal, Slot

from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app import config
from app.domains.migration.application.ports import DatabasePort, HistoryPort, MigrationParserPort, ProgressCallback
from app.domains.migration.application.use_cases import (
    ApproveMigrationUseCase, ResetTestDatabaseUseCase,
)
from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.domains.migration.domain.session import PreMigrationSession
from app.shared.sql.render import render_statements

from app.domains.migration.domain.enums import Environment, MigrationStatus
from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.application.migration_preflight import MigrationPreflight
from app.domains.migration.domain.preflight import MigrationPreflightResult
from .preflight_dialog import PreflightDialog, preflight_summary
from .diagnostic_panel import DiagnosticPanel
from app.domains.migration.application.diagnostic_migration import DiagnosticMigration


class PreMigrationTab(QWidget):
    preflight_completed = Signal(object)
    reset_completed = Signal(object)

    def __init__(
        self,
        pre_migration: ResumablePreMigration,
        db: DatabasePort,
        history: HistoryPort,
        parser: MigrationParserPort,
        run_worker: Callable[..., None],
        migration_selected: Callable[[str], None],
        update_final_state: Callable[[], None],
        preflight: MigrationPreflight | None = None,
        diagnostic: DiagnosticMigration | None = None,
    ) -> None:
        super().__init__()
        self.pre_migration_service = pre_migration
        self.session: PreMigrationSession | None = None
        self.db = db
        self.history = history
        self.parser = parser
        self._run_worker = run_worker
        self._migration_selected = migration_selected
        self._update_final_state = update_final_state
        self.last_pre_result = None
        self.preflight_completed.connect(self._preflight_finished, Qt.ConnectionType.QueuedConnection)
        self.reset_completed.connect(self._reset_finished, Qt.ConnectionType.QueuedConnection)
        self._fresh_test_target: DatabaseConfig | None = None
        self._diagnostic_target: DatabaseConfig | None = None
        self.diagnostic_service = diagnostic
        self.preflight_service = preflight
        self.preflight_result: MigrationPreflightResult | None = None
        self.preflight_dialog: PreflightDialog | None = None
        self._build_ui()
        self._refresh_session_controls()

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
        self.preflight_btn = QPushButton("Run Migration Preflight")
        self.run_pre_btn = QPushButton("Run Migration")
        self.approve_btn = QPushButton("Approve for Final Migration")
        self.test_connection_btn.clicked.connect(self._test_pre_connection)
        self.reset_btn.clicked.connect(self._reset_test)
        self.preflight_btn.clicked.connect(self._run_preflight)
        self.run_pre_btn.clicked.connect(self._run_pre)
        self.approve_btn.clicked.connect(self._approve)
        for b in [self.test_connection_btn, self.reset_btn, self.preflight_btn, self.run_pre_btn, self.approve_btn]:
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self.preflight_status = QLabel("Migration Preflight: jalankan setelah restore TEST, sebelum Run Migration.")
        self.preflight_status.setWordWrap(True)
        layout.addWidget(self.preflight_status)
        self.preflight_details_btn = QPushButton("Lihat Hasil Migration Preflight")
        self.preflight_details_btn.setEnabled(False)
        self.preflight_details_btn.clicked.connect(self._show_preflight)
        layout.addWidget(self.preflight_details_btn)
        for field in (self.pre_migration, self.pre_host, self.pre_port, self.pre_database,
                      self.pre_username, self.pre_password):
            field.textChanged.connect(self._clear_preflight)

        self.session_status = QLabel("SQL sukses dikunci; SQL tersisa dapat diedit setelah gagal. Resume tersedia selama aplikasi tetap terbuka.")
        self.session_status.setWordWrap(True)
        layout.addWidget(self.session_status)
        layout.addWidget(QLabel("SQL yang sudah berhasil (tidak dijalankan ulang)"))
        self.completed_sql = QPlainTextEdit()
        self.completed_sql.setReadOnly(True)
        self.completed_sql.setMaximumHeight(120)
        layout.addWidget(self.completed_sql)
        layout.addWidget(QLabel("SQL yang belum selesai — mulai dari statement gagal"))
        self.pending_sql = QPlainTextEdit()
        self.pending_sql.setPlaceholderText("Setelah gagal, perbaiki SQL di sini lalu klik Lanjutkan Migration.")
        self.pending_sql.setEnabled(False)
        layout.addWidget(self.pending_sql)
        self.pending_sql.textChanged.connect(self._pending_edited)
        self.output_path = QLineEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setPlaceholderText("Lokasi migration_final.sql setelah seluruh statement berhasil")
        layout.addWidget(self.output_path)

        self.pre_progress = QProgressBar()
        layout.addWidget(self.pre_progress)
        self.pre_status = QLabel("Status: belum ada test.")
        layout.addWidget(self.pre_status)

        self.pre_log = QPlainTextEdit()
        self.pre_log.setReadOnly(True)
        layout.addWidget(self.pre_log, 1)
        self.diagnostic_panel = DiagnosticPanel(
            self.diagnostic_service, self._diagnostic_inputs, self._run_worker,
            self.pre_log, self.pre_progress,
        )
        self.diagnostic_panel.started.connect(self._diagnostic_started)
        layout.insertWidget(layout.indexOf(self.session_status), self.diagnostic_panel)
        for field in (self.pre_host, self.pre_port, self.pre_database, self.pre_username):
            field.textChanged.connect(self._forget_fresh_test)

    @Slot()
    def _forget_fresh_test(self) -> None:
        self._fresh_test_target = None

    def _diagnostic_inputs(self) -> tuple[Path, DatabaseConfig, str]:
        if self.session is not None or self._diagnostic_target is not None:
            raise ValueError("TEST memiliki sesi PRE atau sudah DIRTY. Reset/Restore sebelum Diagnostic.")
        target = self._pre_config()
        if target.environment is not Environment.TEST:
            raise ValueError("Diagnostic hanya untuk TEST.")
        if target != self._fresh_test_target:
            raise ValueError("Lakukan Reset Test Database sampai berhasil pada target ini sebelum Diagnostic.")
        migration = Path(self.pre_migration.text())
        if not migration.is_file():
            raise ValueError("Pilih migration.sql terlebih dahulu.")
        return migration, target, self.pre_password.text()

    @Slot(object)
    def _diagnostic_started(self, target: DatabaseConfig) -> None:
        # Conservative: retain dirty state even on fatal errors; only successful
        # reset of this same target clears it. No PRE session/approval is created.
        self._diagnostic_target = target
        self._fresh_test_target = None
        self._clear_preflight()
        self._refresh_session_controls()
        self._set_pre_status("TEST DIRTY: Reset/Restore wajib sebelum PRE Migration.")


    @Slot()
    def _clear_preflight(self) -> None:
        self.preflight_result = None
        self.preflight_details_btn.setEnabled(False)
        self.preflight_status.setText("Migration Preflight: jalankan setelah restore TEST, sebelum Run Migration.")
        if self.preflight_dialog:
            self.preflight_dialog.close()

    def _run_preflight(self) -> None:
        if self.session is not None or self._diagnostic_target is not None or self.preflight_service is None:
            return
        try:
            target = self._pre_config()
            migration = Path(self.pre_migration.text())
            if not migration.is_file() or not target.database:
                raise ValueError("Pilih file migration dan database TEST hasil restore terlebih dahulu.")
        except ValueError as exc:
            QMessageBox.warning(self, "Migration Preflight", str(exc))
            return
        password = self.pre_password.text()
        service = self.preflight_service
        self._clear_preflight()
        self.preflight_status.setText("Migration Preflight sedang berjalan...")

        def analyze(progress: ProgressCallback) -> tuple[MigrationPreflightResult | None, str]:
            # Only input snapshots/service: no widget access on worker.
            try:
                return service.execute(migration, target, password, progress), ''
            except Exception as exc:
                return None, str(exc)

        self._run_worker(analyze, self.pre_log, self.pre_progress,
                         self.preflight_completed.emit, busy_button=self.window())

    @Slot(object)
    def _preflight_finished(self, outcome: tuple[MigrationPreflightResult | None, str]) -> None:
        result, error = outcome
        self.preflight_result = result
        self.preflight_details_btn.setEnabled(result is not None)
        if error:
            self.preflight_status.setText("Migration Preflight gagal: " + error)
            QMessageBox.critical(self, "Migration Preflight", error)
        elif result is not None:
            self.preflight_status.setText("Migration Preflight\n" + preflight_summary(result))

    @Slot()
    def _show_preflight(self) -> None:
        if self.preflight_result is not None:
            if self.preflight_dialog:
                self.preflight_dialog.close()
                self.preflight_dialog.deleteLater()
            self.preflight_dialog = PreflightDialog(self.preflight_result, self)
            self.preflight_dialog.show()

    def _browse_file(self, target: QLineEdit, filter_text: str) -> None:
        if self.session:
            QMessageBox.information(self, "Sesi aktif", "Reset TEST terlebih dahulu untuk mengganti file atau memulai sesi baru.")
            return
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
            "Setelah reset berhasil, semua sesi lokal beserta migration_final.sql di dalamnya akan dihapus.\n\n"
            "Lanjutkan?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self._fresh_test_target = None
        self._clear_preflight()
        # Semua akses QWidget dilakukan di GUI thread.
        config = self._pre_config()
        password = self.pre_password.text()

        print(">>> RESET TEST")
        print(f">>> database = {config.database}")
        print(f">>> backup = {backup}")

        use_case = ResetTestDatabaseUseCase(self.db)

        session_id = self.session.session_id if self.session else None
        if self.session:
            self.session.invalidated = True
        self.last_pre_result = None
        self._refresh_session_controls()
        service = self.pre_migration_service

        def reset(progress: ProgressCallback) -> DatabaseConfig:
            if session_id:
                try:
                    service.invalidate(session_id)
                except FileNotFoundError:
                    pass  # No checkpoint remains to invalidate; the UI session is already blocked.
            use_case.execute(config, password, backup, progress)
            service.clear_all_sessions()
            return config

        self._run_worker(reset, self.pre_log, self.pre_progress,
                         self.reset_completed.emit, busy_button=self.window())

    def _pending_edited(self) -> None:
        self.last_pre_result = None
        self.approve_btn.setEnabled(False)

    def _run_pre(self) -> None:
        if self._diagnostic_target is not None:
            QMessageBox.warning(self, "TEST DIRTY", "Reset/Restore TEST setelah Diagnostic sebelum PRE Migration.")
            return
        migration = Path(self.pre_migration.text())
        if self.session is None and not migration.is_file():
            QMessageBox.warning(self, "Migration", "Pilih migration.sql terlebih dahulu.")
            return
        try:
            target = self._pre_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Konfigurasi", str(exc))
            return
        if not target.database:
            QMessageBox.warning(self, "Konfigurasi", "Nama test database wajib diisi.")
            return
        if self.session and self.session.finished_at:
            QMessageBox.information(self, "Selesai", "Sesi sudah selesai. Gunakan migration_final.sql atau reset TEST untuk sesi baru.")
            return
        if self.session and self.session.failed:
            answer = QMessageBox.warning(
                self, "Lanjutkan dari statement gagal",
                "Statement sukses tidak akan dijalankan ulang. Pastikan database TEST belum di-reset atau diubah di luar aplikasi. "
                "Statement gagal mungkin telah mengubah sebagian data; sesuaikan SQL tersisa dengan kondisi database sebelum melanjutkan.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        password = self.pre_password.text()
        backup = self.pre_backup.text()
        current_session = self.session
        session_id = current_session.session_id if current_session else None
        edited = self.pending_sql.toPlainText() if self.session and self.session.pending else None
        self.last_pre_result = None
        self.approve_btn.setEnabled(False)
        service = self.pre_migration_service

        def execute(progress: ProgressCallback) -> tuple[PreMigrationSession | None, str]:
            session = current_session
            try:
                session = service.load(session_id) if session_id else service.start(migration, target, backup)
                session = service.run(session.session_id, target, password, edited, progress)
                return session, ""
            except Exception as exc:
                if session:
                    try:
                        session = service.load(session.session_id)
                    except Exception:
                        # Never guess which SQL ran when the checkpoint cannot be read.
                        session.in_flight = True
                return session, str(exc)

        self._clear_preflight()
        self._fresh_test_target = None
        self._run_worker(execute, self.pre_log, self.pre_progress,
                         self._session_finished, busy_button=self.window())

    def _session_finished(self, outcome: tuple[PreMigrationSession | None, str]) -> None:
        session, error = outcome
        previous = self.session
        self.session = session
        self.last_pre_result = None
        if session:
            self.completed_sql.setPlainText(render_statements(item.sql for item in session.completed))
            if not error or previous is None:
                self.pending_sql.setPlainText(render_statements(session.pending))
            self.session_status.setText(
                f"Sesi: {self.pre_migration_service.checkpoint_file(session)}\n"
                f"Berhasil: {len(session.completed)} | Belum selesai: {len(session.pending)}"
            )
            if session.failed:
                failed = session.failed
                self.pre_log.appendPlainText(
                    f"\nFAILED STATEMENT : #{failed.sequence}\nERROR CODE : {failed.error_code}"
                    f"\nERROR MESSAGE : {failed.error_message}\nSQL:\n{failed.sql}"
                )
                self._set_pre_status(f"✗ Gagal pada #{failed.sequence}. Edit SQL tersisa lalu lanjutkan.")
            if session.finished_at and not error:
                try:
                    self.last_pre_result = self.pre_migration_service.result(session)
                    path = str(self.pre_migration_service.output_file(session))
                    self.output_path.setText(path)
                    self._migration_selected(path)
                    self._update_final_state()
                    self._set_pre_status("✓ Seluruh migration berhasil. migration_final.sql siap di-approve.")
                except Exception as exc:
                    error = str(exc)
        self._refresh_session_controls()
        if error:
            self._set_pre_status("Operasi terhenti: " + error)
            QMessageBox.critical(self, "Sesi migration", error)

    def _refresh_session_controls(self) -> None:
        active = self.session is not None
        dirty = self._diagnostic_target is not None
        self.diagnostic_panel.run_btn.setEnabled(not active and not dirty and self.diagnostic_service is not None)
        self.preflight_btn.setEnabled(not active and not dirty and self.preflight_service is not None)
        for widget in (self.pre_migration, self.pre_backup, self.pre_host, self.pre_port,
                       self.pre_database, self.pre_username):
            widget.setEnabled(not active and not dirty)
        can_continue = active and not self.session.finished_at and not self.session.in_flight and not self.session.invalidated
        self.pending_sql.setEnabled(bool(can_continue and self.session.pending))
        self.run_pre_btn.setText("Lanjutkan Migration" if active else "Run Migration")
        self.run_pre_btn.setEnabled(not dirty and (not active or bool(can_continue)))
        self.approve_btn.setEnabled(not dirty and self.last_pre_result is not None)

    @Slot(object)
    def _reset_finished(self, target: DatabaseConfig) -> None:
        self._fresh_test_target = target
        if target == self._diagnostic_target:
            self._diagnostic_target = None
            self.diagnostic_panel.summary.setText(
                "TEST telah di-reset/restore. Hasil diagnostic sebelumnya tetap tersedia untuk export."
            )
        self.session = None
        self.last_pre_result = None
        self.completed_sql.clear()
        self.pending_sql.clear()
        self.output_path.clear()
        self.session_status.setText("Database di-reset dan semua sesi dihapus. Run Migration akan memulai sesi baru.")
        self._refresh_session_controls()
        self._migration_selected(self.pre_migration.text())
        self._update_final_state()
        self._set_pre_status("✓ Test database READY")

    def _approve(self):
        if self._diagnostic_target is not None:
            QMessageBox.warning(self, "TEST DIRTY", "Diagnostic tidak dapat di-approve. Reset/Restore lalu jalankan PRE.")
            return
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
                Path(self.output_path.text()),
                self.session.target.database,
                self.session.test_backup,
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

