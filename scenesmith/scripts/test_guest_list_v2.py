import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from app.guest_list import generate_guest_list_v2


def main() -> None:
    rg_path = r"C:\path\to\rg.exe"
    vault = r"C:\path\to\your\vault"

    picks = generate_guest_list_v2(
        rg_path=rg_path,
        vault=vault,
        preset_tags=["Council", "Executives", "Merchants"],
        free_text="#Independent Newcomer",
        count=5,
        anchor_tag="Council",
        extra_groups_ui=[],
        shuffle_seed=42,
    )

    for idx, pick in enumerate(picks, 1):
        print(
            f"{idx}. {pick.file_path} | single={pick.single_tag} | anchors={list(pick.anchor_tags)}"
        )


if __name__ == "__main__":
    main()
