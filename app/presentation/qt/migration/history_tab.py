from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from app.shared.filesystem.history import LocalHistoryRepository

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
class HistoryTab(QWidget):
    def __init__(self, history: LocalHistoryRepository) -> None:
        super().__init__()
        self.history = history
        layout = QVBoxLayout(self)
        path = self.history.root
        label = QLabel(
            f"History lokal:\n{path}\n\n"
            "Setiap execution menyimpan migration.sql, result.json, dan metadata.json."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._show_history)
        clear = QPushButton("Hapus Semua Data")
        clear.clicked.connect(self._reset_all_data)
        layout.addWidget(refresh)
        layout.addWidget(clear)
        self.history_text = QPlainTextEdit()
        self.history_text.setReadOnly(True)
        layout.addWidget(self.history_text, 1)
        self._show_history()

    def _show_history(self) -> None:
        entries = sorted(self.history.root.rglob("result.json"), reverse=True)
        lines = []
        for item in entries[:100]:
            lines.append(str(item))
            try:
                import json
                data = json.loads(item.read_text(encoding="utf-8"))
                lines.append(
                    f"  {data['status']} | {data['database']} | "
                    f"success={data['success_count']} failed={data['failed_count']}"
                )
            except Exception:
                pass
        self.history_text.setPlainText("\n".join(lines) or "Belum ada execution history.")

    def _reset_all_data(self) -> None:
        backup_root = Path.home() / "khanza-migrator-backups"
        approval_file = self.history.approval_file

        answer = QMessageBox.warning(
            self,
            "Hapus Semua Data Lokal",
            (
                "Semua data lokal yang dibuat oleh Khanza Migrator akan dihapus:\n\n"
                "• Execution history\n"
                "• Production backup files\n"
                "• Migration approval\n\n"
                "Database TEST dan PRODUCTION TIDAK akan dihapus "
                "atau diubah oleh tindakan ini.\n\n"
                "Tindakan ini tidak dapat dibatalkan.\n\n"
                "Lanjutkan?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        try:
            # 1. Hapus execution history
            if self.history.root.exists():
                for item in self.history.root.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # 2. Hapus production backups
            if backup_root.exists():
                for item in backup_root.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # 3. Hapus approval
            if approval_file.exists():
                approval_file.unlink()

            # Refresh tampilan history
            self._show_history()

            QMessageBox.information(
                self,
                "Reset All Data",
                "Semua data lokal berhasil di-reset.",
            )

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Reset gagal",
                str(exc),
            )
