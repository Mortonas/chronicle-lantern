from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceRef:
    character_id: str
    path: str
    section: str
    source_id: str
    excerpt: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ClubAttribute:
    value: str
    sources: tuple[SourceRef, ...]

    def __post_init__(self) -> None:
        value = " ".join(unicodedata.normalize("NFKC", str(self.value or "")).split())
        if not value:
            raise ValueError("ClubAttribute value must be non-empty")
        deduped: list[SourceRef] = []
        seen: set[str] = set()
        for source in self.sources:
            if not isinstance(source, SourceRef) or not source.source_id.strip():
                raise ValueError("ClubAttribute sources must have non-empty source IDs")
            if source.source_id in seen:
                continue
            seen.add(source.source_id)
            deduped.append(source)
        if not deduped:
            raise ValueError("ClubAttribute sources must be non-empty")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "sources", tuple(deduped))

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True)
class NpcIdentity:
    npc_id: str
    current_path: str
    display_name: str
    aliases: tuple[str, ...]
    content_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


@dataclass
class ClubFact:
    summary: str
    fact_type: str
    sources: list[SourceRef] = field(default_factory=list)
    target_name: str = ""
    target_npc_id: str | None = None
    link_targets: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "type": self.fact_type,
            "target_name": self.target_name,
            "target_npc_id": self.target_npc_id,
            "link_targets": list(self.link_targets),
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass
class ClubIndex:
    npc_id: str
    name: str
    path: str
    sheet_revision_hash: str
    schema_version: str
    prompt_version: str
    affiliation: str = ""
    faction: str = ""
    status: str = ""
    roles: list[str] = field(default_factory=list)
    personality_tags: list[str] = field(default_factory=list)
    demeanor: ClubAttribute | None = None
    appearance: ClubAttribute | None = None
    mask_identity: ClubAttribute | None = None
    origin: ClubAttribute | None = None
    pronouns: ClubAttribute | None = None
    relationships: list[ClubFact] = field(default_factory=list)
    current_goals: list[ClubFact] = field(default_factory=list)
    grievances: list[ClubFact] = field(default_factory=list)
    unresolved_business: list[ClubFact] = field(default_factory=list)
    rumor_notes: list[ClubFact] = field(default_factory=list)
    story_hooks: list[ClubFact] = field(default_factory=list)
    political_tags: list[str] = field(default_factory=list)
    recent_relevant_events: list[ClubFact] = field(default_factory=list)

    def source_ids(self) -> set[str]:
        ids: set[str] = set()
        for attribute in (self.demeanor, self.appearance, self.mask_identity, self.origin, self.pronouns):
            if attribute is not None:
                ids.update(source.source_id for source in attribute.sources)
        for fact in [
            *self.relationships,
            *self.current_goals,
            *self.grievances,
            *self.unresolved_business,
            *self.rumor_notes,
            *self.story_hooks,
            *self.recent_relevant_events,
        ]:
            ids.update(source.source_id for source in fact.sources)
        return ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "npc_id": self.npc_id,
            "name": self.name,
            "path": self.path,
            "sheet_revision_hash": self.sheet_revision_hash,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "affiliation": self.affiliation,
            "faction": self.faction,
            "status": self.status,
            "roles": list(self.roles),
            "personality_tags": list(self.personality_tags),
            "demeanor": self.demeanor.to_dict() if self.demeanor is not None else None,
            "appearance": self.appearance.to_dict() if self.appearance is not None else None,
            "mask_identity": self.mask_identity.to_dict() if self.mask_identity is not None else None,
            "origin": self.origin.to_dict() if self.origin is not None else None,
            "pronouns": self.pronouns.to_dict() if self.pronouns is not None else None,
            "relationships": [fact.to_dict() for fact in self.relationships],
            "current_goals": [fact.to_dict() for fact in self.current_goals],
            "grievances": [fact.to_dict() for fact in self.grievances],
            "unresolved_business": [fact.to_dict() for fact in self.unresolved_business],
            "rumor_notes": [fact.to_dict() for fact in self.rumor_notes],
            "story_hooks": [fact.to_dict() for fact in self.story_hooks],
            "political_tags": list(self.political_tags),
            "recent_relevant_events": [fact.to_dict() for fact in self.recent_relevant_events],
        }


@dataclass(frozen=True)
class ClubEvent:
    event_id: str
    attendee_ids: tuple[str, ...]
    late_arrival_id: str
    dashboard: dict[str, Any]
    cache_key: str
    seed: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "attendee_ids": list(self.attendee_ids),
            "late_arrival_id": self.late_arrival_id,
            "dashboard": self.dashboard,
            "cache_key": self.cache_key,
            "seed": self.seed,
            "metadata": dict(self.metadata),
        }


__all__ = ["ClubAttribute", "ClubEvent", "ClubFact", "ClubIndex", "NpcIdentity", "SourceRef"]
