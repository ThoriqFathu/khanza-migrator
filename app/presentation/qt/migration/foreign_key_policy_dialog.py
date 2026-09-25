"""Explicit per-run choice for foreign-key validation."""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout, QWidget,
)


class ForeignKeyPolicyDialog(QDialog):
    def __init__(self, parent: QWidget, *, production: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Foreign Key Validation")
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Pilih bagaimana FOREIGN KEY divalidasi selama migration. "
            "Pilihan ini hanya berlaku untuk run ini."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.validate_radio = QRadioButton(
            "Validate Foreign Keys — FOREIGN_KEY_CHECKS = 1 (recommended)"
        )
        self.validate_radio.setChecked(True)
        self.skip_radio = QRadioButton(
            "Skip Foreign Key Validation — FOREIGN_KEY_CHECKS = 0"
        )
        layout.addWidget(self.validate_radio)
        layout.addWidget(self.skip_radio)

        warning = QLabel(
            "Skip memungkinkan FOREIGN KEY dibuat walaupun data existing masih orphan. "
            "Orphan tidak diperbaiki otomatis."
            + (
                "\n\nPRODUCTION: gunakan Skip hanya setelah orphan sudah diaudit dan keputusan ini disengaja."
                if production else ""
            )
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText(
            "Continue" if not production else "Continue to Production Confirmation"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def foreign_key_checks(self) -> bool:
        return self.validate_radio.isChecked()


def ask_foreign_key_checks(parent: QWidget, *, production: bool = False) -> bool | None:
    dialog = ForeignKeyPolicyDialog(parent, production=production)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.foreign_key_checks
