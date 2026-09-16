from __future__ import annotations

from pathlib import Path

from core.character_art import extract_first_image_embed, resolve_character_art


def test_extract_first_image_embed_supports_obsidian_image_links() -> None:
    text = "![[aae7af82-dc08-4a05-a9de-15ae0ecb277c.png]]\n#council\n"

    assert extract_first_image_embed(text) == "aae7af82-dc08-4a05-a9de-15ae0ecb277c.png"


def test_extract_first_image_embed_supports_size_alias() -> None:
    text = "![[portrait.webp|200]]\n"

    assert extract_first_image_embed(text) == "portrait.webp"


def test_resolve_character_art_from_asset_folder(tmp_path: Path) -> None:
    note = tmp_path / "Characters" / "Maldavis.md"
    note.parent.mkdir()
    art_dir = tmp_path / "Assets" / "Character art"
    art_dir.mkdir(parents=True)
    image = art_dir / "portrait.png"
    image.write_bytes(b"not really an image")
    note.write_text("![[portrait.png]]\n", encoding="utf-8")

    assert resolve_character_art(note, vault_root=tmp_path) == image
