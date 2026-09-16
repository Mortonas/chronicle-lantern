from __future__ import annotations


def main() -> None:
    from app import main as production_main

    production_main()


if __name__ == "__main__":
    main()
