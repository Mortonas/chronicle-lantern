from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("src", "core", "app", "adapters")
SKIP_DIRS = {
    "__pycache__",
    ".ai-rewrite",
    ".benchmarks",
    ".club-cache",
    ".debug",
    ".pytest-basetemp",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "tmp_report_run",
    "tmp_vault_test",
}
APPROVED_DIRECT_PROVIDER_FILES = {
    Path("src/model_adapters.py"),
}
FORBIDDEN_MODULE_PREFIXES = (
    "aiohttp",
    "anthropic",
    "cohere",
    "google.genai",
    "google.generativeai",
    "groq",
    "http.client",
    "httpx",
    "mistralai",
    "ollama",
    "openai",
    "requests",
    "urllib.request",
)


def _iter_python_sources() -> list[Path]:
    paths: list[Path] = []
    paths.extend(PROJECT_ROOT.glob("*.py"))
    for dirname in SOURCE_DIRS:
        root = PROJECT_ROOT / dirname
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            paths.append(path)
    return sorted(paths)


def _relative(path: Path) -> Path:
    return path.relative_to(PROJECT_ROOT)


def _is_forbidden_module(module_name: str | None) -> bool:
    if not module_name:
        return False
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    return None


def _provider_boundary_violations(path: Path) -> list[str]:
    rel_path = _relative(path)
    if rel_path in APPROVED_DIRECT_PROVIDER_FILES:
        return []

    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    forbidden_aliases: set[str] = set()
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name):
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    forbidden_aliases.add(bound_name)
                    violations.append(f"{rel_path}:{node.lineno} imports {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if _is_forbidden_module(node.module):
                for alias in node.names:
                    bound_name = alias.asname or alias.name
                    forbidden_aliases.add(bound_name)
                violations.append(f"{rel_path}:{node.lineno} imports from {node.module!r}")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if not name:
            continue
        root_name = name.split(".", 1)[0]
        if root_name in forbidden_aliases or _is_forbidden_module(name):
            violations.append(f"{rel_path}:{node.lineno} calls {name!r}")

    return violations


def test_provider_requests_stay_inside_model_adapter_boundary() -> None:
    violations: list[str] = []
    for path in _iter_python_sources():
        violations.extend(_provider_boundary_violations(path))

    assert not violations, (
        "Provider SDK and direct HTTP-client usage must stay in src/model_adapters.py. "
        "Delegate through model_adapters instead.\n" + "\n".join(violations)
    )
