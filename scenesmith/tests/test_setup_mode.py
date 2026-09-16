from __future__ import annotations

import app


app_module = app._load_app_module()


def test_safe_example_setup_mode_does_not_initialize_club_cache(monkeypatch, qapp, qtbot) -> None:
    called = False

    def forbidden_cache(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("cache initialization must not run in setup mode")

    monkeypatch.delenv("CHRONICLE_LANTERN_CONFIG", raising=False)
    monkeypatch.setattr(app_module, "activate_cache_root", forbidden_cache)
    window = app_module.MainWindow()
    qtbot.addWidget(window)

    assert window.setup_mode
    assert window.club_service is None
    assert not called
    assert not window.generator_tab.isEnabled()
    assert not window.guest_tab.isEnabled()
    assert window.campaign_tab.isEnabled()
    assert "Setup required" in window.status.currentMessage()
