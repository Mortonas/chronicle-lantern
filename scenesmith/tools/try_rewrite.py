from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))

from config_io import load_startup_config
from core.llm_provider import LlmProvider
from core.markdown_rewrite import extract_non_image_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Try the AI rewrite pipeline for a single markdown file.")
    parser.add_argument("path", type=Path, help="Path to a markdown file")
    args = parser.parse_args()

    file_path = args.path.expanduser().resolve()
    if not file_path.exists():
        raise SystemExit(f"File not found: {file_path}")

    selection = load_startup_config(ROOT / "config")
    if not selection.valid:
        raise SystemExit(f"{selection.error.source} configuration is {selection.error.category}")
    if selection.setup_required or not (selection.config.get("model") or {}).get("provider"):
        raise SystemExit("provider setup is incomplete")
    provider = LlmProvider(selection.config)

    original = file_path.read_text(encoding='utf-8', errors='replace')
    existing = extract_non_image_text(original)

    result = provider.generate_markdown(
        filename=file_path.name,
        path=str(file_path),
        existing_non_image_text=existing,
    )
    print(result)


if __name__ == '__main__':
    main()
