import sys
import os
from core_logic import load_tables
from path_resolver import resolve_project_root, assert_path_exists

def main():
    if len(sys.argv) > 1:
        config_dir = sys.argv[1]
    else:
        config_dir = os.path.join(os.path.dirname(__file__), '../config')
    config_dir = os.path.abspath(config_dir)
    project_root = resolve_project_root(config_dir)
    print(f"[LINT] Project root: {project_root}")
    tables = load_tables(config_dir)
    for key, table in tables.items():
        print(f"[LINT] Table: {key}")
        print(f"  source_path: {table.source_path}")
        print(f"  rel_path_from_project: {table.rel_path_from_project}")
        print(f"  exists: {os.path.exists(table.source_path)}")
        if os.path.exists(table.source_path):
            size = os.path.getsize(table.source_path)
            print(f"  size: {size}")
            with open(table.source_path, 'r', encoding='utf-8', errors='replace') as f:
                first_line = f.readline().strip()
            print(f"  first_line: {first_line}")
        else:
            print(f"  [ERROR] Table file missing: {table.source_path}")
    # Check default table
    from config_io import load_app_config
    app_cfg_path = os.path.join(config_dir, 'app.yaml')
    app_cfg = load_app_config(app_cfg_path)
    default_key = app_cfg.get('ui', {}).get('default_table')
    if default_key not in tables:
        print(f"[WARN] ui.default_table '{default_key}' not found in loaded tables.")
        print(f"[LINT] Fallback: {next(iter(tables), None)}")
    else:
        print(f"[LINT] Default table OK: {default_key}")
    # Exit nonzero if any table missing or empty
    failed = False
    for table in tables.values():
        if not os.path.exists(table.source_path) or os.path.getsize(table.source_path) == 0:
            failed = True
    if failed:
        print("[LINT] One or more tables missing or empty.")
        sys.exit(1)
    print("[LINT] All tables present and non-empty.")
    sys.exit(0)

if __name__ == "__main__":
    main()
