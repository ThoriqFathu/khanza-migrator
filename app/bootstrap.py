"""Construct concrete dependencies for the desktop application."""

from app.presentation.qt.migration.main_window import MainWindow
from app.shared.database.backup import MySqlDumpBackupProvider
from app.shared.database.mysql_client import MySqlClient
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.sql.parser import SqlMigrationParser


def create_main_window() -> MainWindow:
    """Create the window after QApplication has been initialized."""
    return MainWindow(
        db=MySqlClient(),
        backup_provider=MySqlDumpBackupProvider(),
        history=LocalHistoryRepository(),
        parser=SqlMigrationParser(),
    )
