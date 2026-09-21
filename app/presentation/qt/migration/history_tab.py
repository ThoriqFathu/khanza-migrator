from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from app.shared.filesystem.history import LocalHistoryRepository


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
        refresh.clicked.connect(lambda: self._show_history())
        layout.addWidget(refresh)
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
