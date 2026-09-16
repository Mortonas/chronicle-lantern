
import pytest
import os

import core_logic
import search_ripgrep

TEST_TABLES_DIR = os.path.join(os.path.dirname(__file__), 'test_tables')

# --- load_tables test ---
def test_load_tables_returns_objects():
    tables = core_logic.load_tables(TEST_TABLES_DIR)
    assert tables, "No tables loaded"
    for t in tables.values():
        assert hasattr(t, 'name')
        assert hasattr(t, 'source_path')
        assert hasattr(t, 'entries')
        assert isinstance(t.entries, list)

# --- roll_scene_concept test ---
def test_roll_scene_concept_shape():
    class DummyTable(list):
        def __init__(self):
            super().__init__()
            self.entries = [{'scene_concept': 'foo', 'tags': ['bar']}]
            self.extend(self.entries)
    result = core_logic.roll_scene_concept(DummyTable())
    assert hasattr(result, 'scene_concept')
    assert hasattr(result, 'tags')
    assert isinstance(result.scene_concept, str)
    assert isinstance(result.tags, list)


def test_build_expression_accepts_prefixed_tags():
    base, groups = core_logic.build_expression(True, ['#Scholars'], [(['#newcomer'], 'or')])
    assert base == ['scholars']
    assert groups == [{'tags': ['newcomer'], 'op': 'OR'}]

# --- ripgrep builder test ---
def test_run_rg_paths_fallback(monkeypatch):
    # Simulate rg returning no files, then files on fallback
    calls = []
    def fake_run(cmd, capture_output, text, cwd=None, encoding=None, errors=None, **_):
        calls.append(cmd)
        class Result:
            def __init__(self, code, out):
                self.returncode = code
                self.stdout = out
                self.stderr = ''
        if '--no-ignore' in cmd:
            return Result(0, 'file1.txt\nfile2.txt')
        return Result(0, '')
    monkeypatch.setattr('subprocess.run', fake_run)
    files = search_ripgrep._run_rg_paths('rg', ['pattern'], cwd='.')
    assert files == {os.path.normpath('./file1.txt'), os.path.normpath('./file2.txt')}
    assert any('--no-ignore' in c for c in calls)


def test_run_rg_paths_hides_windows_console(monkeypatch):
    captured_kwargs = []

    def fake_run(cmd, capture_output, text, cwd=None, encoding=None, errors=None, **kwargs):
        captured_kwargs.append(kwargs)

        class Result:
            returncode = 0
            stdout = 'file1.txt'
            stderr = ''

        return Result()

    monkeypatch.setattr('subprocess.run', fake_run)
    files = search_ripgrep._run_rg_paths('rg', ['pattern'], cwd='.')

    assert files == {os.path.normpath('./file1.txt')}
    if os.name == "nt":
        assert captured_kwargs[0]["creationflags"] & search_ripgrep.subprocess.CREATE_NO_WINDOW
        startupinfo = captured_kwargs[0]["startupinfo"]
        assert startupinfo.dwFlags & search_ripgrep.subprocess.STARTF_USESHOWWINDOW
        assert startupinfo.wShowWindow == search_ripgrep.subprocess.SW_HIDE
    else:
        assert captured_kwargs == [{}]
