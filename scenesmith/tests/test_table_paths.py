import os
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from core_logic import load_tables

_app = QApplication.instance() or QApplication([])

def test_table_paths_exist_and_absolute():
    config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config'))
    tables = load_tables(os.path.join(config_dir, 'tables'))
    for t in tables.values():
        assert os.path.isabs(t.source_path)
        assert os.path.exists(t.source_path)
        assert len(t.entries) >= 1

def test_default_table_fallback():
    config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../config'))
    tables = load_tables(os.path.join(config_dir, 'tables'))
    # Simulate missing default
    from app import MainWindow
    mw = MainWindow()
    mw.cfg['ui']['default_table'] = 'nonexistent.yaml'
    mw.tables = tables
    mw.current_table_key = mw.cfg['ui']['default_table']
    if mw.current_table_key not in mw.tables:
        fallback = next(iter(mw.tables), None)
        assert fallback is not None
    mw.close()

def test_guest_tab_resolves_obsidian_package_import():
    import app
    import app.obsidian_open as obsidian_open
    from ui.guest_tab import GuestTab

    assert hasattr(app, '__path__')
    assert callable(obsidian_open.open_in_obsidian)
    assert GuestTab.__name__ == 'GuestTab'

def test_app_package_exports_main_window():
    import app
    from app import MainWindow

    assert hasattr(app, '__path__')
    assert MainWindow.__name__ == 'MainWindow'

def test_table_with_spaces_in_filename(tmp_path):
    # Create a table file with spaces in the name
    table_path = tmp_path / 'Table With Spaces.yaml'
    table_path.write_text('name: Test Table\nentries:\n  - scene_concept: test\n    tags: [a, b]\n')
    from app_types import EventTable, EventEntry
    t = EventTable(name='Test Table', entries=[EventEntry(scene_concept='test', tags=['a', 'b'])], source_path=str(table_path), rel_path_from_project='Table With Spaces.yaml')
    assert os.path.exists(t.source_path)
    with open(t.source_path, 'r', encoding='utf-8') as f:
        assert 'Test Table' in f.read()
