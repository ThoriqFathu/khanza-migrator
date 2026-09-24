from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.domains.migration.domain.preflight import MigrationPreflightResult, PreflightStatus


def preflight_summary(result: MigrationPreflightResult) -> str:
    return (f"Statements checked: {result.total_statements} | FK candidates: {result.fk_candidates}\n"
            f"FK safe: {result.safe_fk_count} | FK orphan problems: {result.orphan_fk_count}\n"
            f"Column modifications: {result.column_modifications} | FK dependency issues: {result.dependency_issue_count}\n"
            f"Audit errors: {result.audit_error_count}")


class PreflightDialog(QDialog):
    def __init__(self, result: MigrationPreflightResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Migration Preflight")
        self.resize(1100, 560)
        layout = QVBoxLayout(self)
        heading = QLabel(f"TEST: {result.database}\nFile: {result.migration_file}\n{preflight_summary(result)}")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        note = QLabel("Hasil berdasarkan snapshot TEST saat audit, bukan simulasi seluruh migration. "
                      "SAFE hanya untuk pemeriksaan ini. Perubahan data/schema sebelumnya dapat mengubah hasil. "
                      "Review semua BLOCKER/ERROR sebelum menjalankan migration.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Status', 'Type', 'Statement', 'Object', 'Constraint', 'Problem'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        order = {PreflightStatus.BLOCKER: 0, PreflightStatus.ERROR: 1, PreflightStatus.SAFE: 2}
        rows = sorted(result.findings, key=lambda f: (order[f.status], f.statement_sequence))
        self.table.setRowCount(len(rows))
        for row, finding in enumerate(rows):
            values = [finding.status.value, finding.issue_type.value, f'#{finding.statement_sequence}',
                      finding.object_name, finding.constraint_name, finding.problem]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{finding.problem}\n\n{finding.original_sql}")
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
