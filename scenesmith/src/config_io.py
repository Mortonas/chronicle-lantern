from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping
from urllib.parse import urlparse

import yaml

from path_resolver import assert_path_exists


CONFIG_ENV_VAR = "CHRONICLE_LANTERN_CONFIG"
LOCAL_CONFIG_NAME = "app.local.yaml"
EXAMPLE_CONFIG_NAME = "app.example.yaml"
_ENV_REFERENCE_RE = re.compile(r"^(?:env:[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})$")


class AppConfigError(ValueError):
    """A classified startup error whose message contains no configuration values."""

    def __init__(self, source: str, category: str) -> None:
        self.source = source
        self.category = category
        super().__init__(f"{source} configuration is {category}")


@dataclass(frozen=True)
class AppConfigSelection:
    config: dict[str, Any]
    path: Path
    source: str
    setup_required: bool
    error: AppConfigError | None = None

    @property
    def valid(self) -> bool:
        return self.error is None


def _read_bytes(path: Path, *, source: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise AppConfigError(source, "missing") from exc
    except PermissionError as exc:
        raise AppConfigError(source, "unreadable") from exc
    except OSError as exc:
        raise AppConfigError(source, "unreadable") from exc


def _decode_best(data: bytes, *, source: str) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AppConfigError(source, "unreadable")


def _effectively_empty(text: str) -> bool:
    return not any(line.strip() and not line.strip().startswith("#") for line in text.splitlines())


def _load_yaml_any(path: Path, *, source: str) -> Any:
    raw = _read_bytes(path, source=source)
    text = _decode_best(raw, source=source)
    if not raw or _effectively_empty(text):
        raise AppConfigError(source, "empty")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AppConfigError(source, "malformed") from exc
    if data is None:
        raise AppConfigError(source, "empty")
    return data


def _is_windows_unsafe_path(raw: str) -> bool:
    normalized = raw.replace("/", "\\")
    return normalized.startswith(("\\\\", "\\?\\", "\\.\\"))


def _validated_local_path(value: Any, *, source: str, category: str, require_directory: bool) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip() or _is_windows_unsafe_path(value.strip()):
        raise AppConfigError(source, category)
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        raise AppConfigError(source, category)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AppConfigError(source, category) from exc
    if _is_windows_unsafe_path(str(resolved)):
        raise AppConfigError(source, category)
    if require_directory and not resolved.is_dir():
        raise AppConfigError(source, category)
    if not require_directory and not resolved.is_file():
        raise AppConfigError(source, category)
    return str(resolved)


def _validate_string_list(value: Any, *, source: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise AppConfigError(source, "schema-invalid")
    return [item.strip() for item in value]


def _validate_app_cfg(data: Any, *, source: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise AppConfigError(source, "schema-invalid")
    result = dict(data)

    ui = result.get("ui", {})
    if not isinstance(ui, dict):
        raise AppConfigError(source, "schema-invalid")
    result["ui"] = dict(ui)

    result["vault_path"] = _validated_local_path(
        result.get("vault_path"), source=source, category="vault-path-invalid", require_directory=True
    )

    rewrite = result.get("rewrite", {})
    if not isinstance(rewrite, dict):
        raise AppConfigError(source, "schema-invalid")
    rewrite = dict(rewrite)
    rewrite["anchor_folder"] = _validated_local_path(
        rewrite.get("anchor_folder"), source=source, category="rewrite-path-invalid", require_directory=True
    )
    result["rewrite"] = rewrite

    model = result.get("model", {})
    if model is None:
        model = {}
    if not isinstance(model, dict):
        raise AppConfigError(source, "schema-invalid")
    model = dict(model)
    credential = model.get("api_key")
    if credential not in (None, "") and (
        not isinstance(credential, str) or not _ENV_REFERENCE_RE.fullmatch(credential.strip())
    ):
        raise AppConfigError(source, "credential-reference-invalid")
    endpoint = model.get("endpoint")
    if endpoint not in (None, ""):
        if not isinstance(endpoint, str):
            raise AppConfigError(source, "provider-endpoint-invalid")
        parsed = urlparse(endpoint.strip())
        if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise AppConfigError(source, "provider-endpoint-invalid")
    result["model"] = model

    schema = result.get("character_schema", {})
    if not isinstance(schema, dict):
        raise AppConfigError(source, "schema-invalid")
    schema = dict(schema)
    schema["affiliation_labels"] = _validate_string_list(
        schema.get("affiliation_labels", ["Affiliation"]), source=source
    )
    schema["faction_labels"] = _validate_string_list(
        schema.get("faction_labels", ["Faction"]), source=source
    )
    schema["relationship_headings"] = _validate_string_list(
        schema.get("relationship_headings", ["Relationships"]), source=source
    )
    schema["default_character_tags"] = _validate_string_list(
        schema.get("default_character_tags", ["npc", "character"]), source=source
    )
    result["character_schema"] = schema
    return result


def _validated_selected_config_path(raw: str, *, source: str) -> Path:
    if not raw or not raw.strip() or _is_windows_unsafe_path(raw.strip()):
        raise AppConfigError(source, "path-invalid")
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        raise AppConfigError(source, "path-invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise AppConfigError(source, "missing") from exc
    except PermissionError as exc:
        raise AppConfigError(source, "unreadable") from exc
    except (OSError, RuntimeError) as exc:
        raise AppConfigError(source, "path-invalid") from exc
    if _is_windows_unsafe_path(str(resolved)):
        raise AppConfigError(source, "path-invalid")
    if not resolved.is_file():
        raise AppConfigError(source, "path-invalid")
    return resolved


def load_app_config(app_yaml_path: str | Path, *, source: str = "selected") -> Dict[str, Any]:
    path = _validated_selected_config_path(str(app_yaml_path), source=source)
    return _validate_app_cfg(_load_yaml_any(path, source=source), source=source)


def load_startup_config(
    config_dir: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfigSelection:
    config_root = Path(config_dir).resolve()
    environment = os.environ if environ is None else environ
    if CONFIG_ENV_VAR in environment:
        source = "environment"
        raw_path = environment.get(CONFIG_ENV_VAR, "")
        try:
            path = _validated_selected_config_path(raw_path, source=source)
            config = load_app_config(path, source=source)
            return AppConfigSelection(config, path, source, setup_required=not bool(config.get("vault_path")))
        except AppConfigError as exc:
            return AppConfigSelection({}, config_root / EXAMPLE_CONFIG_NAME, source, True, exc)

    local_path = config_root / LOCAL_CONFIG_NAME
    if local_path.exists():
        source = "local"
        try:
            config = load_app_config(local_path, source=source)
            return AppConfigSelection(config, local_path, source, setup_required=not bool(config.get("vault_path")))
        except AppConfigError as exc:
            return AppConfigSelection({}, local_path, source, True, exc)

    source = "example"
    example_path = config_root / EXAMPLE_CONFIG_NAME
    try:
        config = load_app_config(example_path, source=source)
        return AppConfigSelection(config, example_path, source, setup_required=True)
    except AppConfigError as exc:
        return AppConfigSelection({}, example_path, source, True, exc)


def load_project_tables(project_config_path: str) -> List[str]:
    path = Path(project_config_path)
    data = _load_yaml_any(path, source="project")
    assert_path_exists("project_config", project_config_path)
    if not isinstance(data, dict):
        raise ValueError("Project configuration must be a mapping.")
    tables = data.get("tables", [])
    if not isinstance(tables, list) or not all(isinstance(item, str) for item in tables):
        raise ValueError("Project configuration tables must be a list of strings.")
    return tables


def load_table_file(path: str) -> Dict[str, Any]:
    data = _load_yaml_any(Path(path), source="table")
    assert_path_exists("table_file", path)
    if not isinstance(data, dict):
        raise ValueError("Table must be a mapping.")
    name = data.get("name")
    entries = data.get("entries")
    if not isinstance(name, str) or not name.strip() or not isinstance(entries, list):
        raise ValueError("Table requires a name and entries list.")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("scene_concept"), str):
            raise ValueError("Each table entry requires scene_concept text.")
        tags = entry.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("Table entry tags must be a list of strings.")
    return data


def save_app_config(app_yaml_path: str, data: Dict[str, Any]) -> None:
    path = Path(app_yaml_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


__all__ = [
    "AppConfigError",
    "AppConfigSelection",
    "CONFIG_ENV_VAR",
    "EXAMPLE_CONFIG_NAME",
    "LOCAL_CONFIG_NAME",
    "load_app_config",
    "load_startup_config",
    "load_project_tables",
    "load_table_file",
    "save_app_config",
]
