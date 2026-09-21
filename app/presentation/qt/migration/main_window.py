from PySide6.QtCore import QThread, Signal, Slot

from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from app.domains.migration.application.ports import (
    BackupPort, DatabasePort, MigrationParserPort,
)
from app.shared.filesystem.history import LocalHistoryRepository
from .final_migration_tab import FinalMigrationTab
from .history_tab import HistoryTab
from .pre_migration_tab import PreMigrationTab
from .workers import Worker


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
        self.final_tab = FinalMigrationTab(
            db=self.db,
            backup_provider=self.backup_provider,
            history=self.history,
            parser=self.parser,
            run_worker=self._run_worker,
            pre_migration_path=self.pre_tab.migration_path,
        )
        self.tabs.addTab(self.final_tab, "Final Migration")
        self.tabs.addTab(HistoryTab(self.history), "History")
        self.setCentralWidget(self.tabs)

        self._update_final_state()

    def _set_final_migration(self, path: str) -> None:
        self.final_tab._set_final_migration(path)

    def _update_final_state(self) -> None:
        self.final_tab._update_final_state()

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
