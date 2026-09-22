"""Construct concrete dependencies for the desktop application."""

from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.shared.filesystem.sessions import LocalSessionRepository

from app.presentation.qt.migration.main_window import MainWindow
from app.shared.database.backup import MySqlDumpBackupProvider
from app.shared.database.mysql_client import MySqlClient
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.sql.parser import SqlMigrationParser


def create_main_window() -> MainWindow:
    """Create the window after QApplication has been initialized."""
    db = MySqlClient()
    history = LocalHistoryRepository()
    parser = SqlMigrationParser()
    return MainWindow(
        db=db,
        backup_provider=MySqlDumpBackupProvider(),
        history=history,
        parser=parser,
        pre_migration=ResumablePreMigration(parser, db, history, LocalSessionRepository()),
    )
