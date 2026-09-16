from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import config_io
from config_io import CONFIG_ENV_VAR, load_startup_config


def _write_config(path: Path, *, vault: Path | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "vault_path": str(vault) if vault else None,
                "model": {"api_key": None, "endpoint": None},
                "rewrite": {"anchor_folder": None},
                "character_schema": {
                    "affiliation_labels": ["Affiliation"],
                    "faction_labels": ["Faction"],
                    "relationship_headings": ["Relationships"],
                    "default_character_tags": ["npc", "character"],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path.resolve()


def test_explicit_environment_config_is_authoritative(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    vault = tmp_path / "vault"
    vault.mkdir()
    selected = _write_config(tmp_path / "selected.yaml", vault=vault)
    _write_config(config_dir / "app.local.yaml")
    _write_config(config_dir / "app.example.yaml")

    result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: str(selected)})

    assert result.valid
    assert result.source == "environment"
    assert result.path == selected
    assert not result.setup_required


@pytest.mark.parametrize(
    ("raw", "category"),
    [("", "path-invalid"), ("relative.yaml", "path-invalid")],
)
def test_invalid_environment_path_never_falls_through(tmp_path: Path, raw: str, category: str) -> None:
    config_dir = tmp_path / "config"
    _write_config(config_dir / "app.local.yaml")
    _write_config(config_dir / "app.example.yaml")

    result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: raw})

    assert not result.valid
    assert result.source == "environment"
    assert result.error is not None and result.error.category == category
    assert str(tmp_path) not in str(result.error)


@pytest.mark.parametrize(
    ("contents", "category"),
    [("", "empty"), ("model: [", "malformed"), ("model: []", "schema-invalid")],
)
def test_invalid_local_file_blocks_example(tmp_path: Path, contents: str, category: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.local.yaml").write_text(contents, encoding="utf-8")
    _write_config(config_dir / "app.example.yaml")

    result = load_startup_config(config_dir, environ={})

    assert not result.valid
    assert result.source == "local"
    assert result.error is not None and result.error.category == category


def test_literal_credentials_and_non_https_endpoints_are_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_config(config_dir / "app.example.yaml")
    (config_dir / "app.local.yaml").write_text(
        "model:\n  api_key: literal-secret\n  endpoint: http://private.invalid\nrewrite:\n  anchor_folder: null\n",
        encoding="utf-8",
    )

    result = load_startup_config(config_dir, environ={})

    assert not result.valid
    assert result.error is not None and result.error.category == "credential-reference-invalid"
    assert "literal-secret" not in str(result.error)


def test_safe_example_enters_setup_mode(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_config(config_dir / "app.example.yaml")

    result = load_startup_config(config_dir, environ={})

    assert result.valid
    assert result.source == "example"
    assert result.setup_required
    assert result.config["vault_path"] is None
    assert result.config["model"].get("endpoint") is None


def test_environment_config_rejects_unc_and_device_paths(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_config(config_dir / "app.example.yaml")
    for unsafe in (r"\\server\share\app.yaml", r"\\?\C:\app.yaml", r"\\.\PIPE\config"):
        result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: unsafe})
        assert not result.valid
        assert result.error is not None and result.error.category == "path-invalid"


def test_supported_environment_reference_is_accepted(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    path = _write_config(config_dir / "app.local.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["model"]["api_key"] = "env:CHRONICLE_PROVIDER_KEY"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _write_config(config_dir / "app.example.yaml")

    result = load_startup_config(config_dir, environ={})

    assert result.valid
    assert result.config["model"]["api_key"] == "env:CHRONICLE_PROVIDER_KEY"


def test_missing_absolute_environment_file_blocks_local(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_config(config_dir / "app.local.yaml")
    _write_config(config_dir / "app.example.yaml")
    result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: str((tmp_path / "missing.yaml").resolve())})
    assert not result.valid
    assert result.error is not None and result.error.category == "missing"


def test_environment_directory_is_rejected(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_config(config_dir / "app.example.yaml")
    result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: str(tmp_path.resolve())})
    assert not result.valid
    assert result.error is not None and result.error.category == "path-invalid"


def test_selected_unreadable_error_is_redacted(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    selected = _write_config(tmp_path / "private-name.yaml")
    _write_config(config_dir / "app.example.yaml")

    def deny(_path, *, source):
        raise config_io.AppConfigError(source, "unreadable")

    monkeypatch.setattr(config_io, "_read_bytes", deny)
    result = load_startup_config(config_dir, environ={CONFIG_ENV_VAR: str(selected)})
    assert not result.valid
    assert result.error is not None and str(result.error) == "environment configuration is unreadable"
    assert "private-name" not in str(result.error)


@pytest.mark.parametrize(
    ("fragment", "category"),
    [
        ("vault_path: relative-vault", "vault-path-invalid"),
        ("rewrite:\n  anchor_folder: relative-folder", "rewrite-path-invalid"),
        ("model:\n  endpoint: http://provider.invalid", "provider-endpoint-invalid"),
    ],
)
def test_schema_rejects_unsafe_operational_values(tmp_path: Path, fragment: str, category: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.local.yaml").write_text(fragment + "\n", encoding="utf-8")
    _write_config(config_dir / "app.example.yaml")
    result = load_startup_config(config_dir, environ={})
    assert not result.valid
    assert result.error is not None and result.error.category == category


def test_readme_mirrors_precedence_and_no_fallthrough() -> None:
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert "Configuration sources and precedence" in readme
    assert "CHRONICLE_LANTERN_CONFIG" in readme
    assert "config/app.local.yaml" in readme
    assert "config/app.example.yaml" in readme
    assert "never falls through" in readme
    assert "redacted" in readme
