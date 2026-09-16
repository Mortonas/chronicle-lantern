from __future__ import annotations

import pytest

try:
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover - Qt not available during import
    QApplication = None  # type: ignore


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Core routing-policy checks must always run and report their real result."""
    forbidden_names = ("skip", "skipif", "xfail")
    violations: list[str] = []
    for item in items:
        if item.get_closest_marker("core_routing_policy") is None:
            continue
        forbidden = [name for name in forbidden_names if any(item.iter_markers(name=name))]
        if forbidden:
            violations.append(f"{item.nodeid}: {', '.join(forbidden)}")

    if violations:
        details = "\n".join(f"- {violation}" for violation in violations)
        raise pytest.UsageError(
            "core_routing_policy items cannot carry skip, skipif, or xfail marks:\n" + details
        )


@pytest.fixture(scope="session")
def qapp():
    """Ensure a QApplication instance is available for UI-oriented tests."""
    if QApplication is None:  # pragma: no cover - safety fallback
        pytest.skip("PySide6 is required for UI tests")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
