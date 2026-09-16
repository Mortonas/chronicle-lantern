import os

def resolve_project_root(config_dir: str) -> str:
    """Given the config directory, return the absolute project root."""
    return os.path.abspath(os.path.dirname(config_dir))

def resolve_table_abs(project_root: str, rel_path: str) -> str:
    """Given the project root and a relative path from config.yaml, return the absolute path."""
    return os.path.normpath(os.path.join(project_root, rel_path))

def assert_path_exists(tag: str, path: str):
    if not os.path.exists(path):
        print(f"[ERROR] {tag}: Path does not exist: {path}")
    else:
        print(f"[OK] {tag}: Path exists: {path}")
