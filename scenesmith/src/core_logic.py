
from __future__ import annotations
import os
import glob
import json
import csv
import random

from typing import Any, List, Dict, Set
from app_types import EventTable, EventEntry

def log(msg: str) -> None:
    print(f"[DEBUG] {msg}")

# --- Utility: roll_scene_concept ---
def roll_scene_concept(table: Any, rng: random.Random = None) -> EventEntry:
    """Randomly select an entry from a table (EventTable, dict, or list). Returns EventEntry object."""
    if rng is None:
        rng = random.Random()
    entries = getattr(table, "entries", None) or table.get("entries") if isinstance(table, dict) else table
    if not entries:
        raise ValueError("No entries to select from.")
    entry = rng.choice(entries)
    # Convert to EventEntry if needed
    if isinstance(entry, EventEntry):
        return entry
    if isinstance(entry, dict):
        scene_concept = entry.get("scene_concept") or entry.get("concept") or str(entry)
        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else [t.strip() for t in str(entry.get("tags", "")).split(",") if t.strip()]
        return EventEntry(scene_concept=scene_concept, tags=tags)
    # If entry is a string or other type, wrap as EventEntry
    return EventEntry(scene_concept=str(entry), tags=[])

# --- Utility: build_expression ---
def build_expression(use_table: bool, base_tags: list, extra_groups_ui: list) -> tuple:
    """Normalize tags and groups for search logic."""
    base = [t.strip().lstrip("#").lower() for t in base_tags if t.strip().lstrip("#")] if use_table else []
    groups = []
    for tags, op in extra_groups_ui:
        tnorm = [t.strip().lstrip("#").lower() for t in tags if t.strip().lstrip("#")]
        if tnorm:
            groups.append({"tags": tnorm, "op": op.upper()})
    return base, groups

# Minimal, dependency-free load_tables
def load_tables(folder: str) -> dict[str, EventTable]:
    """Load all .yml/.yaml/.json/.csv files in a folder into a dict by basename, returning EventTable objects."""
    result = {}
    for ext in ("*.yml", "*.yaml", "*.json", "*.csv"):
        for path in glob.glob(os.path.join(folder, ext)):
            key = os.path.splitext(os.path.basename(path))[0]
            abs_path = os.path.abspath(path)
            try:
                if path.endswith((".yml", ".yaml")):
                    import yaml
                    with open(path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                elif path.endswith(".json"):
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                elif path.endswith(".csv"):
                    with open(path, encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        data = list(reader)
                else:
                    continue
                # Normalize to EventTable shape
                if isinstance(data, dict) and "entries" in data:
                    entries = data["entries"]
                    name = data.get("name", key)
                elif isinstance(data, list):
                    entries = data
                    name = key
                else:
                    continue
                # Convert all entries to EventEntry
                event_entries = []
                for entry in entries:
                    if isinstance(entry, EventEntry):
                        event_entries.append(entry)
                    elif isinstance(entry, dict):
                        scene_concept = entry.get("scene_concept") or entry.get("concept") or str(entry)
                        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else [t.strip() for t in str(entry.get("tags", "")).split(",") if t.strip()]
                        event_entries.append(EventEntry(scene_concept=scene_concept, tags=tags))
                    else:
                        event_entries.append(EventEntry(scene_concept=str(entry), tags=[]))
                result[key] = EventTable(
                    name=name,
                    entries=event_entries,
                    source_path=abs_path,
                    rel_path_from_project=os.path.relpath(abs_path, start=os.getcwd())
                )
            except Exception as e:
                log(f"[load_tables] Failed to load {path}: {e}")
    return result

__all__ = [
    "load_tables",
    "roll_scene_concept",
    "build_expression",
    "log"
]


