import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QThread, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox

from app.domains.migration.application.diagnostic_migration import DiagnosticMigration
from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.filesystem.sessions import LocalSessionRepository
from app.shared.sql.parser import SqlMigrationParser
from tests.test_pre_migration_resume_ui import wait_for_worker


@pytest.fixture(scope='module')
def qt_app():
    with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
        yield QApplication.instance() or QApplication([])


@pytest.fixture
def diagnostic_window(qt_app, tmp_path, monkeypatch):
    with patch('dotenv.load_dotenv', return_value=False):
        from app.presentation.qt.migration.main_window import MainWindow
        from app.presentation.qt.migration.diagnostic_panel import DiagnosticPanel
        from app import config
    for name in vars(config):
        if name.startswith(('PRE_', 'FINAL_')):
            monkeypatch.setattr(config, name, 3306 if name.endswith('_PORT') else '')
    rendered_on_gui = []
    class RecordingPanel(DiagnosticPanel):
        @Slot(object)
        def _finished(self, outcome):
            rendered_on_gui.append(QThread.currentThread() == qt_app.thread())
            super()._finished(outcome)
    monkeypatch.setattr('app.presentation.qt.migration.pre_migration_tab.DiagnosticPanel', RecordingPanel)
    source = tmp_path / 'migration.sql'
    source.write_text('SELECT 1; SELECT 2; SELECT 3;')
    backup = tmp_path / 'backup.sql'
    backup.write_text('-- disposable fake backup')
    db = Mock()
    db.database_exists.return_value = True
    parser = SqlMigrationParser()
    history = LocalHistoryRepository(tmp_path / 'history')
    resume = ResumablePreMigration(parser, db, history, LocalSessionRepository(tmp_path / 'sessions'))
    service = DiagnosticMigration(parser, db)
    service.execute = Mock(wraps=service.execute)
    window = MainWindow(db, Mock(), history, parser, resume, diagnostic=service)
    tab = window.pre_tab
    tab.pre_database.setText('fresh_test')
    tab.pre_migration.setText(str(source))
    tab.pre_backup.setText(str(backup))
    tab.pre_password.setText('test-secret::8c19!')
    monkeypatch.setattr(QMessageBox, 'warning', Mock(return_value=QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, 'information', Mock())
    critical_threads = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *_: critical_threads.append(QThread.currentThread() == qt_app.thread()))
    monkeypatch.setattr('subprocess.run', Mock(side_effect=AssertionError('No real database')))
    monkeypatch.setattr('subprocess.Popen', Mock(side_effect=AssertionError('No real database')))
    window.show()
    yield window, service, db, source, rendered_on_gui, critical_threads
    if window._active_threads:
        wait_for_worker(window)
    window.close()


def reset(window):
    window.pre_tab.reset_btn.click()
    wait_for_worker(window)


def test_fresh_reset_required_and_confirmation_no_does_nothing(diagnostic_window):
    window, service, db, _, _, _ = diagnostic_window
    tab = window.pre_tab
    panel = tab.diagnostic_panel
    assert panel.run_btn.text() == 'Run Diagnostic Migration'
    panel.run_btn.click()
    service.execute.assert_not_called()
    db.execute.assert_not_called()
    assert 'Reset Test Database' in QMessageBox.warning.call_args.args[2]
    reset(window)
    assert tab._fresh_test_target == tab._pre_config()
    QMessageBox.warning.return_value = QMessageBox.No
    panel.run_btn.click()
    assert 'fresh_test' in QMessageBox.warning.call_args.args[2]
    assert 'TETAP MELANJUTKAN' in QMessageBox.warning.call_args.args[2]
    assert QMessageBox.warning.call_args.args[-1] == QMessageBox.No
    assert tab._diagnostic_target is None
    assert tab._fresh_test_target is not None
    assert not window._active_threads
    service.execute.assert_not_called()
    tab.pre_host.setText('different-server')
    panel.run_btn.click()
    assert tab._fresh_test_target is None
    service.execute.assert_not_called()


@pytest.mark.parametrize('mode', ['errors', 'success', 'fatal', 'partial'])
def test_worker_snapshot_results_export_and_dirty_until_successful_reset(
        diagnostic_window, qt_app, tmp_path, monkeypatch, mode):
    window, service, db, source, render_threads, critical_threads = diagnostic_window
    tab = window.pre_tab
    panel = tab.diagnostic_panel
    reset(window)
    calls_in_worker = []
    def execute(target, password, sql):
        calls_in_worker.append(QThread.currentThread() != qt_app.thread())
        assert target.database == 'fresh_test' and password == 'test-secret::8c19!'
        if sql == 'SELECT 2' and mode == 'partial':
            raise FileNotFoundError('mysql unavailable')
        if sql == 'SELECT 2' and mode == 'errors':
            error = RuntimeError('ERROR 1832 (HY000): Cannot modify column')
            error.errno = 1832
            error.stderr = 'ERROR 1832 (HY000): Cannot modify column\n'
            raise error
    db.execute.side_effect = execute
    if mode == 'fatal':
        db.test_connection.side_effect = RuntimeError('test-secret::8c19! unreachable')
    queued = []
    panel._run_worker = lambda *args, **kwargs: queued.append((args, kwargs))
    original_sql = source.read_text()
    panel.run_btn.click()
    assert len(queued) == 1
    assert tab._diagnostic_target.database == 'fresh_test'
    assert tab._fresh_test_target is None
    assert not tab.run_pre_btn.isEnabled() and not panel.run_btn.isEnabled()
    assert not tab.approve_btn.isEnabled() and not tab.pre_database.isEnabled()
    args, kwargs = queued.pop()
    assert all(not hasattr(cell.cell_contents, 'metaObject') for cell in args[0].__closure__)
    # Simulate input changes between snapshot and worker execution.
    tab.pre_migration.setText(str(tmp_path / 'missing.sql'))
    tab.pre_password.setText('changed-password')
    original_text = QLineEdit.text
    def gui_text(widget):
        assert QThread.currentThread() == qt_app.thread()
        return original_text(widget)
    monkeypatch.setattr(QLineEdit, 'text', gui_text)
    window._run_worker(*args, **kwargs)
    wait_for_worker(window)
    assert render_threads == [True]
    assert service.execute.call_args.args[:3] == (source, tab._diagnostic_target, 'test-secret::8c19!')
    if mode == 'fatal':
        assert panel.result is None and not panel.export_btn.isEnabled()
        assert 'test-secret::8c19!' not in panel.summary.text()
        assert critical_threads == [True]
    else:
        assert calls_in_worker and all(calls_in_worker)
        assert panel.result is not None
        assert 'Database: fresh_test' in panel.summary.text()
        assert panel.result.failure_count == (0 if mode == 'success' else 1)
        assert 'Reset/Restore TEST' in panel.summary.text()
        panel.details_btn.click()
        assert panel.dialog.table.rowCount() == panel.result.failure_count
        if mode != 'success':
            assert 'SELECT 2' in panel.dialog.detail.toPlainText()
            assert panel.dialog.table.item(0, 0).text() == '#2'
        if mode == 'partial':
            assert panel.result.unattempted_count == 1
            assert 'Aborted' in panel.summary.text()
        destination = tmp_path / 'diagnostic.md'
        save = Mock(return_value=(str(destination), 'Markdown (*.md)'))
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
        panel.export_btn.click()
        assert save.call_args.args[2].startswith('diagnostic_migration_')
        report = destination.read_text()
        assert 'Diagnostic Migration Report' in report
        assert 'test-secret::8c19!' not in report and 'changed-password' not in report
        if mode == 'errors':
            assert 'ERROR 1832 (HY000): Cannot modify column' in report
        assert service.execute.call_count == 1
    assert tab.session is None and tab.last_pre_result is None
    assert not tab.pending_sql.toPlainText() and not tab.completed_sql.toPlainText()
    assert not tab.output_path.text() and not tab.approve_btn.isEnabled()
    assert tab.history.load_approval() is None
    assert not list((tmp_path / 'sessions').glob('*'))
    assert source.read_text() == original_sql
    previous_executions = db.execute.call_count
    tab._run_pre()  # Handler guard, not just a disabled button.
    tab._approve()
    panel._run()
    assert db.execute.call_count == previous_executions
    assert service.execute.call_count == 1
    assert tab._diagnostic_target is not None
    # A failed restore must not clear dirty state or mark TEST fresh.
    db.test_connection.side_effect = RuntimeError('reset failed')
    reset(window)
    assert tab._diagnostic_target is not None and tab._fresh_test_target is None
    assert not tab.run_pre_btn.isEnabled()
    db.test_connection.side_effect = None
    tab.pre_migration.setText(str(source))
    reset(window)
    assert tab._diagnostic_target is None
    assert tab._fresh_test_target == tab._pre_config()
    assert tab.run_pre_btn.isEnabled() and panel.run_btn.isEnabled()
    assert not tab.approve_btn.isEnabled()


def test_active_pre_session_blocks_diagnostic(diagnostic_window):
    window, service, db, _, _, _ = diagnostic_window
    tab = window.pre_tab
    reset(window)
    db.execute.side_effect = RuntimeError('PRE failed')
    tab.run_pre_btn.click()
    wait_for_worker(window)
    session = tab.session
    assert session is not None and session.failed is not None
    assert not tab.diagnostic_panel.run_btn.isEnabled()
    tab.diagnostic_panel._run()
    service.execute.assert_not_called()
    assert tab.session is session


def test_export_cancel_and_write_failure_preserve_result(diagnostic_window, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from app.domains.migration.domain.diagnostic import DiagnosticMigrationResult
    from app.domains.migration.domain.models import StatementResult
    window, service, _, _, _, critical_threads = diagnostic_window
    panel = window.pre_tab.diagnostic_panel
    now = datetime.now(timezone.utc)
    result = DiagnosticMigrationResult(now, now, 'test', 'migration.sql', 2, [
        StatementResult(1, 'SELECT first_failure', False, 1, 1832, 'actual first error'),
        StatementResult(2, 'SELECT second_failure', False, 1, 1452, 'actual second error'),
    ])
    panel._finished((result, ''))
    panel.details_btn.click()
    assert panel.dialog.table.rowCount() == 2
    panel.dialog.table.setCurrentCell(1, 0)
    assert 'SELECT second_failure' in panel.dialog.detail.toPlainText()
    assert 'actual second error' in panel.dialog.detail.toPlainText()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', Mock(return_value=('', '')))
    panel.export_btn.click()
    assert panel.result is result
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', Mock(return_value=(str(tmp_path / 'missing' / 'report.md'), '')))
    panel.export_btn.click()
    assert critical_threads == [True]
    assert panel.result is result and panel.export_btn.isEnabled()
    service.execute.assert_not_called()
