"""Small offscreen regression for the resumable Pre-Migration workflow."""
import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.filesystem.sessions import LocalSessionRepository
from app.shared.sql.parser import SqlMigrationParser


@pytest.fixture(scope='module')
def qt_app():
    with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
        application = QApplication.instance() or QApplication([])
        yield application


def wait_for_worker(window) -> None:
    loop = QEventLoop()
    poll = QTimer()
    timeout = QTimer()
    timeout.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if not window._active_threads else None)
    timeout.timeout.connect(loop.quit)
    poll.start(10)
    timeout.start(8000)
    loop.exec()
    poll.stop()
    timeout.stop()
    assert not window._active_threads, 'Worker did not finish'
    assert window.isEnabled()


def test_editor_resume_export_approval_and_reset(qt_app, tmp_path: Path, monkeypatch) -> None:
    with patch('dotenv.load_dotenv', return_value=False):
        from app.presentation.qt.migration.main_window import MainWindow
        from app import config
    # Avoid user's .env or input paths even if config was imported by another test.
    for name in vars(config):
        if name.startswith(('PRE_', 'FINAL_')):
            monkeypatch.setattr(config, name, 3306 if name.endswith('_PORT') else '')
    source = tmp_path / 'input.sql'
    source.write_text('\n'.join(f'SELECT {i};' for i in range(1, 17)))
    backup = tmp_path / 'backup.sql'
    backup.write_text('-- backup')
    db = Mock()
    db.database_exists.return_value = True
    history = LocalHistoryRepository(tmp_path / 'history')
    service = ResumablePreMigration(SqlMigrationParser(), db, history,
                                    LocalSessionRepository(tmp_path / 'sessions'))
    window = MainWindow(db, Mock(), history, SqlMigrationParser(), service)
    monkeypatch.setattr(QMessageBox, 'warning', Mock(return_value=QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, 'information', Mock())
    critical = Mock()
    monkeypatch.setattr(QMessageBox, 'critical', critical)
    monkeypatch.setattr('subprocess.Popen', Mock(side_effect=AssertionError('Real database forbidden')))
    try:
        window.show()
        tab = window.pre_tab
        tab.pre_migration.setText(str(source))
        tab.pre_backup.setText(str(backup))
        tab.pre_database.setText('test_db')
        db.execute.side_effect = [None] * 13 + [RuntimeError('cannot modify column pegawai')]
        tab.run_pre_btn.click()
        assert not window.isEnabled()
        window.close()
        assert window.isVisible()
        wait_for_worker(window)
        assert tab.session.failed.sequence == 14
        assert len(tab.session.completed) == 13
        assert tab.completed_sql.isReadOnly()
        assert tab.pending_sql.isEnabled() and not tab.pre_database.isEnabled()
        assert not tab.approve_btn.isEnabled()
        tab.pending_sql.setPlainText('SELECT 140; SELECT 15; SELECT 16;')
        db.execute.reset_mock(side_effect=True)
        tab.run_pre_btn.click()
        wait_for_worker(window)
        assert [c.args[2] for c in db.execute.call_args_list] == ['SELECT 140', 'SELECT 15', 'SELECT 16']
        assert tab.last_pre_result.success_count == 16
        output = Path(tab.output_path.text())
        assert output.is_file() and output.name == 'migration_final.sql'
        assert window.final_tab.final_migration.text() == str(output)
        assert tab.approve_btn.isEnabled() and not tab.pending_sql.isEnabled()
        tab.approve_btn.click()
        assert history.load_approval().migration_hash == tab.last_pre_result.migration_hash
        service.start(source, tab._pre_config(), str(backup))  # An older/different session is also removed.
        assert len(list((tmp_path / 'sessions').iterdir())) == 2
        QMessageBox.warning.return_value = QMessageBox.No
        tab.reset_btn.click()
        assert not window._active_threads
        assert output.exists()
        assert len(list((tmp_path / 'sessions').iterdir())) == 2
        QMessageBox.warning.return_value = QMessageBox.Yes
        db.test_connection.side_effect = RuntimeError('reset connection failed')
        tab.reset_btn.click()
        wait_for_worker(window)
        assert len(list((tmp_path / 'sessions').iterdir())) == 2
        assert output.exists()
        critical.assert_called_once()
        critical.reset_mock()
        db.test_connection.side_effect = None
        tab.reset_btn.click()
        wait_for_worker(window)
        assert tab.session is None
        assert list((tmp_path / "sessions").iterdir()) == []
        assert not output.exists()
        assert tab.run_pre_btn.isEnabled() and not tab.approve_btn.isEnabled()
        assert tab.pre_database.isEnabled()
        critical.assert_not_called()
    finally:
        if window._active_threads:
            wait_for_worker(window)
        window.close()


def test_missing_checkpoint_blocks_resume_but_allows_reset(qt_app, tmp_path: Path, monkeypatch) -> None:
    with patch('dotenv.load_dotenv', return_value=False):
        from app.presentation.qt.migration.main_window import MainWindow
        from app import config
    for name in vars(config):
        if name.startswith(('PRE_', 'FINAL_')):
            monkeypatch.setattr(config, name, 3306 if name.endswith('_PORT') else '')
    source = tmp_path / 'input.sql'
    source.write_text('SELECT 1; SELECT 2;')
    backup = tmp_path / 'backup.sql'
    backup.write_text('-- backup')
    db = Mock()
    db.execute.side_effect = [None, RuntimeError('failed')]
    history = LocalHistoryRepository(tmp_path / 'history')
    service = ResumablePreMigration(SqlMigrationParser(), db, history, LocalSessionRepository(tmp_path / 'sessions'))
    window = MainWindow(db, Mock(), history, SqlMigrationParser(), service)
    monkeypatch.setattr(QMessageBox, 'warning', Mock(return_value=QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    monkeypatch.setattr('subprocess.Popen', Mock(side_effect=AssertionError('No database')))
    try:
        tab = window.pre_tab
        tab.pre_migration.setText(str(source))
        tab.pre_backup.setText(str(backup))
        tab.pre_database.setText('test_db')
        tab.run_pre_btn.click()
        wait_for_worker(window)
        service.checkpoint_file(tab.session).unlink()
        db.execute.reset_mock(side_effect=True)
        tab.run_pre_btn.click()
        wait_for_worker(window)
        assert tab.session.in_flight
        assert len(tab.session.completed) == 1
        assert not tab.run_pre_btn.isEnabled()
        db.execute.assert_not_called()
        tab.reset_btn.click()
        wait_for_worker(window)
        assert tab.session is None and tab.run_pre_btn.isEnabled()
    finally:
        if window._active_threads:
            wait_for_worker(window)
        window.close()
