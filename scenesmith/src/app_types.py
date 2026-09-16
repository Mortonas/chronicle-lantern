from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Literal, Dict, Set, Optional

TagOp = Literal["AND", "OR"]

@dataclass
class EventEntry:
    scene_concept: str
    tags: List[str]


@dataclass
class EventTable:
    name: str
    entries: List[EventEntry]
    source_path: str           # absolute
    rel_path_from_project: str # as in config.yaml

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, idx):
        return self.entries[idx]

@dataclass
class TagGroup:
    tags: List[str]
    join_with_prev: TagOp = "AND"  # how this group combines with the prior result

@dataclass
class TagExpression:
    use_table_tags: bool = True
    base_tags: List[str] = field(default_factory=list)
    extra_groups: List[TagGroup] = field(default_factory=list)

@dataclass
class SelectionResult:
    scene_concept: str
    primary_files: List[str]
    lore_files: List[str]
    npc_count: int
    location: str
    active_tags: List[str]  # flattened tags that were used
    chosen_tags: List[str]  # tags actually assigned to NPCs
    npc_tag_map: Dict[int, str]  # NPC index (1-based) to tag
