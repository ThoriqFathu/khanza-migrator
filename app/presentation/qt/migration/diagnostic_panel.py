"""Focused diagnostic controls/results, sharing the existing window worker runner."""
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.domains.migration.application.diagnostic_migration import DiagnosticMigration, diagnostic_error_text
from app.domains.migration.application.ports import ProgressCallback
from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
from app.domains.migration.domain.models import DatabaseConfig
from app.shared.diagnostic_report import DIRTY_NOTE, render_diagnostic_report
from .foreign_key_policy_dialog import ask_foreign_key_checks
from app.shared.diagnostic_data_sql import (
    render_diagnostic_data_audit, render_diagnostic_data_cleanup,
)


def diagnostic_summary(result: DiagnosticMigrationResult) -> str:
    status = 'Completed' if result.completed else 'Aborted'
    return (f'Diagnostic Migration {status}\nDatabase: {result.database} | Statements: {result.total_statements} | '
            f'Success: {result.success_count} | Failed: {result.failure_count} | '
            f'Unattempted: {result.unattempted_count} | FK Validation: '
            f'{"ON" if result.foreign_key_checks else "SKIPPED"}')


class DiagnosticResultDialog(QDialog):
    def __init__(self, result: DiagnosticMigrationResult, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle('Diagnostic Migration — Actual Failures')
        self.resize(1000, 620)
        layout = QVBoxLayout(self)
        label = QLabel(diagnostic_summary(result) + '\n' + DIRTY_NOTE)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        if result.fatal_error:
            fatal = QPlainTextEdit(result.fatal_error)
            fatal.setReadOnly(True)
            fatal.setMaximumHeight(80)
            layout.addWidget(fatal)
        self.failures = [s for s in result.statements if not s.success]
        self.table = QTableWidget(len(self.failures), 3)
        self.table.setHorizontalHeaderLabels(['Statement', 'Error Code', 'Actual Error'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for row, item in enumerate(self.failures):
            for column, value in enumerate((f'#{item.sequence}',
                                           str(item.error_code) if item.error_code is not None else 'Unavailable',
                                           item.error_message or '')):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        layout.addWidget(QLabel('SQL lengkap dan actual error untuk failure terpilih:'))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail)
        self.table.currentCellChanged.connect(self._selection_changed)
        if self.failures:
            self.table.setCurrentCell(0, 0)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot(int, int, int, int)
    def _selection_changed(self, row: int, column: int, old_row: int, old_column: int) -> None:
        if 0 <= row < len(self.failures):
            failure = self.failures[row]
            self.detail.setPlainText(f'SQL:\n{failure.sql}\n\nActual Error:\n{failure.error_message or ""}')


class DiagnosticPanel(QWidget):
    started = Signal(object)
    completed = Signal(object)

    def __init__(self, service: DiagnosticMigration | None,
                 prepare: Callable[[], tuple[Path, DatabaseConfig, str]],
                 run_worker: Callable[..., None], log: QPlainTextEdit, progress: QProgressBar) -> None:
        super().__init__()
        self.service = service
        self._prepare = prepare
        self._run_worker = run_worker
        self._log = log
        self._progress = progress
        self.result: DiagnosticMigrationResult | None = None
        self.dialog: DiagnosticResultDialog | None = None
        self.completed.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        self.run_btn = QPushButton('Run Diagnostic Migration')
        self.details_btn = QPushButton('Lihat Actual Failures')
        self.export_btn = QPushButton('Export Diagnostic Report')
        self.run_btn.clicked.connect(self._run)
        self.details_btn.clicked.connect(self._show_details)
        self.export_btn.clicked.connect(self._export)
        self.run_btn.setEnabled(service is not None)
        self.details_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        for button in (self.run_btn, self.details_btn, self.export_btn):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.summary = QLabel('Diagnostic: actual TEST execution, continue-on-error. Reset/Restore TEST sebelum menjalankan.')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    @Slot()
    def _run(self) -> None:
        if self.service is None:
            return
        try:
            migration, target, password = self._prepare()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Diagnostic Migration', str(exc))
            return
        answer = QMessageBox.warning(
            self, 'Diagnostic Migration — TEST',
            f'Target TEST: {target.host}:{target.port}/{target.database}\n\n'
            'Diagnostic Migration akan menjalankan migration.sql secara nyata, '
            'mencatat actual MySQL/MariaDB errors, dan TETAP MELANJUTKAN jika statement gagal.\n\n'
            'Database TEST akan berubah. Tidak ada rollback atau auto-fix. '
            'Setelah run (termasuk ketika terhenti), wajib Reset/Restore TEST sebelum PRE Migration.\n\nLanjutkan?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        foreign_key_checks = ask_foreign_key_checks(self)
        if foreign_key_checks is None:
            return
        # Everything below passed to the worker is plain input/service, no QWidget.
        service = self.service
        self.started.emit(target)
        self.result = None
        self.details_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        if self.dialog:
            self.dialog.close()
        self.summary.setText('Diagnostic Migration berjalan. TEST dianggap DIRTY; Reset/Restore wajib sebelum PRE.')

        def execute(progress: ProgressCallback) -> tuple[DiagnosticMigrationResult | None, str]:
            try:
                return service.execute(
                    migration, target, password, progress,
                    foreign_key_checks=foreign_key_checks,
                ), ''
            except Exception as exc:
                return None, diagnostic_error_text(exc, password)

        self._run_worker(execute, self._log, self._progress, self.completed.emit, busy_button=self.window())

    @Slot(object)
    def _finished(self, outcome: tuple[DiagnosticMigrationResult | None, str]) -> None:
        self.result, error = outcome
        if self.result is not None:
            self.summary.setText(diagnostic_summary(self.result) + '\n' + DIRTY_NOTE)
            self.details_btn.setEnabled(True)
            self.export_btn.setEnabled(True)
        else:
            self.summary.setText('Diagnostic Migration gagal: ' + error + '\nReset/Restore TEST sebelum PRE.')
            QMessageBox.critical(self, 'Diagnostic Migration', error)

    @Slot()
    def _show_details(self) -> None:
        if self.result is None:
            return
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
        self.dialog = DiagnosticResultDialog(self.result, self)
        self.dialog.show()

    @Slot()
    def _export(self) -> None:
        result = self.result
        if result is None:
            return
        default = f'diagnostic_migration_{result.finished_at:%Y%m%d_%H%M%S}.md'
        filename, _ = QFileDialog.getSaveFileName(self, 'Export Diagnostic Report', default, 'Markdown (*.md)')
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != '.md':
            QMessageBox.warning(self, 'Export Diagnostic Report', 'Gunakan nama file dengan extension .md.')
            return
        audit_path = path.with_name(f'{path.stem}_data_audit.sql')
        cleanup_path = path.with_name(f'{path.stem}_data_cleanup.sql')
        try:
            path.write_text(render_diagnostic_report(result), encoding='utf-8')
            audit_path.write_text(render_diagnostic_data_audit(result), encoding='utf-8')
            cleanup_path.write_text(render_diagnostic_data_cleanup(result), encoding='utf-8')
        except OSError as exc:
            QMessageBox.critical(self, 'Export Diagnostic Report', str(exc))
            return
        QMessageBox.information(
            self, 'Export Diagnostic Report',
            'Diagnostic artifacts tersimpan:\n'
            f'• Report: {path}\n'
            f'• Data audit: {audit_path}\n'
            f'• Data cleanup: {cleanup_path}\n\n'
            'Cleanup hanya berisi DELETE untuk orphan ERROR 1452 dan tidak dieksekusi otomatis.'
        )
