from __future__ import annotations

import sys
from pathlib import PurePosixPath


DEPENDENCY_MANIFESTS = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-minimal.txt",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "scenesmith/pyproject.toml",
    "scenesmith/requirements.txt",
    "scenesmith/requirements-dev.txt",
    "scenesmith/requirements-minimal.txt",
    "scenesmith/poetry.lock",
    "scenesmith/Pipfile",
    "scenesmith/Pipfile.lock",
}
DEPENDENCY_REVIEW_FILES = {
    "scenesmith/docs/DEPENDENCY_SECURITY.md",
}


def _normalize(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/").lstrip("./")))


def check_paths(paths: list[str]) -> list[str]:
    changed = {_normalize(path) for path in paths if path}
    changed_manifests = sorted(changed & DEPENDENCY_MANIFESTS)
    if not changed_manifests:
        return []
    if changed & DEPENDENCY_REVIEW_FILES:
        return []
    return [
        "Dependency manifests changed without a dependency review update.",
        "Changed manifests: " + ", ".join(changed_manifests),
        "Update scenesmith/docs/DEPENDENCY_SECURITY.md or split the dependency change into a reviewed task.",
    ]


def main(argv: list[str]) -> int:
    errors = check_paths(argv)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
