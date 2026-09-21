"""Qt application startup and event loop."""

from PySide6.QtWidgets import QApplication

from app.bootstrap import create_main_window
from app.shared.logging.logger import configure_logging


def run_app() -> None:
    configure_logging()
    app = QApplication([])
    window = create_main_window()
    window.show()
    app.exec()
