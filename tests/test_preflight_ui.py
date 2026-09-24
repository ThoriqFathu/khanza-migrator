import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QThread, Slot
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from app.domains.migration.application.migration_preflight import MigrationPreflight
from app.domains.migration.application.resumable_pre_migration import ResumablePreMigration
from app.shared.filesystem.history import LocalHistoryRepository
from app.shared.filesystem.sessions import LocalSessionRepository
from app.shared.sql.parser import SqlMigrationParser
from app.shared.sql.preflight_extractor import MigrationPreflightExtractor
from tests.test_pre_migration_resume_ui import wait_for_worker


@pytest.fixture(scope='module')
def qt_app():
    with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
        yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize('fatal', [False, True])
def test_preflight_snapshot_worker_and_main_thread_render(qt_app, tmp_path: Path, monkeypatch, fatal):
    with patch('dotenv.load_dotenv', return_value=False):
        from app.presentation.qt.migration.main_window import MainWindow
        from app.presentation.qt.migration.pre_migration_tab import PreMigrationTab
        from app import config
    for name in vars(config):
        if name.startswith(('PRE_', 'FINAL_')):
            monkeypatch.setattr(config, name, 3306 if name.endswith('_PORT') else '')
    source = tmp_path / 'migration.sql'
    sql = 'ALTER TABLE c ADD CONSTRAINT fk FOREIGN KEY (id) REFERENCES p(id);'
    source.write_text(sql)
    db = Mock()
    worker_threads = []
    def count(*_):
        worker_threads.append(QThread.currentThread())
        return 12
    db.count_orphans.side_effect = count
    if fatal:
        db.test_connection.side_effect = RuntimeError('offline')
    history = LocalHistoryRepository(tmp_path / 'history')
    parser = SqlMigrationParser()
    sessions = LocalSessionRepository(tmp_path / 'sessions')
    resume = ResumablePreMigration(parser, db, history, sessions)
    service = MigrationPreflight(parser, MigrationPreflightExtractor(), db)
    render_threads = []
    class RecordingTab(PreMigrationTab):
        @Slot(object)
        def _preflight_finished(self, outcome):
            render_threads.append(QThread.currentThread())
            super()._preflight_finished(outcome)
    monkeypatch.setattr('app.presentation.qt.migration.main_window.PreMigrationTab', RecordingTab)
    critical_threads = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *_: critical_threads.append(QThread.currentThread()))
    monkeypatch.setattr('subprocess.run', Mock(side_effect=AssertionError('No real database')))
    window = MainWindow(db, Mock(), history, parser, resume, service)
    # Hold dispatch to change widgets after snapshot but before worker starts.
    queued = []
    window.pre_tab._run_worker = lambda *args, **kwargs: queued.append((args, kwargs))
    try:
        window.show()
        tab = window.pre_tab
        tab.pre_migration.setText(str(source))
        tab.pre_database.setText('snapshot_test')
        tab.pre_password.setText('snapshot_password')
        before_pending = tab.pending_sql.toPlainText()
        tab.preflight_btn.click()
        assert len(queued) == 1
        args, kwargs = queued.pop()
        # The callable captures no self/widget; widget reads would trip this guard too.
        assert all(not hasattr(cell.cell_contents, 'metaObject') for cell in args[0].__closure__)
        tab.pre_database.setText('changed_after_dispatch')
        tab.pre_migration.setText(str(tmp_path / 'missing.sql'))
        tab.pre_password.setText('changed_password')
        original_text = QLineEdit.text
        def gui_only_text(widget):
            assert QThread.currentThread() == qt_app.thread()
            return original_text(widget)
        monkeypatch.setattr(QLineEdit, 'text', gui_only_text)
        window._run_worker(*args, **kwargs)
        wait_for_worker(window)
        assert render_threads == [qt_app.thread()]
        called_config, password = db.test_connection.call_args.args
        assert called_config.database == 'snapshot_test' and password == 'snapshot_password'
        if fatal:
            assert critical_threads == [qt_app.thread()]
            assert 'offline' in tab.preflight_status.text()
            assert not tab.preflight_details_btn.isEnabled()
        else:
            assert worker_threads and all(t != qt_app.thread() for t in worker_threads)
            assert 'FK orphan problems: 1' in tab.preflight_status.text()
            assert tab.preflight_result.migration_file == str(source)
            tab.preflight_details_btn.click()
            assert tab.preflight_dialog.table.rowCount() == 1
            assert tab.preflight_dialog.table.item(0, 0).text() == 'BLOCKER'
            assert tab.preflight_dialog.thread() == qt_app.thread()
        assert tab.session is None and tab.last_pre_result is None
        assert tab.pending_sql.toPlainText() == before_pending
        assert not tab.output_path.text()
        assert source.read_text() == sql
        assert history.load_approval() is None
        db.execute.assert_not_called()
        assert not list((tmp_path / 'sessions').glob('*/session.json'))
        tab.pre_database.setText('invalidate_result')
        assert tab.preflight_result is None
    finally:
        if window._active_threads:
            wait_for_worker(window)
        window.close()
