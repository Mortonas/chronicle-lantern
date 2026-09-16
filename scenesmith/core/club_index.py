from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from core.club_cache import ClubCacheService
from core.club_hashing import file_content_hash, stable_hash
from core.club_models import ClubAttribute, ClubFact, ClubIndex, NpcIdentity, SourceRef
from core.relationship_graph import normalize_wiki_link_target
from core.tag_index import extract_hashtags

CLUB_INDEX_SCHEMA_VERSION = "club_index_v7"
CLUB_INDEX_PROMPT_VERSION = "deterministic_index_v5"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TYPED_FIELD_RE = re.compile(
    r"^\s*>?\s*(?:[-*+]\s*)?(?:\*\*)?"
    r"(demeanou?r|appearance|mask|origin|pronouns)"
    r"(?:\*\*)?\s*[:\-]\s*(?:\*\*)?\s*(.*)$",
    re.IGNORECASE,
)
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
REL_TYPE_RE = re.compile(r"\(([^)]+)\)")

SECTION_FACT_TYPES = {
    "at a glance motives": "goal",
    "relationships": "relationship",
    "plots and schemes": "goal",
    "current goals": "goal",
    "goals": "goal",
    "grievances": "grievance",
    "unresolved business": "unresolved_business",
    "rumors": "rumor",
    "rumour": "rumor",
    "rumor notes": "rumor",
    "whispers": "rumor",
    "recent events": "recent_event",
    "recent relevant events": "recent_event",
    "dependents and tools": "relationship",
    "advantages flaws": "relationship",
    "story hooks": "story_hook",
}

PERSONALITY_SECTIONS = {"personality", "mannerisms"}
TYPED_SECTION_FIELDS = {
    "demeanor": "demeanor",
    "demeanour": "demeanor",
    "character demeanor": "demeanor",
    "character demeanour": "demeanor",
    "appearance": "appearance",
    "mask": "mask_identity",
    "origin": "origin",
    "pronouns": "pronouns",
}
TYPED_LABEL_FIELDS = dict(TYPED_SECTION_FIELDS)
SUPPORTED_PRONOUNS = {"he/him", "she/her", "they/them", "it/its"}
RELATIONSHIP_TYPE_HINTS = (
    "adversary",
    "ally",
    "enemy",
    "friend",
    "friendship",
    "informant",
    "mentor",
    "patron",
    "retainer",
    "rival",
)


class ClubIndexStore:
    def __init__(self, cache: ClubCacheService, *, character_schema: dict[str, Any] | None = None) -> None:
        self.cache = cache
        self.character_schema = character_schema or {}

    def get_or_build(self, identity: NpcIdentity, path: Path | str) -> ClubIndex:
        p = Path(path)
        revision = file_content_hash(p)
        cache_name = f"{identity.npc_id}.json"
        cached = self.cache.read_json("indexes", cache_name, default=None)
        if _valid_cached_index(cached, revision):
            return club_index_from_dict(cached["index"])
        index = build_club_index(
            identity, p, sheet_revision_hash=revision, character_schema=self.character_schema
        )
        self.cache.write_json(
            "indexes", cache_name,
            data={"cache_key": _index_cache_key(identity.npc_id, revision, self.cache.cache_identity), "index": index.to_dict()},
        )
        return index


def build_club_index(
    identity: NpcIdentity,
    path: Path,
    *,
    sheet_revision_hash: str | None = None,
    character_schema: dict[str, Any] | None = None,
) -> ClubIndex:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _split_sections(text)
    tags = extract_hashtags(text)
    fields = _extract_fields(text, character_schema or {})
    affiliation = fields.get("affiliation", "")
    faction = fields.get("faction", "")
    roles = _role_values(fields)
    status = fields.get("status", "")
    personality_tags = _personality_tags(sections)
    typed_attributes = _typed_attributes(sections, identity=identity, path=path)

    index = ClubIndex(
        npc_id=identity.npc_id,
        name=identity.display_name,
        path=str(path),
        sheet_revision_hash=sheet_revision_hash or file_content_hash(path),
        schema_version=CLUB_INDEX_SCHEMA_VERSION,
        prompt_version=CLUB_INDEX_PROMPT_VERSION,
        affiliation=affiliation,
        faction=faction,
        status=status,
        roles=roles,
        personality_tags=personality_tags,
        demeanor=typed_attributes["demeanor"],
        appearance=typed_attributes["appearance"],
        mask_identity=typed_attributes["mask_identity"],
        origin=typed_attributes["origin"],
        pronouns=typed_attributes["pronouns"],
        political_tags=[],
    )

    for section in sections:
        section_key = _section_key(section["title"])
        default_fact_type = SECTION_FACT_TYPES.get(section_key)
        for line_no, line in section["lines"]:
            fact_type = _fact_type_for_line(section_key, line, default_fact_type)
            if not fact_type:
                continue
            fact = _fact_from_line(
                line,
                fact_type=fact_type,
                identity=identity,
                path=path,
                section=section["title"],
                line_no=line_no,
            )
            if fact is None:
                continue
            if fact_type == "relationship":
                index.relationships.append(fact)
            elif fact_type == "goal":
                index.current_goals.append(fact)
            elif fact_type == "grievance":
                index.grievances.append(fact)
            elif fact_type == "unresolved_business":
                index.unresolved_business.append(fact)
            elif fact_type == "rumor":
                index.rumor_notes.append(fact)
            elif fact_type == "story_hook":
                index.story_hooks.append(fact)
            elif fact_type == "recent_event":
                index.recent_relevant_events.append(fact)
    return index


def club_index_from_dict(data: dict[str, Any]) -> ClubIndex:
    def facts(name: str) -> list[ClubFact]:
        return [_fact_from_dict(item) for item in data.get(name) or []]

    typed_fields = ("demeanor", "appearance", "mask_identity", "origin", "pronouns")
    missing_typed_fields = [field for field in typed_fields if field not in data]
    if missing_typed_fields:
        raise ValueError(f"missing typed attribute fields: {', '.join(missing_typed_fields)}")

    return ClubIndex(
        npc_id=str(data["npc_id"]),
        name=str(data.get("name") or ""),
        path=str(data.get("path") or ""),
        sheet_revision_hash=str(data.get("sheet_revision_hash") or ""),
        schema_version=str(data.get("schema_version") or ""),
        prompt_version=str(data.get("prompt_version") or ""),
        affiliation=str(data.get("affiliation") or ""),
        faction=str(data.get("faction") or ""),
        status=str(data.get("status") or ""),
        roles=[str(item) for item in data.get("roles") or []],
        personality_tags=[str(item) for item in data.get("personality_tags") or []],
        demeanor=_attribute_from_dict(data.get("demeanor"), field="demeanor"),
        appearance=_attribute_from_dict(data.get("appearance"), field="appearance"),
        mask_identity=_attribute_from_dict(data.get("mask_identity"), field="mask_identity"),
        origin=_attribute_from_dict(data.get("origin"), field="origin"),
        pronouns=_attribute_from_dict(data.get("pronouns"), field="pronouns"),
        relationships=facts("relationships"),
        current_goals=facts("current_goals"),
        grievances=facts("grievances"),
        unresolved_business=facts("unresolved_business"),
        rumor_notes=facts("rumor_notes"),
        story_hooks=facts("story_hooks"),
        political_tags=[str(item) for item in data.get("political_tags") or []],
        recent_relevant_events=facts("recent_relevant_events"),
    )


def _attribute_from_dict(data: Any, *, field: str) -> ClubAttribute | None:
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {"value", "sources"}:
        raise ValueError(f"{field} must be null or a sourced attribute object")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{field}.sources must be a non-empty list")
    parsed_sources: list[SourceRef] = []
    for source in sources:
        if not isinstance(source, dict) or not str(source.get("source_id") or "").strip():
            raise ValueError(f"{field}.sources must contain valid SourceRef objects")
        parsed_sources.append(
            SourceRef(
                character_id=str(source.get("character_id") or ""),
                path=str(source.get("path") or ""),
                section=str(source.get("section") or ""),
                source_id=str(source.get("source_id") or ""),
                excerpt=str(source.get("excerpt") or ""),
            )
        )
    attribute = ClubAttribute(value=str(data.get("value") or ""), sources=tuple(parsed_sources))
    if field == "pronouns" and attribute.value not in SUPPORTED_PRONOUNS:
        raise ValueError("pronouns contains an unsupported canonical pair")
    return attribute


def _fact_from_dict(data: dict[str, Any]) -> ClubFact:
    fact_type = str(data.get("type") or "")
    fact_type_key = fact_type.lower()
    raw_target_npc_id = data.get("target_npc_id")
    return ClubFact(
        summary=str(data.get("summary") or ""),
        fact_type=fact_type,
        target_name=str(data.get("target_name") or ""),
        target_npc_id=None if fact_type_key == "rumor" else str(raw_target_npc_id or ""),
        link_targets=tuple(clean for item in data.get("link_targets") or [] if (clean := str(item).strip())),
        sources=[
            SourceRef(
                character_id=str(source.get("character_id") or ""),
                path=str(source.get("path") or ""),
                section=str(source.get("section") or ""),
                source_id=str(source.get("source_id") or ""),
                excerpt=str(source.get("excerpt") or ""),
            )
            for source in data.get("sources") or []
            if isinstance(source, dict)
        ],
    )


def _valid_cached_index(cached: Any, revision: str) -> bool:
    if not isinstance(cached, dict):
        return False
    index = cached.get("index")
    if not isinstance(index, dict):
        return False
    versions_match = (
        index.get("sheet_revision_hash") == revision
        and index.get("schema_version") == CLUB_INDEX_SCHEMA_VERSION
        and index.get("prompt_version") == CLUB_INDEX_PROMPT_VERSION
    )
    if not versions_match:
        return False
    try:
        club_index_from_dict(index)
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _index_cache_key(npc_id: str, revision: str, cache_identity: str = "isolated-debug-cache") -> str:
    return stable_hash(
        {
            "npc_id": npc_id,
            "cache_identity": cache_identity,
            "sheet_revision_hash": revision,
            "schema_version": CLUB_INDEX_SCHEMA_VERSION,
            "prompt_version": CLUB_INDEX_PROMPT_VERSION,
        }
    )


def _split_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current = {"title": "Preamble", "lines": []}
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if match:
            sections.append(current)
            current = {"title": match.group(2).strip(), "lines": []}
            continue
        if line.strip():
            current["lines"].append((line_no, line))
    sections.append(current)
    return sections


def _extract_fields(text: str, character_schema: dict[str, Any]) -> dict[str, str]:
    affiliation_labels = character_schema.get("affiliation_labels") or ["Affiliation"]
    faction_labels = character_schema.get("faction_labels") or ["Faction"]
    label_map = {
        **{str(label).casefold(): "affiliation" for label in affiliation_labels},
        **{str(label).casefold(): "faction" for label in faction_labels},
        "status": "status",
        "role": "role",
        "roles": "roles",
        "group": "group",
        "epitaph": "epitaph",
    }
    labels = "|".join(re.escape(label) for label in sorted(label_map, key=len, reverse=True))
    field_re = re.compile(
        rf"^\s*>?\s*(?:[-*+]\s*)?(?:\*\*)?({labels})(?:\*\*)?\s*[:\-]\s*(?:\*\*)?\s*(.+)$",
        re.IGNORECASE,
    )
    fields: dict[str, str] = {}
    for line in text.splitlines()[:120]:
        match = field_re.match(line)
        if match:
            fields[label_map[match.group(1).casefold()]] = _clean_summary(match.group(2))
    return fields


def _personality_tags(sections: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for section in sections:
        if _section_key(section["title"]) not in PERSONALITY_SECTIONS:
            continue
        for _line_no, line in section["lines"][:8]:
            clean = _strip_line_label(_clean_summary(line))
            if clean:
                tags.extend(part.lower() for part in re.split(r"[,;/]", clean) if part.strip())
    return list(dict.fromkeys(tag.strip() for tag in tags if 2 <= len(tag.strip()) <= 40))[:12]


def _typed_attributes(
    sections: list[dict[str, Any]],
    *,
    identity: NpcIdentity,
    path: Path,
) -> dict[str, ClubAttribute | None]:
    candidates: dict[str, list[tuple[int, str | None, SourceRef]]] = {
        "demeanor": [],
        "appearance": [],
        "mask_identity": [],
        "origin": [],
        "pronouns": [],
    }
    for section in sections:
        section_key = _section_key(section["title"])
        section_field = TYPED_SECTION_FIELDS.get(section_key)
        for line_no, line in section["lines"]:
            match = TYPED_FIELD_RE.match(line)
            if match:
                field = TYPED_LABEL_FIELDS.get(_section_key(match.group(1)))
                raw_value = match.group(2)
            elif section_field:
                field = section_field
                raw_value = line
            else:
                continue
            if not field:
                continue
            display_value = _normalized_attribute_display(raw_value)
            if field == "pronouns" and display_value:
                display_value = _normalized_pronoun_value(display_value)
            excerpt = _clean_summary(line)
            candidates[field].append(
                (
                    line_no,
                    display_value,
                    SourceRef(
                        character_id=identity.npc_id,
                        path=str(path),
                        section=section["title"],
                        source_id=stable_hash(
                            {
                                "npc_id": identity.npc_id,
                                "section": section["title"],
                                "line": line_no,
                                "text": excerpt,
                            }
                        )[:16],
                        excerpt=excerpt[:240],
                    ),
                )
            )
    return {
        field: _merge_attribute_candidates(values)
        for field, values in candidates.items()
    }


def _normalized_attribute_display(value: Any) -> str | None:
    clean = _clean_summary(str(value or ""))
    clean = " ".join(unicodedata.normalize("NFKC", clean).split())
    return clean or None


def _attribute_comparison_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _normalized_pronoun_value(value: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    return normalized if normalized in SUPPORTED_PRONOUNS else None


def _merge_attribute_candidates(candidates: list[tuple[int, str | None, SourceRef]]) -> ClubAttribute | None:
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda candidate: candidate[0])
    if any(value is None or not source.source_id for _line_no, value, source in ordered):
        return None
    values = [value for _line_no, value, _source in ordered if value is not None]
    keys = {_attribute_comparison_key(value) for value in values}
    if len(keys) != 1:
        return None
    return ClubAttribute(
        value=values[0],
        sources=tuple(source for _line_no, _value, source in ordered),
    )


def _fact_from_line(
    line: str,
    *,
    fact_type: str,
    identity: NpcIdentity,
    path: Path,
    section: str,
    line_no: int,
) -> ClubFact | None:
    summary = _clean_summary(line)
    if not _is_semantic_summary(summary):
        return None
    target_name = _target_name_from_line(line)
    link_targets = tuple(dict.fromkeys(target for target in _link_targets_from_line(line) if target))
    relationship_type = _relationship_type_from_line(line, fact_type=fact_type)
    source = SourceRef(
        character_id=identity.npc_id,
        path=str(path),
        section=section,
        source_id=stable_hash({"npc_id": identity.npc_id, "section": section, "line": line_no, "text": summary})[:16],
        excerpt=summary[:240],
    )
    target_npc_id = None if relationship_type == "rumor" else ""
    return ClubFact(
        summary=summary,
        fact_type=relationship_type,
        target_name=target_name,
        target_npc_id=target_npc_id,
        link_targets=link_targets,
        sources=[source],
    )


def _clean_summary(text: str) -> str:
    text = re.sub(r"!\[\[[^\]]+\]\]", "", str(text or ""))
    text = re.sub(r"^\s*>\s?", "", text.strip())
    text = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", text.strip())
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", lambda match: match.group(2).strip(), text)
    text = re.sub(r"\[\[([^\]]+)\]\]", lambda match: normalize_wiki_link_target(match.group(1)), text)
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"`", "", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_semantic_summary(summary: str) -> bool:
    clean = re.sub(r"[\s\-–—_*`|:.;]+", "", str(summary or ""))
    if not clean:
        return False
    if clean in {"[]", "()"}:
        return False
    return any(char.isalnum() for char in clean)


def _split_csv(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;/]", text) if part.strip()]


def _role_values(fields: dict[str, str]) -> list[str]:
    roles: list[str] = []
    roles.extend(_split_csv(fields.get("role") or fields.get("roles") or ""))
    if fields.get("group"):
        roles.append(fields["group"])
    roles.extend(_split_epitaph_roles(fields.get("epitaph") or ""))
    result: list[str] = []
    seen: set[str] = set()
    for role in roles:
        clean = _clean_summary(role)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result[:8]


def _split_epitaph_roles(text: str) -> list[str]:
    clean = _clean_summary(text)
    if not clean:
        return []
    return [part.strip() for part in re.split(r"\s+\band\b\s+|[,;/]", clean, flags=re.IGNORECASE) if part.strip()]


def _section_key(title: str) -> str:
    key = _clean_summary(title).lower()
    key = re.sub(r"[:].*$", "", key)
    key = re.sub(r"[–—-]", " ", key)
    key = re.sub(r"[^a-z0-9]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _line_label(line: str) -> str:
    clean = _clean_summary(line)
    if ":" not in clean:
        return ""
    return clean.split(":", 1)[0].strip()


def _strip_line_label(clean: str) -> str:
    if ":" not in clean:
        return clean
    label, value = clean.split(":", 1)
    if 1 <= len(label.strip()) <= 40:
        return value.strip()
    return clean


def _fact_type_for_line(section_key: str, line: str, default_fact_type: str | None) -> str | None:
    label = _line_label(line).lower()
    if section_key == "at a glance motives":
        if label == "pressure point":
            return "grievance"
        if label in {"primary drive", "secondary aim"}:
            return "goal"
    if section_key == "background details" and label == "ambition":
        return "goal"
    return default_fact_type


def _target_name_from_line(line: str) -> str:
    link_match = WIKI_LINK_RE.search(line)
    if link_match:
        return normalize_wiki_link_target(link_match.group(1))
    label = _line_label(line)
    if not label:
        return ""
    paren_match = REL_TYPE_RE.search(label)
    if paren_match:
        if label.lower().startswith(("advantage", "flaw")):
            return _clean_summary(paren_match.group(1))
        return _clean_summary(label[: paren_match.start()])
    return ""


def _link_targets_from_line(line: str) -> list[str]:
    return [normalize_wiki_link_target(match) for match in WIKI_LINK_RE.findall(line)]


def _relationship_type_from_line(line: str, *, fact_type: str) -> str:
    if fact_type != "relationship":
        return fact_type
    label = _line_label(line).lower()
    for hint in RELATIONSHIP_TYPE_HINTS:
        if hint in label:
            return hint
    rel_match = REL_TYPE_RE.search(line)
    if rel_match:
        return _clean_summary(rel_match.group(1)).lower()
    return fact_type


__all__ = [
    "CLUB_INDEX_PROMPT_VERSION",
    "CLUB_INDEX_SCHEMA_VERSION",
    "ClubIndexStore",
    "build_club_index",
    "club_index_from_dict",
]
