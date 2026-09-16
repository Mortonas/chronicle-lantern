from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath


SCHEMA_RULES = [
    {
        "name": "club whole-sheet readings and interpreted prep",
        "schema_paths": {"scenesmith/core/club_prep.py", "scenesmith/core/club_tag_groups.py", "scenesmith/templates/club_prep.j2"},
        "version_owner_paths": {"scenesmith/core/club_prep.py"},
        "version_names": "CLUB_PREP_READING_SCHEMA_VERSION, CLUB_PREP_READING_PROMPT_VERSION, CLUB_PREP_REASONING_VERSION, CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION, CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION, CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION",
    },
    {
        "name": "campaign state",
        "schema_paths": {
            "scenesmith/core/campaign_state.py",
        },
        "version_owner_paths": {
            "scenesmith/core/campaign_state.py",
        },
        "version_names": "SCHEMA_VERSION",
    },
    {
        "name": "club dashboard/event/panel cache",
        "schema_paths": {
            "scenesmith/core/club_generation.py",
            "scenesmith/core/club_models.py",
        },
        "version_owner_paths": {
            "scenesmith/core/club_generation.py",
        },
        "version_names": "CLUB_DASHBOARD_SCHEMA_VERSION, CLUB_PANEL_SCHEMA_VERSION, CLUB_SKELETON_VERSION",
    },
    {
        "name": "club index cache",
        "schema_paths": {
            "scenesmith/core/club_index.py",
        },
        "version_owner_paths": {
            "scenesmith/core/club_index.py",
        },
        "version_names": "CLUB_INDEX_SCHEMA_VERSION",
    },
    {
        "name": "club identity registry",
        "schema_paths": {
            "scenesmith/core/club_identity.py",
        },
        "version_owner_paths": {
            "scenesmith/core/club_identity.py",
        },
        "version_names": "IDENTITY_REGISTRY_VERSION",
    },
]


def _normalize(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/").lstrip("./")))


def _version_names(rule: dict[str, object]) -> list[str]:
    return [name.strip() for name in str(rule["version_names"]).split(",")]


def _version_changes_by_file(diff_text: str) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = {}
    current_path: str | None = None
    all_version_names = {
        name
        for rule in SCHEMA_RULES
        for name in _version_names(rule)
    }

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_path = None
            continue
        if line.startswith("+++ b/"):
            current_path = _normalize(line[6:])
            continue
        if line.startswith("+++ "):
            current_path = None
            continue
        if not current_path:
            continue
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue

        changed_line = line[1:].strip()
        for version_name in all_version_names:
            if re.match(rf"^{re.escape(version_name)}\s*=", changed_line):
                changes.setdefault(current_path, set()).add(version_name)

    return changes


def _rule_has_version_change(rule: dict[str, object], version_changes: dict[str, set[str]]) -> bool:
    owner_paths = set(rule["version_owner_paths"])
    version_names = set(_version_names(rule))
    return any(version_changes.get(path, set()) & version_names for path in owner_paths)


def check_paths(paths: list[str], diff_text: str | None = None) -> list[str]:
    changed = {_normalize(path) for path in paths if path}
    version_changes = _version_changes_by_file(diff_text) if diff_text is not None else {}
    errors: list[str] = []

    for rule in SCHEMA_RULES:
        changed_schema_paths = sorted(changed & rule["schema_paths"])
        if not changed_schema_paths:
            continue
        owner_paths = set(rule["version_owner_paths"])
        changed_owner_paths = changed & owner_paths
        changed_dependent_schema_paths = set(changed_schema_paths) - owner_paths
        if diff_text is None and changed & rule["version_owner_paths"]:
            continue
        if diff_text is not None and changed_owner_paths and not changed_dependent_schema_paths:
            continue
        if diff_text is not None and _rule_has_version_change(rule, version_changes):
            continue
        errors.append(
            f"{rule['name']} schema-sensitive files changed without the matching version owner. "
            f"Changed: {', '.join(changed_schema_paths)}. "
            f"Include {', '.join(sorted(rule['version_owner_paths']))} and update {rule['version_names']} when the wire/cache shape changes."
        )

    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check schema-sensitive changes include a matching version update.")
    parser.add_argument("--diff-file", help="Caller-supplied unified diff for the same changed-file range.")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)

    diff_text = None
    if args.diff_file:
        diff_text = Path(args.diff_file).read_text(encoding="utf-8-sig")

    errors = check_paths(args.paths, diff_text=diff_text)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
