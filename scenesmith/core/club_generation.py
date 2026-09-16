from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

from core.club_cache import CacheDescriptor, ClubCacheService
from core.club_tag_groups import tag_arrangement
from core.club_hashing import stable_hash
from core.club_identity import IDENTITY_REGISTRY_VERSION, NpcIdentityRegistry
from core.club_index import CLUB_INDEX_PROMPT_VERSION, CLUB_INDEX_SCHEMA_VERSION, ClubIndexStore
from core.club_models import ClubAttribute, ClubEvent, ClubFact, ClubIndex, NpcIdentity
from core.llm_provider import LlmProvider
from core.club_prep import (
    PrepContext, PrepEngine, RequestBudget, capture_document, fallback_scene,
    prep_references, visible_scene, authentication_failure, CLUB_PREP_REASONING_VERSION,
    CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION, CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION,
    CLUB_PREP_READING_PROMPT_VERSION, CLUB_PREP_READING_SCHEMA_VERSION,
    CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION,
)

CLUB_DASHBOARD_SCHEMA_VERSION = "club_dashboard_v20"
CLUB_DASHBOARD_PROMPT_VERSION = "club_dashboard_prompt_v18"
CLUB_DASHBOARD_AI_REQUEST_VERSION = "club_dashboard_ai_request_v9"
CLUB_PANEL_SCHEMA_VERSION = "club_panel_v12"
CLUB_PANEL_PROMPT_VERSION = "club_panel_prompt_v15"
CLUB_PANEL_AI_REQUEST_VERSION = "club_panel_ai_request_v8"
CLUB_SKELETON_VERSION = "club_skeleton_v7"
CLUB_RUMOR_MATERIALIZATION_VERSION = "club_rumor_materialization_v3"
CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET = 9000
CLUB_PANEL_PROMPT_TOKEN_BUDGET = 4500
PROMPT_SOURCE_EXCERPT_CHARS = 180
PROMPT_SUMMARY_CHARS = 220
PANEL_RELATIONSHIP_LIMIT = 20
PANEL_FACT_LIMIT = 24
DASHBOARD_RELATIONSHIP_LIMIT = 120
DASHBOARD_FACT_LIMIT = 80
DASHBOARD_RANKED_CONNECTION_LIMIT = 12
RUMOR_LIMIT_OPTIONS = (3, 5, 7)
DASHBOARD_VISIBLE_LIST_FIELDS = ("hot_connections", "possible_pressure", "rumors_in_circulation", "guest_brief")
NPC_PANEL_VISIBLE_LIST_FIELDS = ("likely_conversation", "sensitive_subjects")
NPC_PANEL_PRESENTATION_TEXT_LIMIT = 180

GROUNDED_DASHBOARD_FIELDS = {
    "rumors",
    "possible_drama",
    "interesting_connections",
    "unresolved_business",
    "opportunities",
    "top_connections",
}
SOURCE_REF_FIELDS = ("character_id", "path", "section", "source_id", "excerpt")
SOURCE_REF_FIELD_SET = set(SOURCE_REF_FIELDS)
COMPACT_SOURCE_REF_FIELD_SET = {"source_id"}
FORBIDDEN_DASHBOARD_KEYS = {"timeline", "event_timeline", "planned_movements", "npc_movements", "planned_npc_movement"}
RUMOR_TRUTH_KEYS = {"truth", "truth_rating", "is_true", "confirmed", "rating"}
FACT_TYPE_WEIGHTS = {
    "blackmail": 12,
    "grave misconduct": 12,
    "commit grave misconduct against": 12,
    "coercive bond": 11,
    "coercively bound": 11,
    "manipulation": 11,
    "coercion": 11,
    "toy": 10,
    "secret": 10,
    "leverage": 10,
    "betrayal": 10,
    "betray": 10,
    "exposure": 10,
    "debt": 9,
    "dependency": 9,
    "dependent": 9,
    "favor": 9,
    "enemy": 9,
    "rival": 9,
    "rivalry": 9,
    "hostile": 9,
    "hostility": 9,
    "paramour": 8,
    "lover": 8,
    "intimacy": 8,
    "distrust": 7,
    "grievance": 7,
    "unresolved_business": 6,
    "relationship": 4,
    "ally": 4,
    "patron": 4,
    "opposition": 3,
    "political opposition": 3,
    "contact": 3,
    "business": 3,
    "useful": 3,
    "interest": 3,
    "goal": 3,
    "rumor": 3,
    "recent_event": 2,
}
DRAMA_TYPE_WEIGHTS = {
    "blackmail": 12,
    "grave misconduct": 12,
    "commit grave misconduct against": 12,
    "coercive bond": 11,
    "coercively bound": 11,
    "compulsion": 11,
    "manipulation": 11,
    "coercion": 11,
    "coerce": 11,
    "leverage": 10,
    "betray": 10,
    "betrayal": 10,
    "expose": 10,
    "exposure": 10,
    "blackmail material": 10,
    "toy": 10,
    "debt": 9,
    "dependency": 9,
    "dependent": 9,
    "favor": 9,
    "enemy": 9,
    "rival": 9,
    "rivalry": 9,
    "hostile": 9,
    "hostility": 9,
    "secret": 9,
    "scandal": 9,
    "paramour": 8,
    "lover": 8,
    "intimacy": 8,
    "distrust": 7,
    "grievance": 7,
    "opposition": 3,
    "political opposition": 3,
    "unresolved_business": 5,
}
DRAMA_TYPES = {
    "blackmail",
    "coercive bond",
    "coercively bound",
    "coercion",
    "grave misconduct",
    "commit grave misconduct against",
    "manipulation",
    "leverage",
    "betrayal",
    "exposure",
    "toy",
    "debt",
    "dependency",
    "dependent",
    "favor",
    "enemy",
    "rival",
    "rivalry",
    "hostile",
    "hostility",
    "distrust",
    "opposition",
    "political opposition",
    "grievance",
    "unresolved_business",
}
OPPORTUNITY_TYPES = {"goal", "interest"}
OPPORTUNITY_ACTION_KEYWORDS = (
    "ask",
    "bargain",
    "blackmail",
    "buy",
    "carry",
    "confront",
    "debt",
    "deliver",
    "expose",
    "favor",
    "interrupt",
    "leverage",
    "offer",
    "pressure",
    "recruit",
    "sabotage",
    "sell",
    "steal",
    "warn",
    "warning",
)
RUMOR_ACTION_KEYWORDS = (
    *OPPORTUNITY_ACTION_KEYWORDS,
    "basement",
    "courier",
    "dawn",
    "leaving",
    "missing",
    "plan",
    "plans",
    "plot",
    "question",
    "seen",
    "threat",
    "turning point",
)
PRESSURE_KEYWORDS = (
    "betray",
    "blackmail",
    "coercive bond",
    "coercively bound",
    "coerc",
    "death threat",
    "danger",
    "debt",
    "dependen",
    "grave misconduct",
    "expose",
    "exposure",
    "exploit",
    "hostil",
    "leverage",
    "manipulat",
    "sacrifice",
    "scandal",
    "secret",
    "studying",
    "test subject",
    "using",
)
PRESSURE_LABELS = {
    "betray": "betrayal",
    "blackmail": "blackmail",
    "coercive bond": "coercive bond",
    "coercively bound": "coercive bond",
    "favor": "favor",
    "coerc": "coercion",
    "danger": "danger",
    "debt": "debt",
    "dependen": "dependency",
    "grave misconduct": "grave misconduct",
    "distrust": "distrust",
    "enemy": "enemy",
    "expose": "exposure",
    "exposure": "exposure",
    "hostil": "hostility",
    "leverage": "leverage",
    "manipulat": "manipulation",
    "paramour": "paramour",
    "rival": "rivalry",
    "sacrifice": "sacrifice",
    "scandal": "scandal",
    "studying": "exploitation",
    "test subject": "exploitation",
    "using": "exploitation",
}
GENERIC_ROOM_RELATIONSHIP_TYPES = {
    "",
    "associate",
    "association",
    "connected",
    "connection",
    "contact",
    "other",
    "relationship",
    "unknown",
}
PRONOUN_SUBJECTS = {
    "he/him": "He",
    "she/her": "She",
    "they/them": "They",
    "it/its": "It",
}
SENSITIVE_DEBUG_KEY_PARTS = ("api_key", "token", "secret", "password", "authorization", "auth")
SAFE_DEBUG_KEY_EXCEPTIONS = {"cache_key", "source_id", "source_ids", "source_map"}


class ClubGenerationError(RuntimeError):
    pass


def club_cache_owner_versions() -> dict[str, str]:
    """Every serialized, prompt, request, and key owner in the generic cache root."""
    return {
        "dashboard.ai_request": CLUB_DASHBOARD_AI_REQUEST_VERSION,
        "dashboard.prompt": CLUB_DASHBOARD_PROMPT_VERSION,
        "dashboard.schema": CLUB_DASHBOARD_SCHEMA_VERSION,
        "identity.schema": IDENTITY_REGISTRY_VERSION,
        "index.prompt": CLUB_INDEX_PROMPT_VERSION,
        "index.schema": CLUB_INDEX_SCHEMA_VERSION,
        "panel.ai_request": CLUB_PANEL_AI_REQUEST_VERSION,
        "panel.prompt": CLUB_PANEL_PROMPT_VERSION,
        "panel.schema": CLUB_PANEL_SCHEMA_VERSION,
        "prep.encounter_cue": CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION,
        "prep.npc_conversation": CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION,
        "prep.reading_prompt": CLUB_PREP_READING_PROMPT_VERSION,
        "prep.reading_schema": CLUB_PREP_READING_SCHEMA_VERSION,
        "prep.reasoning": CLUB_PREP_REASONING_VERSION,
        "prep.rumor_guidance": CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION,
        "rumor.materialization": CLUB_RUMOR_MATERIALIZATION_VERSION,
        "skeleton.schema": CLUB_SKELETON_VERSION,
    }


class ClubPromptTooLargeError(ClubGenerationError):
    def __init__(self, message: str, *, estimated_tokens: int, budget: int) -> None:
        super().__init__(message)
        self.estimated_tokens = estimated_tokens
        self.budget = budget


class ClubProvider(Protocol):
    def generate_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        strip_response: bool = True,
        request_options: dict[str, Any] | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class _AdmittedFactRecord:
    section: str
    item_id: str
    text: str
    involved_npc_ids: tuple[str, ...]
    support: dict[str, Any]


@dataclass(frozen=True, order=True)
class _PresentationDiagnostic:
    section: str
    item_id: str
    failure_category: str
    disposition: str = "omitted"


@dataclass(frozen=True)
class _RoomPresentationPart:
    text: str
    support_key: str | tuple[str, ...]


@dataclass(frozen=True)
class _DashboardPresentation:
    hot_connections: tuple[_AdmittedFactRecord, ...]
    possible_pressure: tuple[_AdmittedFactRecord, ...]
    rumors: tuple[_AdmittedFactRecord, ...]
    guest_brief: tuple[str, ...]
    guest_support_keys: tuple[str, ...]
    room_parts: tuple[_RoomPresentationPart, ...]
    diagnostics: tuple[_PresentationDiagnostic, ...]

    @property
    def room_situation(self) -> str:
        return " ".join(part.text for part in self.room_parts)


@dataclass(frozen=True)
class ClubBuildResult:
    event: ClubEvent
    identities: tuple[NpcIdentity, ...]
    indexes_by_id: dict[str, ClubIndex]
    attendee_relationships: tuple[dict[str, Any], ...]
    attendee_facts: tuple[dict[str, Any], ...]
    source_map: dict[str, dict[str, Any]]
    debug_context: dict[str, Any]
    prep_context: PrepContext | None = field(default=None, repr=False)

    def attendee_summaries(self) -> list[dict[str, str]]:
        return [
            {
                "npc_id": identity.npc_id,
                "name": identity.display_name,
                "path": identity.current_path,
            }
            for identity in self.identities
        ]

    def with_event(self, event: ClubEvent) -> "ClubBuildResult":
        return ClubBuildResult(
            event=event,
            identities=self.identities,
            indexes_by_id=self.indexes_by_id,
            attendee_relationships=self.attendee_relationships,
            attendee_facts=self.attendee_facts,
            source_map=self.source_map,
            debug_context={**self.debug_context, "event": event.to_dict()},
            prep_context=self.prep_context,
        )

    def to_debug_dict(self) -> dict[str, Any]:
        source_ids = _source_ids_from_items([*self.attendee_relationships, *self.attendee_facts])
        for index in self.indexes_by_id.values():
            for _attribute_type, attribute in _typed_index_attributes(index):
                source_ids.update(source.source_id for source in attribute.sources)
        return safe_debug_data({
            "event": self.event.to_dict(),
            "attendees": [identity.to_dict() for identity in self.identities],
            "indexes": {npc_id: _compact_index_for_prompt(index) for npc_id, index in sorted(self.indexes_by_id.items())},
            "attendee_relationships": list(self.attendee_relationships),
            "attendee_facts": list(self.attendee_facts),
            "source_map": _compact_source_map(self.source_map, source_ids),
            "debug_context": self.debug_context,
            "prep_support": self.prep_context.debug_support(prep_references([
                self.event.dashboard.get("scene_prep", {}),
                self.event.dashboard.get("rumor_guidance", []),
            ])) if self.prep_context else {},
        })


class _DeferredClubProvider:
    def __init__(self, factory):
        self.factory = factory
        self.provider = None

    def generate_from_messages(self, messages, **kwargs):
        if self.provider is None:
            try:
                self.provider = self.factory()
            except Exception:
                raise RuntimeError("Provider credentials or configuration unavailable") from None
        return self.provider.generate_from_messages(messages, **kwargs)


def _dashboard_cache_identity(
    *,
    attendee_ids: Sequence[str],
    index_revisions: dict[str, str],
    venue: str,
    event_type: str,
    guest_paths: Sequence[str],
    skeleton: dict[str, Any],
    seed: int,
    use_ai: bool,
    cache_identity: str = "isolated-debug-cache",
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "cache_identity": cache_identity,
        "attendee_ids": sorted(attendee_ids),
        "index_revisions": dict(index_revisions),
        "venue": venue,
        "event_type": event_type,
        "guest_paths": sorted(str(path) for path in guest_paths),
        "skeleton": skeleton,
        "schema_version": CLUB_DASHBOARD_SCHEMA_VERSION,
        "prompt_version": CLUB_DASHBOARD_PROMPT_VERSION if use_ai else f"{CLUB_DASHBOARD_PROMPT_VERSION}_deterministic",
        "seed": seed,
    }
    if use_ai:
        identity["ai_request_version"] = CLUB_DASHBOARD_AI_REQUEST_VERSION
    return identity


class ClubGenerationService:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        cache_root: Path | str,
        cache_descriptor: CacheDescriptor | None = None,
        vault_root: Path | str | None = None,
        provider: ClubProvider | None = None,
    ) -> None:
        self.config = config or {}
        self.cache = (
            ClubCacheService(cache_root, descriptor=cache_descriptor)
            if cache_descriptor is not None
            else ClubCacheService(cache_root)
        )
        self.identity_registry = NpcIdentityRegistry(self.cache, vault_root=vault_root or self.config.get("vault_path"))
        self.index_store = ClubIndexStore(
            self.cache,
            character_schema=self.config.get("character_schema") or {},
        )
        self.provider = provider

    def build_event(
        self,
        guest_paths: Sequence[str],
        *,
        host_path: str | None = None,
        venue: str = "The Lantern Room",
        event_type: str = "social gathering",
        seed: int | None = None,
        use_ai: bool = True,
        force: bool = False,
    ) -> ClubEvent:
        return self.build_event_result(
            guest_paths,
            host_path=host_path,
            venue=venue,
            event_type=event_type,
            seed=seed,
            use_ai=use_ai,
            force=force,
        ).event

    def build_event_result(
        self,
        guest_paths: Sequence[str],
        *,
        host_path: str | None = None,
        venue: str = "The Lantern Room",
        event_type: str = "social gathering",
        seed: int | None = None,
        rumor_limit: int | None = None,
        use_ai: bool = True,
        force: bool = False,
        progress=None,
        baseline_ready=None,
    ) -> ClubBuildResult:
        # Canonical admission and rumor selection finish before any sheet AI request.
        baseline = self._build_canonical_event_result(
            guest_paths, host_path=host_path, venue=venue, event_type=event_type,
            seed=seed, rumor_limit=rumor_limit, use_ai=False, force=force,
        )
        documents = []
        import hashlib
        for identity in baseline.identities:
            index = baseline.indexes_by_id[identity.npc_id]
            raw = Path(index.path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != index.sheet_revision_hash:
                raise ClubGenerationError("A character sheet changed during the build. Generate the event again.")
            documents.append(capture_document(identity.npc_id, identity.display_name, raw.decode("utf-8", errors="replace"), index.path))
        context = PrepContext(tuple(documents), {}, "")
        baseline = replace(baseline, prep_context=context)
        initial = fallback_scene(documents, baseline.event.late_arrival_id, self._prep_positions(baseline))
        initial["encounters"] = tag_arrangement(documents, baseline.event.late_arrival_id)
        initial["stages"]["encounters"] = {"status": "complete", "method": "tags"}
        initial["revision"] = stable_hash([baseline.event.cache_key, "baseline", CLUB_PREP_REASONING_VERSION])
        baseline = baseline.with_event(replace(baseline.event, dashboard={**baseline.event.dashboard, "scene_prep": initial}))
        if baseline_ready:
            baseline_ready(baseline)
        if not use_ai:
            return baseline
        return self.complete_event_prep(baseline, progress=progress, force=force)

    def _prep_positions(self, result: ClubBuildResult) -> dict[str, str]:
        return {npc: "; ".join(filter(None, [index.status, *index.roles])) for npc, index in result.indexes_by_id.items()}

    def _prep_engine(self, progress=None, *, route="dashboard") -> PrepEngine:
        versions = ([CLUB_PANEL_SCHEMA_VERSION, CLUB_PANEL_PROMPT_VERSION, CLUB_PANEL_AI_REQUEST_VERSION] if route == "panel" else
                    [CLUB_DASHBOARD_SCHEMA_VERSION, CLUB_DASHBOARD_PROMPT_VERSION, CLUB_DASHBOARD_AI_REQUEST_VERSION])
        return PrepEngine(
            self.cache,
            _DeferredClubProvider(self._provider),
            self.config.get("model") or {},
            progress,
            reasoning_identity=[self.cache.cache_identity, *versions],
        )

    def complete_event_prep(self, result: ClubBuildResult, *, progress=None, late_arrival_id: str | None = None, force: bool = False) -> ClubBuildResult:
        if result.prep_context is None:
            raise ClubGenerationError("This event has no retained sheet context. Generate it again.")
        engine = self._prep_engine(progress)
        budget = RequestBudget()
        selected_rumors = result.event.dashboard.get("rumors") or []
        context = engine.context(
            result.prep_context.documents,
            budget,
            downstream_reserve=8 if selected_rumors else 4,
        )
        late = result.event.late_arrival_id if late_arrival_id is None else late_arrival_id
        event_info = dict(result.event.dashboard.get("event") or {})
        event_info["late_arrival_id"] = late
        scene = engine.scene(context, late, {**event_info, "seed": result.event.seed}, self._prep_positions(result), budget, force=force,
                             fixed_encounters=tag_arrangement(context.documents, late))
        pre_guidance_failures = [f for reading in context.readings.values() for f in reading["failures"]] + [
            stage.get("reason", "") for stage in scene["stages"].values() if stage["status"] != "complete"
        ]
        blocked_reason = next(
            (reason for reason in ("missing_credentials", "authentication") if reason in pre_guidance_failures),
            "provider_stopped",
        )
        rumor_guidance, rumor_stage = engine.rumor_guidance(
            context,
            selected_rumors,
            scene.get("encounters") or [],
            late,
            budget,
            force=force,
            allow_provider=not budget.stopped,
            blocked_reason=blocked_reason,
        )
        dashboard = {
            **result.event.dashboard,
            "event": event_info,
            "scene_prep": scene,
            "rumor_guidance": rumor_guidance,
            "late_arrival_id": late,
        }
        guidance_incomplete = bool(selected_rumors) and rumor_stage.get("status") != "complete"
        incomplete = bool(scene["incomplete"] or guidance_incomplete)
        all_stages = {**scene["stages"], "rumor_guidance": rumor_stage}
        has_ai_sections = any(
            stage["status"] == "complete"
            and stage.get("method") != "tags"
            and stage.get("status") != "not_applicable"
            for stage in all_stages.values()
        )
        mode = ("partial_ai" if has_ai_sections else "deterministic_fallback") if incomplete else "ai"
        failures = [f for r in context.readings.values() for f in r["failures"]] + [
            stage.get("reason", "") for stage in all_stages.values()
            if stage.get("status") not in {"complete", "not_applicable"}
        ]
        reason = next((f for f in ("missing_credentials", "authentication", "transport", "request_budget", "prompt_budget", "validation", "malformed_json") if f in failures), "validation" if failures else "")
        failure = {"missing_credentials": ("provider_missing_credentials", "provider_config"), "authentication": ("provider_authentication_failed", "provider_request"), "transport": ("provider_request_failed", "provider_request"),
                   "request_budget": ("request_budget_exhausted", "request_budget"), "prompt_budget": ("prompt_too_large", "prompt_budget"),
                   "validation": ("ai_output_validation_failed", "ai_output_validation"), "malformed_json": ("ai_output_validation_failed", "ai_output_validation")}.get(reason, (None, None))
        metadata = _generation_metadata(mode, model_name=self._model_name(), from_cache=budget.used == 0,
                                        ai_attempted=budget.used > 0, fallback_used=incomplete, ai_error_type=failure[0], ai_error_stage=failure[1])
        metadata["prep_stages"] = all_stages
        metadata["prep_requests"] = budget.used
        metadata["prep_revision"] = scene["revision"]
        key = stable_hash([
            self.cache.cache_identity,
            result.event.event_id,
            context.digest,
            late,
            scene["revision"],
            rumor_guidance,
            CLUB_DASHBOARD_SCHEMA_VERSION,
        ])
        event = replace(result.event, dashboard=dashboard, late_arrival_id=late, metadata=metadata, cache_key=key)
        if not incomplete:
            self.cache.write_json("events", key + ".json", data=event.to_dict())
        return replace(result.with_event(event), prep_context=context)

    def change_late_arrival_prep(self, result: ClubBuildResult, *, host_path: str | None = None, progress=None) -> ClubBuildResult:
        eligible = [identity for identity in result.identities if identity.npc_id != result.event.late_arrival_id and (not host_path or Path(result.indexes_by_id[identity.npc_id].path) != Path(host_path))]
        late = random.SystemRandom().choice(eligible).npc_id if eligible else result.event.late_arrival_id
        return self.complete_event_prep(result, progress=progress, late_arrival_id=late)

    def _build_canonical_event_result(
        self,
        guest_paths: Sequence[str],
        *,
        host_path: str | None = None,
        venue: str = "The Lantern Room",
        event_type: str = "social gathering",
        seed: int | None = None,
        rumor_limit: int | None = None,
        use_ai: bool = True,
        force: bool = False,
    ) -> ClubBuildResult:
        identities, indexes = self._load_attendees(guest_paths)
        if not identities:
            raise ClubGenerationError("No attendees available for club generation.")
        normalized_rumor_limit = normalize_rumor_limit(rumor_limit, attendee_count=len(identities))
        context = build_attendee_context(identities, indexes)
        source_map = build_source_map(indexes, identities=identities)
        event_seed = int(seed if seed is not None else time.time_ns() & 0xFFFFFFFF)
        late_arrival_id = select_late_arrival(identities, host_path=host_path, seed=event_seed)
        skeleton = _build_dashboard_skeleton_from_context(
            context,
            source_map,
            venue=venue,
            event_type=event_type,
            late_arrival_id=late_arrival_id,
            rumor_limit=normalized_rumor_limit,
            seed=event_seed,
        )
        cache_identity = _dashboard_cache_identity(
            attendee_ids=[identity.npc_id for identity in identities],
            index_revisions={index.npc_id: index.sheet_revision_hash for index in indexes},
            venue=venue,
            event_type=event_type,
            guest_paths=guest_paths,
            skeleton=skeleton,
            seed=event_seed,
            use_ai=use_ai,
            cache_identity=self.cache.cache_identity,
        )
        cache_key = stable_hash(cache_identity)
        cached = self.cache.read_json("events", f"{cache_key}.json", default=None)
        cached_event = _event_from_cached(cached, cache_key=cache_key, default_mode="ai" if use_ai else "deterministic")
        if cached_event is not None and not force and (not use_ai or _metadata_mode(cached_event.metadata) == "ai"):
            event = _with_event_metadata(
                cached_event,
                {
                    "from_cache": True,
                    "ai_attempted": False,
                    "fallback_used": False,
                    "ai_error_type": None,
                    "ai_error_stage": None,
                    "validation_error": None,
                    "fallback_reason": None,
                },
            )
            return _build_result(event, identities, indexes, context, source_map, dashboard_skeleton=skeleton)

        validation_error = ""
        fallback_reason = ""
        if use_ai:
            try:
                dashboard = {
                    **self._generate_dashboard(skeleton),
                    "rumor_guidance": [],
                }
                metadata = _generation_metadata("ai", model_name=self._model_name(), from_cache=False, skeleton=skeleton)
            except Exception as exc:  # pylint: disable=broad-except
                failure = _ai_failure_details(exc)
                validation_error = _sanitize_validation_error(str(exc))
                prompt_too_large = isinstance(exc, ClubPromptTooLargeError)
                fallback_reason = (
                    "AI dashboard prompt too large; deterministic fallback used."
                    if prompt_too_large
                    else "AI dashboard generation failed; deterministic fallback used."
                )
                if cached_event is not None and _metadata_mode(cached_event.metadata) == "ai":
                    event = _with_event_metadata(
                        cached_event,
                        {
                            "from_cache": True,
                            "ai_attempted": failure["ai_attempted"],
                            "fallback_used": False,
                            "ai_error_type": failure["ai_error_type"],
                            "ai_error_stage": failure["ai_error_stage"],
                            "validation_error": validation_error,
                            "fallback_reason": "AI dashboard regeneration failed; reused cached AI dashboard.",
                        },
                    )
                    return _build_result(event, identities, indexes, context, source_map, dashboard_skeleton=skeleton)
                dashboard = dashboard_from_skeleton(skeleton)
                metadata = _generation_metadata(
                    "deterministic_fallback",
                    model_name=self._model_name(),
                    from_cache=False,
                    ai_attempted=failure["ai_attempted"],
                    fallback_used=True,
                    ai_error_type=failure["ai_error_type"],
                    ai_error_stage=failure["ai_error_stage"],
                    validation_error=validation_error,
                    fallback_reason=fallback_reason,
                    prompt_too_large=prompt_too_large,
                    estimated_input_tokens=getattr(exc, "estimated_tokens", 0),
                    prompt_budget=getattr(exc, "budget", 0),
                    skeleton=skeleton,
                )
        else:
            dashboard = dashboard_from_skeleton(skeleton)
            metadata = _generation_metadata("deterministic", model_name="", from_cache=False, skeleton=skeleton)
        _emit_presentation_diagnostics(_compose_dashboard_presentation(skeleton).diagnostics)
        event_id = stable_hash({"cache_key": cache_key, "kind": "club_event"})[:16]
        event = ClubEvent(
            event_id=event_id,
            attendee_ids=tuple(identity.npc_id for identity in identities),
            late_arrival_id=late_arrival_id,
            dashboard=dashboard,
            cache_key=cache_key,
            seed=event_seed,
            metadata=metadata,
        )
        if _metadata_mode(event.metadata) in {"ai", "deterministic"}:
            self.cache.write_json("events", f"{cache_key}.json", data=event.to_dict())
        return _build_result(event, identities, indexes, context, source_map, dashboard_skeleton=skeleton)

    def regenerate_late_arrival(self, event: ClubEvent, guest_paths: Sequence[str], *, host_path: str | None = None) -> ClubEvent:
        identities, _indexes = self._load_attendees(guest_paths)
        late_arrival_id = select_late_arrival(identities, host_path=host_path, seed=time.time_ns() & 0xFFFFFFFF)
        updated = ClubEvent(
            event_id=event.event_id,
            attendee_ids=event.attendee_ids,
            late_arrival_id=late_arrival_id,
            dashboard=event.dashboard,
            cache_key=event.cache_key,
            seed=event.seed,
            metadata=dict(event.metadata),
        )
        self.cache.write_json("events", f"{event.cache_key}.json", data=updated.to_dict())
        return updated

    def build_npc_panel(
        self,
        event: ClubEvent,
        guest_paths: Sequence[str],
        npc_id: str,
        *,
        use_ai: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        identities, indexes = self._load_attendees(guest_paths)
        context = build_attendee_context(identities, indexes)
        source_map = build_source_map(indexes, identities=identities)
        result = _build_result(event, identities, indexes, context, source_map)
        if "scene_prep" in event.dashboard:
            documents = tuple(capture_document(i.npc_id, i.display_name, Path(result.indexes_by_id[i.npc_id].path).read_bytes().decode("utf-8", errors="replace"), result.indexes_by_id[i.npc_id].path) for i in identities)
            revisions = event.dashboard["scene_prep"].get("document_revisions")
            if revisions and revisions != {d.npc_id: d.revision for d in documents}:
                raise ClubGenerationError("Character sheets changed after this event. Generate the event again.")
            if use_ai:
                engine = self._prep_engine()
                result = replace(result, prep_context=engine.context(documents, RequestBudget(4)))
        return self.build_npc_panel_from_result(result, npc_id, use_ai=use_ai, force=force)

    def build_npc_panel_from_result(
        self, result: ClubBuildResult, npc_id: str, *, use_ai: bool = True, force: bool = False,
    ) -> dict[str, Any]:
        panel = self._build_canonical_npc_panel_from_result(result, npc_id, use_ai=use_ai, force=force)
        if "scene_prep" not in result.event.dashboard:
            return {
                **panel,
                "conversation_openings": [],
                "metadata": {
                    **panel.get("metadata", {}),
                    "conversation_openings_stage": {"status": "unavailable", "reason": "deterministic"},
                },
            }
        scene = result.event.dashboard.get("scene_prep") or {}
        skeleton = build_npc_panel_skeleton(result, npc_id)
        if use_ai and result.prep_context is not None and result.prep_context.readings:
            auth = panel.get("metadata", {}).get("ai_error_type") in {"provider_missing_credentials", "provider_authentication_failed"}
            engine = self._prep_engine(route="panel")
            rows, stage = engine.relevance(
                result.prep_context, npc_id, scene, skeleton.get("people_here") or [],
                force=force, allow_provider=not auth,
            )
            relevance_auth = stage.get("reason") in {"authentication", "missing_credentials"}
            conversation_rows, conversation_stage = engine.conversation_openings(
                result.prep_context,
                npc_id,
                force=force,
                allow_provider=not auth and not relevance_auth,
                blocked_reason=(stage.get("reason") if relevance_auth else "authentication"),
            )
        else:
            rows, stage = [], {"status": "unavailable", "reason": "deterministic"}
            conversation_rows, conversation_stage = [], {"status": "unavailable", "reason": "deterministic"}
        if stage["status"] != "complete":
            cues = {r["item_id"]: r["text"] for r in panel.get("presentation", {}).get("people_here", [])}
            rows = [{"npc_id": r.get("target_npc_id"), "text": cues.get(r.get("item_id")) or r.get("prep_text") or r.get("summary", ""),
                     "classification": "established", "evidence": []} for r in panel.get("people_here", []) if r.get("target_npc_id")]
        return {
            **panel,
            "who_matters": rows,
            "conversation_openings": conversation_rows,
            "prep_revision": scene.get("revision", ""),
            "metadata": {
                **panel.get("metadata", {}),
                "who_matters_stage": stage,
                "conversation_openings_stage": conversation_stage,
            },
        }

    def _build_canonical_npc_panel_from_result(
        self,
        result: ClubBuildResult,
        npc_id: str,
        *,
        use_ai: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        index_by_id = result.indexes_by_id
        if npc_id not in index_by_id or npc_id not in set(result.event.attendee_ids):
            raise ClubGenerationError("NPC is not part of this club event.")
        panel_skeleton = build_npc_panel_skeleton(result, npc_id)
        digest = stable_hash(
            {
                "relationships": relationship_digest_for(npc_id, result.attendee_relationships),
                "facts": relationship_digest_for(npc_id, result.attendee_facts),
                "skeleton": panel_skeleton,
            }
        )
        panel_identity = {
            "cache_identity": self.cache.cache_identity,
            "event_id": result.event.event_id,
            "npc_id": npc_id,
            "npc_revision": index_by_id[npc_id].sheet_revision_hash,
            "npc_context_digest": digest,
            "schema_version": CLUB_PANEL_SCHEMA_VERSION,
            "prompt_version": CLUB_PANEL_PROMPT_VERSION if use_ai else f"{CLUB_PANEL_PROMPT_VERSION}_deterministic",
        }
        if result.prep_context is not None:
            panel_identity["prep_context"] = result.prep_context.digest if result.prep_context.readings else "baseline"
            panel_identity["prep_revision"] = result.event.dashboard.get("scene_prep", {}).get("revision", "")
        if use_ai:
            panel_identity["ai_request_version"] = CLUB_PANEL_AI_REQUEST_VERSION
        panel_key = stable_hash(panel_identity)
        cached = self.cache.read_json("npc_panels", f"{panel_key}.json", default=None)
        cached_panel = _panel_from_cached(cached, cache_key=panel_key, default_mode="ai" if use_ai else "deterministic")
        if cached_panel is not None:
            try:
                validate_ai_npc_panel(cached_panel, source_ids=set(panel_skeleton.get("source_ids", [])), npc_id=npc_id, skeleton=panel_skeleton)
            except (ClubGenerationError, KeyError, TypeError, ValueError):
                cached_panel = None
        if cached_panel is not None and not force and (not use_ai or _metadata_mode(cached_panel.get("metadata")) == "ai"):
            return _with_panel_metadata(
                cached_panel,
                {
                    "from_cache": True,
                    "ai_attempted": False,
                    "fallback_used": False,
                    "ai_error_type": None,
                    "ai_error_stage": None,
                    "validation_error": None,
                    "fallback_reason": None,
                },
            )
        if use_ai:
            try:
                panel = self._generate_npc_panel(panel_skeleton, npc_id)
                panel = _with_panel_metadata(panel, _generation_metadata("ai", model_name=self._model_name(), from_cache=False, skeleton=panel_skeleton))
                self.cache.write_json(
                    "npc_panels",
                    f"{panel_key}.json",
                    data={"cache_key": panel_key, "panel": panel, "metadata": panel["metadata"]},
                )
                return panel
            except Exception as exc:  # pylint: disable=broad-except
                failure = _ai_failure_details(exc)
                validation_error = _sanitize_validation_error(str(exc))
                prompt_too_large = isinstance(exc, ClubPromptTooLargeError)
                if cached_panel is not None and _metadata_mode(cached_panel.get("metadata")) == "ai":
                    return _with_panel_metadata(
                        cached_panel,
                        {
                            "from_cache": True,
                            "ai_attempted": failure["ai_attempted"],
                            "fallback_used": False,
                            "ai_error_type": failure["ai_error_type"],
                            "ai_error_stage": failure["ai_error_stage"],
                            "validation_error": validation_error,
                            "fallback_reason": "AI NPC panel regeneration failed; reused cached AI panel.",
                        },
                    )
                panel = npc_panel_from_skeleton(panel_skeleton)
                return _with_panel_metadata(
                    panel,
                    _generation_metadata(
                        "deterministic_fallback",
                        model_name=self._model_name(),
                        from_cache=False,
                        ai_attempted=failure["ai_attempted"],
                        fallback_used=True,
                        ai_error_type=failure["ai_error_type"],
                        ai_error_stage=failure["ai_error_stage"],
                        validation_error=validation_error,
                        fallback_reason=(
                            "AI NPC panel prompt too large; deterministic fallback used."
                            if prompt_too_large
                            else "AI NPC panel generation failed; deterministic fallback used."
                        ),
                        prompt_too_large=prompt_too_large,
                        estimated_input_tokens=getattr(exc, "estimated_tokens", 0),
                        prompt_budget=getattr(exc, "budget", 0),
                        skeleton=panel_skeleton,
                    ),
                )
        panel = npc_panel_from_skeleton(panel_skeleton)
        panel = _with_panel_metadata(panel, _generation_metadata("deterministic", model_name="", from_cache=False, skeleton=panel_skeleton))
        self.cache.write_json("npc_panels", f"{panel_key}.json", data={"cache_key": panel_key, "panel": panel, "metadata": panel["metadata"]})
        return panel

    def attendee_summary(self, guest_paths: Sequence[str]) -> list[dict[str, str]]:
        identities, _indexes = self._load_attendees(guest_paths)
        return [
            {
                "npc_id": identity.npc_id,
                "name": identity.display_name,
                "path": identity.current_path,
            }
            for identity in identities
        ]

    def _load_attendees(self, guest_paths: Sequence[str]) -> tuple[list[NpcIdentity], list[ClubIndex]]:
        identities: list[NpcIdentity] = []
        indexes: list[ClubIndex] = []
        seen: set[str] = set()
        for raw_path in guest_paths:
            path = Path(raw_path)
            if not path.exists() or not path.is_file():
                continue
            identity = self.identity_registry.get_or_create(path)
            if identity.npc_id in seen:
                continue
            seen.add(identity.npc_id)
            index = self.index_store.get_or_build(identity, path)
            identities.append(identity)
            indexes.append(index)
        return identities, indexes

    def _generate_dashboard(
        self,
        skeleton: dict[str, Any],
    ) -> dict[str, Any]:
        source_ids = set(skeleton["source_ids"])
        prompt = dashboard_prompt(skeleton)
        estimated_tokens = estimate_prompt_tokens(prompt)
        if estimated_tokens > CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET:
            raise ClubPromptTooLargeError(
                f"dashboard prompt estimated at {estimated_tokens} input tokens; budget is {CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET}",
                estimated_tokens=estimated_tokens,
                budget=CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET,
            )
        try:
            dashboard = _generate_valid_json(
                self._provider(),
                prompt,
                validator=lambda data: validate_ai_dashboard(
                    postprocess_dashboard(data, skeleton),
                    source_ids=source_ids,
                    attendee_ids=set(skeleton["attendee_ids"]),
                    skeleton=skeleton,
                ),
            )
        except ClubGenerationError:
            raise
        return postprocess_dashboard(dashboard, skeleton)

    def _generate_npc_panel(self, skeleton: dict[str, Any], npc_id: str) -> dict[str, Any]:
        prompt = npc_panel_prompt(skeleton, npc_id=npc_id)
        estimated_tokens = estimate_prompt_tokens(prompt)
        if estimated_tokens > CLUB_PANEL_PROMPT_TOKEN_BUDGET:
            raise ClubPromptTooLargeError(
                f"NPC panel prompt estimated at {estimated_tokens} input tokens; budget is {CLUB_PANEL_PROMPT_TOKEN_BUDGET}",
                estimated_tokens=estimated_tokens,
                budget=CLUB_PANEL_PROMPT_TOKEN_BUDGET,
            )
        return _generate_valid_json(
            self._provider(),
            prompt,
            validator=lambda data: postprocess_npc_presentation(data, skeleton),
        )

    def _provider(self) -> ClubProvider:
        if self.provider is not None:
            return self.provider
        return LlmProvider(self.config)

    def _model_name(self) -> str:
        model_cfg = self.config.get("model") if isinstance(self.config, dict) else {}
        if not isinstance(model_cfg, dict):
            return ""
        return str(model_cfg.get("name") or model_cfg.get("provider") or "")


def build_attendee_context(identities: Sequence[NpcIdentity], indexes: Sequence[ClubIndex]) -> dict[str, Any]:
    identity_by_id = {identity.npc_id: identity for identity in identities}
    id_by_name = _id_by_name_for_identities(identities)

    attendee_relationships: list[dict[str, Any]] = []
    attendee_facts: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    compact_indexes: list[dict[str, Any]] = []
    for index in indexes:
        compact_indexes.append(_compact_index_for_prompt(index))
        for fact in index.relationships:
            target_id = fact.target_npc_id or id_by_name.get(_attendee_lookup_key(fact.target_name))
            if not target_id or target_id == index.npc_id or target_id not in identity_by_id:
                continue
            item = _grounded_item_from_fact(fact, characters=[index.npc_id, target_id])
            attendee_relationships.append(item)
            attendee_facts.append(item)
            source_ids.update(_source_ids_from_items([item]))
        for fact in [
            *index.current_goals,
            *index.grievances,
            *index.unresolved_business,
            *index.rumor_notes,
            *index.recent_relevant_events,
        ]:
            mentioned_ids = _mentioned_attendee_ids_for_fact(fact, id_by_name, identity_by_id)
            item = _grounded_item_from_fact(fact, characters=[index.npc_id], mentioned_npc_ids=mentioned_ids)
            attendee_facts.append(item)
            source_ids.update(_source_ids_from_items([item]))
    return {
        "attendee_ids": [identity.npc_id for identity in identities],
        "identities": [_identity_for_prompt(identity) for identity in identities],
        "indexes": compact_indexes,
        "attendee_relationships": dedupe_grounded_facts(attendee_relationships),
        "attendee_facts": dedupe_grounded_facts(attendee_facts),
        "source_ids": sorted(source_ids),
    }


def build_source_map(
    indexes: Sequence[ClubIndex],
    *,
    identities: Sequence[NpcIdentity] | None = None,
) -> dict[str, dict[str, Any]]:
    id_by_name = _id_by_name_for_identities(identities or [])
    source_map: dict[str, dict[str, Any]] = {}
    for index in indexes:
        for attribute_type, attribute in _typed_index_attributes(index):
            for source in attribute.sources:
                source_map[source.source_id] = {
                    "source": source.to_dict(),
                    "npc_id": index.npc_id,
                    "name": index.name,
                    "attribute_type": attribute_type,
                    "attribute_value": clean_display_text(attribute.value),
                    "fact_type": "",
                    "summary": "",
                    "target_name": "",
                    "target_npc_id": "",
                }
        for fact in index.relationships:
            target_npc_id = fact.target_npc_id or id_by_name.get(_attendee_lookup_key(fact.target_name), "")
            _add_source_map_fact(source_map, index=index, fact=fact, target_npc_id=target_npc_id)
        for fact in [
            *index.current_goals,
            *index.grievances,
            *index.unresolved_business,
            *index.rumor_notes,
            *index.story_hooks,
            *index.recent_relevant_events,
        ]:
            target_npc_id = None if fact.fact_type == "rumor" else ""
            _add_source_map_fact(source_map, index=index, fact=fact, target_npc_id=target_npc_id)
    return dict(sorted(source_map.items()))


def _add_source_map_fact(
    source_map: dict[str, dict[str, Any]],
    *,
    index: ClubIndex,
    fact: ClubFact,
    target_npc_id: str | None,
) -> None:
    for source in fact.sources:
        entry = dict(source_map.get(source.source_id) or {})
        entry.update({
            "source": source.to_dict(),
            "npc_id": index.npc_id,
            "name": index.name,
            "fact_type": fact.fact_type,
            "summary": clean_display_text(fact.summary),
            "target_name": fact.target_name,
            "target_npc_id": target_npc_id,
        })
        source_map[source.source_id] = entry


def _id_by_name_for_identities(identities: Sequence[NpcIdentity]) -> dict[str, str]:
    seen: dict[str, str] = {}
    ambiguous: set[str] = set()
    for identity in identities:
        for value in [identity.display_name, Path(identity.current_path).stem, *identity.aliases]:
            key = _attendee_lookup_key(value)
            if not key:
                continue
            existing = seen.get(key)
            if existing and existing != identity.npc_id:
                ambiguous.add(key)
                continue
            seen[key] = identity.npc_id
    return {key: npc_id for key, npc_id in seen.items() if key not in ambiguous}


def _attendee_lookup_key(value: str) -> str:
    return " ".join(str(value or "").casefold().strip().split())


def _mentioned_attendee_ids_for_fact(
    fact: ClubFact,
    id_by_name: dict[str, str],
    identity_by_id: dict[str, NpcIdentity],
) -> list[str]:
    mentioned: list[str] = []
    for target_name in fact.link_targets or ((fact.target_name,) if fact.target_name else ()):
        target_id = id_by_name.get(_attendee_lookup_key(target_name))
        if target_id and target_id in identity_by_id and target_id not in mentioned:
            mentioned.append(target_id)
    return mentioned


def dashboard_prompt_context(context: dict[str, Any], source_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    identities = [item for item in context.get("identities") or [] if isinstance(item, dict)]
    all_relationships = dedupe_grounded_facts(context.get("attendee_relationships") or [])
    all_facts = dedupe_grounded_facts(context.get("attendee_facts") or [])
    relationships = _prioritized_prompt_items(all_relationships, limit=DASHBOARD_RELATIONSHIP_LIMIT)
    facts = _prioritized_prompt_items(all_facts, limit=DASHBOARD_FACT_LIMIT)
    identity_by_id = {str(item.get("npc_id")): item for item in identities}
    prompt_source_ids = _source_ids_from_items([*relationships, *facts])
    return safe_debug_data(
        {
            "attendee_ids": list(context.get("attendee_ids") or []),
            "identities": identities,
            "indexes": list(context.get("indexes") or []),
            "attendee_relationships": relationships,
            "attendee_facts": facts,
            "source_ids": sorted(prompt_source_ids),
            "source_map": _compact_source_map(source_map, prompt_source_ids),
            "deterministic_social_components": cluster_social_map(context.get("attendee_ids") or [], all_relationships),
            "ranked_connections": rank_connections(all_relationships, identity_by_id=identity_by_id)[:DASHBOARD_RANKED_CONNECTION_LIMIT],
        }
    )


def build_npc_panel_context(result: ClubBuildResult, npc_id: str) -> dict[str, Any]:
    identities = {identity.npc_id: _identity_for_prompt(identity) for identity in result.identities}
    index = result.indexes_by_id.get(npc_id)
    raw_related_relationships = [
        item for item in result.attendee_relationships if str(item.get("source_npc_id") or "") == npc_id
    ]
    raw_related_facts = [
        item
        for item in result.attendee_facts
        if is_relevant_to_clicked_npc(item, npc_id)
    ]
    id_by_name = _id_by_name_for_identities(result.identities)
    identity_objects = {identity.npc_id: identity for identity in result.identities}
    for attendee_index in result.indexes_by_id.values():
        for fact in attendee_index.story_hooks:
            mentioned_ids = _mentioned_attendee_ids_for_fact(fact, id_by_name, identity_objects)
            item = _grounded_item_from_fact(
                fact,
                characters=[attendee_index.npc_id],
                mentioned_npc_ids=mentioned_ids,
            )
            if is_relevant_to_clicked_npc(item, npc_id):
                raw_related_facts.append(item)
    relationship_keys = {grounded_fact_key(item) for item in raw_related_relationships}
    related_relationships = _rank_directed_panel_facts(raw_related_relationships, identity_by_id=identities)[:PANEL_RELATIONSHIP_LIMIT]
    related_facts = [
        item
        for item in dedupe_grounded_facts(raw_related_facts)
        if grounded_fact_key(item) not in relationship_keys
    ][:PANEL_FACT_LIMIT]
    source_ids = _source_ids_from_items([*related_relationships, *related_facts])
    compact_index = _compact_index_for_prompt(index) if index is not None else {}
    source_ids.update(_source_ids_from_attribute_payloads(compact_index))
    return safe_debug_data(
        {
            "npc_id": npc_id,
            "identity": identities.get(npc_id, {}),
            "index": compact_index,
            "identities": [identities.get(npc_id, {})],
            "indexes": [_compact_index_for_prompt(index)] if index is not None else [],
            "attendees": [
                {"npc_id": identity.npc_id, "display_name": identity.display_name}
                for identity in result.identities
            ],
            "attendee_relationships": related_relationships,
            "attendee_facts": related_facts,
            "source_ids": sorted(source_ids),
            "source_map": _compact_source_map(result.source_map, source_ids),
        }
    )


def is_relevant_to_clicked_npc(item: dict[str, Any], clicked_id: str) -> bool:
    if not isinstance(item, dict):
        return False
    npc_id = str(clicked_id or "")
    if not npc_id:
        return False
    if str(item.get("source_npc_id") or "") == npc_id:
        return True
    if str(item.get("target_npc_id") or "") == npc_id:
        return True
    if npc_id in {str(character) for character in item.get("characters") or []}:
        return True
    return npc_id in {str(mentioned_id) for mentioned_id in item.get("mentioned_npc_ids") or []}


def build_dashboard_skeleton(result: ClubBuildResult) -> dict[str, Any]:
    context = _context_from_result(result)
    event = result.event.dashboard.get("event") if isinstance(result.event.dashboard, dict) else {}
    rumor_selection = result.event.dashboard.get("rumor_selection") if isinstance(result.event.dashboard, dict) else {}
    rumor_limit = None
    if isinstance(rumor_selection, dict):
        rumor_limit = rumor_selection.get("limit")
    return _build_dashboard_skeleton_from_context(
        context,
        result.source_map,
        venue=str(event.get("venue") or "The Lantern Room"),
        event_type=str(event.get("event_type") or "social gathering"),
        late_arrival_id=result.event.late_arrival_id,
        rumor_limit=normalize_rumor_limit(rumor_limit, attendee_count=len(result.identities)),
        seed=result.event.seed,
    )


def build_npc_panel_skeleton(result: ClubBuildResult, npc_id: str) -> dict[str, Any]:
    context = build_npc_panel_context(result, npc_id)
    return _build_npc_panel_skeleton_from_context(context, npc_id)


def _build_npc_panel_skeleton_from_context(context: dict[str, Any], npc_id: str) -> dict[str, Any]:
    identity = context.get("identity") if isinstance(context.get("identity"), dict) else {}
    index = context.get("index") if isinstance(context.get("index"), dict) else {}
    relationships = _rank_directed_panel_facts(context.get("attendee_relationships") or [])
    facts = dedupe_grounded_facts(context.get("attendee_facts") or [])
    incoming_rumors = [item for item in facts if _is_incoming_panel_rumor(item, npc_id)]
    pressures = [
        item
        for item in [*relationships, *facts]
        if not _is_incoming_panel_rumor(item, npc_id) and _is_panel_sensitive(item)
    ]
    skeleton_items = [*relationships, *facts]
    source_ids = {str(source_id) for source_id in context.get("source_ids") or [] if str(source_id)}
    occupied_fact_ids: dict[tuple[str, ...], str] = {}
    identity_by_id = {str(item.get("npc_id")): item for item in context.get("attendees") or [] if isinstance(item, dict)}
    identity_by_id.setdefault(npc_id, identity)
    explicit_focus_candidates = [
        item
        for item in facts
        if _is_explicit_self_focus(item, npc_id)
    ]
    current_desire = _select_panel_focus_support(
        explicit_focus_candidates=explicit_focus_candidates,
    )
    if current_desire is not None:
        occupied_fact_ids[_canonical_fact_identity(current_desire)] = "current_desire"
    people_here = _reserve_panel_items(
        relationships,
        occupied_fact_ids,
        section="people_here",
        identity_by_id=identity_by_id,
        limit=3,
    )
    interesting, suppressed = _select_panel_hook_items(
        [
            item
            for item in _rank_panel_hook_items(facts)
            if not _is_incoming_panel_rumor(item, npc_id)
        ],
        occupied_fact_ids,
        identity_by_id=identity_by_id,
    )
    if interesting:
        occupied_fact_ids[_canonical_fact_identity(interesting[0])] = "interesting_detail"
    sensitive_candidates = dedupe_grounded_facts(
        [
            *_prioritized_prompt_items(incoming_rumors, limit=12),
            *_prioritized_prompt_items(pressures, limit=12),
        ]
    )
    sensitive = _reserve_panel_items(
        sensitive_candidates,
        occupied_fact_ids,
        section="sensitive",
        identity_by_id=identity_by_id,
        limit=2,
    )
    conversation_pool = _prioritized_prompt_items(
        [
            item
            for item in [*facts, *relationships]
            if not _is_incoming_panel_rumor(item, npc_id) and not _is_panel_sensitive(item)
        ],
        limit=12,
    )
    likely_subjects = _reserve_panel_items(
        conversation_pool,
        occupied_fact_ids,
        section="likely_subjects",
        identity_by_id=identity_by_id,
        limit=3,
    )
    current_read_basis = _current_read_basis(index)
    panel = {
        "kind": "npc_panel",
        "schema_version": CLUB_SKELETON_VERSION,
        "npc_id": npc_id,
        "name": clean_display_text(str(identity.get("display_name") or index.get("name") or npc_id)),
        "identity": {
            "affiliation": clean_display_text(str(index.get("affiliation") or "")),
            "faction": clean_display_text(str(index.get("faction") or "")),
            "status": clean_display_text(str(index.get("status") or "")),
            "roles": [clean_display_text(str(role)) for role in index.get("roles") or []],
        },
        "personality": dedupe_display_strings(index.get("personality_tags") or []),
        "current_read": "",
        "current_read_basis": current_read_basis,
        "tonight": {
            "current_demeanor": "",
            "current_desire": current_desire,
        },
        "people_here": people_here,
        "conversation": {
            "likely_subjects": likely_subjects,
            "sensitive": sensitive,
        },
        "interesting_detail": interesting[0] if interesting else None,
        "presentation_suppressed": suppressed,
        "empty_state": "" if skeleton_items or index.get("personality_tags") or index.get("affiliation") or index.get("faction") else "No attendee-specific indexed facts were found for this NPC.",
        "source_ids": sorted(source_ids),
        "source_map": _compact_source_map(context.get("source_map") or {}, source_ids),
    }
    panel["current_read"] = _fallback_current_read(panel)
    return safe_debug_data(panel)


def _reserve_panel_items(
    items: Sequence[dict[str, Any]],
    occupied_fact_ids: dict[tuple[str, ...], str],
    *,
    section: str,
    identity_by_id: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in items:
        identity = _canonical_fact_identity(item)
        if identity in occupied_fact_ids:
            continue
        occupied_fact_ids[identity] = section
        selected.append(_clone_grounded_item(item, section, identity_by_id=identity_by_id))
        if len(selected) >= limit:
            break
    return selected


def _is_incoming_panel_rumor(item: dict[str, Any], npc_id: str) -> bool:
    return bool(
        isinstance(item, dict)
        and str(item.get("type") or "").casefold() == "rumor"
        and str(item.get("source_npc_id") or "") not in {"", npc_id}
        and item.get("target_npc_id") is None
        and npc_id in {str(value) for value in item.get("mentioned_npc_ids") or []}
    )


def _is_explicit_self_focus(item: dict[str, Any], npc_id: str) -> bool:
    return bool(
        isinstance(item, dict)
        and str(item.get("type") or "").casefold() in {"goal", "intent", "desire", "objective"}
        and str(item.get("source_npc_id") or "") == npc_id
    )


def _focus_text(item: dict[str, Any], category: str) -> str:
    summary = clean_display_text(str(item.get("summary") or "")).rstrip(".!?").strip()
    prefixes = {
        "goal": "May be focused on — ",
        "pressure": "May be preoccupied with this pressure — ",
        "relationship": "Could be watching this relationship — ",
        "concern": "May be focused on this grounded concern — ",
    }
    return _sentence(f"{prefixes[category]}{summary}")


def _rebuild_focus_support(item: dict[str, Any], category: str) -> dict[str, Any]:
    support = deepcopy(item)
    support["summary"] = clean_display_text(str(item.get("summary") or ""))
    support["section"] = "current_desire"
    support["item_id"] = stable_hash(
        {"panel_focus": list(_canonical_fact_identity(item))}
    )[:16]
    support["sources"] = _dedupe_sources_in_order(item.get("sources") or [])
    support["characters"] = [str(value) for value in item.get("characters") or []]
    support["mentioned_npc_ids"] = [str(value) for value in item.get("mentioned_npc_ids") or []]
    support["link_targets"] = [str(value) for value in item.get("link_targets") or []]
    support["display_label"] = _display_label_for_item(support)
    support["prep_text"] = _focus_text(support, category)
    return support


def _select_panel_focus_support(
    *,
    explicit_focus_candidates: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    ranked_explicit_focus = sorted(
        list(explicit_focus_candidates),
        key=lambda item: (
            _is_panel_sensitive(item),
            -_prompt_item_score(item),
            _canonical_fact_identity(item),
        ),
    )
    return _rebuild_focus_support(ranked_explicit_focus[0], "goal") if ranked_explicit_focus else None


def _build_dashboard_skeleton_from_context(
    context: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
    *,
    venue: str,
    event_type: str,
    late_arrival_id: str,
    rumor_limit: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    identities = [item for item in context.get("identities") or [] if isinstance(item, dict)]
    attendee_ids = list(context.get("attendee_ids") or [])
    relationships = dedupe_grounded_facts(context.get("attendee_relationships") or [])
    facts = dedupe_grounded_facts(context.get("attendee_facts") or [])
    rumor_limit = normalize_rumor_limit(rumor_limit, attendee_count=len(identities))
    identity_by_id = {str(item.get("npc_id")): item for item in identities}
    for index in context.get("indexes") or []:
        if not isinstance(index, dict):
            continue
        npc_id = str(index.get("npc_id") or "")
        if not npc_id:
            continue
        identity_by_id.setdefault(npc_id, {"npc_id": npc_id})
        identity_by_id[npc_id]["affiliation"] = index.get("affiliation") or ""
        identity_by_id[npc_id]["faction"] = index.get("faction") or ""
    dashboard_facts = [item for item in facts if _is_dashboard_candidate_fact(item)]
    ranked = rank_connections(relationships, identity_by_id=identity_by_id)
    curated = _curate_dashboard_visible_sections(
        relationships=relationships,
        dashboard_facts=dashboard_facts,
        ranked_connections=ranked,
        rumor_limit=rumor_limit,
        seed=seed,
    )
    pressures = [_clone_grounded_item(item, "possible_drama", identity_by_id=identity_by_id) for item in curated["possible_drama"]]
    top_connections = [_clone_grounded_item(item, "top_connections", identity_by_id=identity_by_id) for item in curated["top_connections"]]
    interesting = [_clone_grounded_item(item, "interesting_connections", identity_by_id=identity_by_id) for item in curated["interesting_connections"]]
    rumor_pool = [_clone_grounded_item(item, "rumor_pool", identity_by_id=identity_by_id) for item in curated["rumor_pool"]]
    rumors = [_clone_grounded_item(item, "rumors", identity_by_id=identity_by_id) for item in curated["rumors"]]
    unresolved = [_clone_grounded_item(item, "unresolved_business", identity_by_id=identity_by_id) for item in curated["unresolved_business"]]
    opportunities = [_clone_grounded_item(item, "opportunities", identity_by_id=identity_by_id) for item in curated["opportunities"]]
    room_groups = room_facing_social_map(attendee_ids, relationships, identity_by_id=identity_by_id)
    attendee_count = len(identities)
    source_ids = _source_ids_from_items([*pressures, *interesting, *rumors, *rumor_pool, *unresolved, *opportunities, *top_connections])
    skeleton = {
        "kind": "dashboard",
        "schema_version": CLUB_SKELETON_VERSION,
        "rumor_materialization_version": CLUB_RUMOR_MATERIALIZATION_VERSION,
        "event": {
            "venue": venue,
            "host": "",
            "event_type": event_type,
            "mood": "tense",
            "unusual_circumstance": "",
            "late_arrival_id": late_arrival_id,
        },
        "attendee_ids": attendee_ids,
        "attendees": identities,
        "room_groups": room_groups,
        "social_map": room_groups,
        "notable_details": [
            f"{attendee_count} attendees are in the room.",
            f"{len(relationships)} attendee-to-attendee connection{'s' if len(relationships) != 1 else ''} are grounded in the indexes.",
        ],
        "rumors": rumors,
        "rumor_pool": rumor_pool,
        "rumor_selection": {
            "limit": rumor_limit,
            "selected_count": len(rumors),
            "grounded_count": len(rumor_pool),
        },
        "rumor_selection_audit": curated["rumor_selection_audit"],
        "possible_drama": pressures,
        "interesting_connections": interesting,
        "unresolved_business": unresolved,
        "opportunities": opportunities,
        "top_connections": top_connections,
        "background_details": [],
        "source_ids": sorted(source_ids),
        "source_map": _compact_source_map(source_map, source_ids),
        "dropped_item_count": max(0, len(facts) + len(relationships) - len(source_ids)),
    }
    return safe_debug_data(skeleton)


def _build_result(
    event: ClubEvent,
    identities: Sequence[NpcIdentity],
    indexes: Sequence[ClubIndex],
    context: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
    dashboard_skeleton: dict[str, Any] | None = None,
) -> ClubBuildResult:
    return ClubBuildResult(
        event=event,
        identities=tuple(identities),
        indexes_by_id={index.npc_id: index for index in indexes},
        attendee_relationships=tuple(context.get("attendee_relationships") or []),
        attendee_facts=tuple(context.get("attendee_facts") or []),
        source_map=source_map,
        debug_context={
            "attendee_ids": list(context.get("attendee_ids") or []),
            "source_ids": list(context.get("source_ids") or []),
            "relationship_count": len(context.get("attendee_relationships") or []),
            "fact_count": len(context.get("attendee_facts") or []),
            "event": event.to_dict(),
            "dashboard_skeleton": dashboard_skeleton or {},
            "dashboard_prompt_context": dashboard_prompt_context(context, source_map),
        },
    )


def postprocess_dashboard(dashboard: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    processed = dict(dashboard)
    identities = [item for item in context.get("attendees") or [] if isinstance(item, dict)]
    identity_by_id = {str(item.get("npc_id")): item for item in identities}
    for field in GROUNDED_DASHBOARD_FIELDS:
        processed[field] = _rehydrate_dashboard_grounded_items(
            processed.get(field) or [],
            field,
            skeleton=context,
            identity_by_id=identity_by_id,
        )
    for field in ("notable_details", "background_details"):
        processed[field] = dedupe_display_strings(processed.get(field) or [])
    social_map = []
    for item in processed.get("social_map") or []:
        if isinstance(item, dict):
            clean = dict(item)
            clean["summary"] = _clean_social_summary(str(clean.get("summary") or ""))
            social_map.append(clean)
    processed["social_map"] = social_map
    if context.get("kind") == "dashboard":
        candidate_errors: list[str] = []
        for field in GROUNDED_DASHBOARD_FIELDS:
            _validate_skeleton_list(processed.get(field), field=field, skeleton=context, errors=candidate_errors)
        if candidate_errors:
            raise ClubGenerationError("; ".join(candidate_errors))
        presentation = _compose_dashboard_presentation(context)
        processed["room_situation"] = presentation.room_situation
        processed["hot_connections"] = [record.text for record in presentation.hot_connections]
        processed["possible_pressure"] = [record.text for record in presentation.possible_pressure]
        processed["rumors_in_circulation"] = [record.text for record in presentation.rumors]
        processed["guest_brief"] = list(presentation.guest_brief)
        processed["top_connections"] = [record.support for record in presentation.hot_connections]
        processed["possible_drama"] = [record.support for record in presentation.possible_pressure]
        processed["rumors"] = [record.support for record in presentation.rumors]
    else:
        processed["room_situation"] = clean_display_text(str(processed.get("room_situation") or ""))
        for field in ("hot_connections", "possible_pressure", "rumors_in_circulation", "guest_brief"):
            processed[field] = _postprocess_visible_strings(processed.get(field), fallback=[])
    if context.get("rumor_selection"):
        processed["rumor_selection"] = dict(context.get("rumor_selection") or {})
    return validate_dashboard(
        processed,
        source_ids=set(context.get("source_ids") or []),
        attendee_ids=set(context.get("attendee_ids") or []),
        skeleton=context if context.get("kind") == "dashboard" else None,
    )


def _rehydrate_dashboard_grounded_items(
    items: Sequence[Any],
    field: str,
    *,
    skeleton: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if skeleton.get("kind") != "dashboard":
        return [_with_prep_text(item, field, identity_by_id=identity_by_id) for item in dedupe_grounded_facts(items)]
    rehydrated: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, item in enumerate(items):
        clean = _rehydrate_skeleton_grounded_item(
            item,
            f"{field}[{idx}]",
            skeleton=skeleton,
            identity_by_id=identity_by_id,
            map_field=field,
            errors=errors,
        )
        if clean is not None:
            rehydrated.append(clean)
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return rehydrated


def postprocess_npc_panel(panel: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    processed = dict(panel)
    fallback = npc_panel_from_skeleton(skeleton) if skeleton.get("kind") == "npc_panel" else {}
    identity_by_id = {str(skeleton.get("npc_id") or ""): {"display_name": clean_display_text(str(skeleton.get("name") or ""))}}
    processed["identity"] = dict(skeleton.get("identity") or processed.get("identity") or {})
    processed["personality"] = list(skeleton.get("personality") or processed.get("personality") or [])
    processed["current_read"] = clean_display_text(str(processed.get("current_read") or fallback.get("current_read") or ""))
    processed["likely_conversation"] = _postprocess_visible_strings(
        processed.get("likely_conversation"),
        fallback=fallback.get("likely_conversation") or [],
    )
    processed["sensitive_subjects"] = _postprocess_visible_strings(
        processed.get("sensitive_subjects"),
        fallback=fallback.get("sensitive_subjects") or [],
    )
    processed["people_here"] = _rehydrate_npc_grounded_items(
        processed.get("people_here") or [],
        "people_here",
        skeleton=skeleton,
        identity_by_id=identity_by_id,
    )
    tonight = dict(processed.get("tonight") or {})
    skeleton_tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    if not clean_display_text(str(tonight.get("current_demeanor") or "")):
        tonight["current_demeanor"] = str(skeleton_tonight.get("current_demeanor") or "")
    errors: list[str] = []
    current_desire = _rehydrate_skeleton_grounded_item(
        tonight.get("current_desire"),
        "tonight.current_desire",
        skeleton=skeleton,
        identity_by_id=identity_by_id,
        errors=errors,
    )
    if current_desire is not None:
        tonight["current_desire"] = current_desire
    elif isinstance(skeleton_tonight.get("current_desire"), dict):
        tonight["current_desire"] = _with_prep_text(
            skeleton_tonight["current_desire"],
            "current_desire",
            identity_by_id=identity_by_id,
        )
    processed["tonight"] = tonight
    raw_conversation = processed.get("conversation") if isinstance(processed.get("conversation"), dict) else {}
    conversation = dict(raw_conversation)
    conversation["likely_subjects"] = _rehydrate_npc_grounded_items(
        conversation.get("likely_subjects") or [],
        "conversation.likely_subjects",
        skeleton=skeleton,
        identity_by_id=identity_by_id,
    )
    conversation["sensitive"] = _rehydrate_npc_grounded_items(
        conversation.get("sensitive") or [],
        "conversation.sensitive",
        skeleton=skeleton,
        identity_by_id=identity_by_id,
    )
    processed["conversation"] = conversation
    interesting_detail = _rehydrate_skeleton_grounded_item(
        processed.get("interesting_detail"),
        "interesting_detail",
        skeleton=skeleton,
        identity_by_id=identity_by_id,
        errors=errors,
    )
    if interesting_detail is not None:
        processed["interesting_detail"] = interesting_detail
    elif isinstance(skeleton.get("interesting_detail"), dict):
        processed["interesting_detail"] = _with_prep_text(
            skeleton["interesting_detail"],
            "interesting_detail",
            identity_by_id=identity_by_id,
        )
    elif processed.get("interesting_detail") is None:
        processed["interesting_detail"] = ""
    useful_hook = clean_display_text(str(processed.get("useful_hook") or ""))
    processed["useful_hook"] = useful_hook or str(fallback.get("useful_hook") or "")
    processed["presentation_suppressed"] = list(skeleton.get("presentation_suppressed") or [])
    if errors:
        raise ClubGenerationError("; ".join(errors))
    if skeleton.get("kind") == "npc_panel":
        processed = _compose_npc_panel_factual_fields(processed, skeleton)
    return validate_npc_panel(
        processed,
        source_ids=set(skeleton.get("source_ids") or []),
        npc_id=str(skeleton.get("npc_id") or ""),
        skeleton=skeleton if skeleton.get("kind") == "npc_panel" else None,
    )


def _rehydrate_npc_grounded_items(
    items: Sequence[Any],
    field: str,
    *,
    skeleton: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rehydrated: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, item in enumerate(items):
        clean = _rehydrate_skeleton_grounded_item(
            item,
            f"{field}[{idx}]",
            skeleton=skeleton,
            identity_by_id=identity_by_id,
            map_field=field,
            errors=errors,
        )
        if clean is not None:
            rehydrated.append(clean)
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return rehydrated


def _rehydrate_skeleton_grounded_item(
    item: Any,
    field: str,
    *,
    skeleton: dict[str, Any],
    identity_by_id: dict[str, dict[str, Any]],
    map_field: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    lookup_field = map_field or field
    validation_errors: list[str] = []
    _validate_skeleton_item(item, field=field, skeleton=skeleton, errors=validation_errors, map_field=lookup_field)
    hard_errors = [error for error in validation_errors if ".summary must match skeleton item " not in error]
    if hard_errors:
        if errors is not None:
            errors.extend(hard_errors)
        return None
    expected = (_skeleton_item_maps(skeleton).get(lookup_field) or {}).get(str(item.get("item_id") or ""))
    if expected is None:
        return None
    clean = dict(expected)
    prep_text = clean_display_text(str(item.get("prep_text") or ""))
    if prep_text:
        clean["prep_text"] = _sentence(prep_text)
    else:
        clean["prep_text"] = _prep_text_for_item(clean, field.rsplit(".", 1)[-1], identity_by_id=identity_by_id)
    return clean


def _postprocess_visible_strings(value: Any, *, fallback: Sequence[Any]) -> list[str]:
    if not isinstance(value, list):
        return dedupe_display_strings(fallback)
    strings = [_display_text_for_render(item) for item in value]
    return dedupe_display_strings(strings) or dedupe_display_strings(fallback)


def safe_debug_data(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_debug_key(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = safe_debug_data(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [safe_debug_data(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if value.__class__.__name__ == "LiteralSecret":
        return "<redacted>"
    return f"<{value.__class__.__name__}>"


def safe_debug_json(value: Any) -> str:
    return json.dumps(safe_debug_data(value), ensure_ascii=False, indent=2, sort_keys=True)


def _clean_social_summary(text: str) -> str:
    clean = clean_display_text(text)
    clean = re.sub(r"\bvisible useful tension\b", "useful pressure", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bvisible connection tension\b", "connection pressure", clean, flags=re.IGNORECASE)
    return clean


def _identity_for_prompt(identity: NpcIdentity) -> dict[str, Any]:
    return {
        "npc_id": identity.npc_id,
        "display_name": clean_display_text(identity.display_name),
        "aliases": [clean_display_text(alias) for alias in identity.aliases],
    }


def _compact_index_for_prompt(index: ClubIndex) -> dict[str, Any]:
    return {
        "npc_id": index.npc_id,
        "name": clean_display_text(index.name),
        "affiliation": clean_display_text(index.affiliation),
        "faction": clean_display_text(index.faction),
        "status": clean_display_text(index.status),
        "roles": [clean_display_text(role) for role in index.roles],
        "personality_tags": dedupe_display_strings(index.personality_tags),
        "demeanor": _club_attribute_payload(index.demeanor),
        "appearance": _club_attribute_payload(index.appearance),
        "mask_identity": _club_attribute_payload(index.mask_identity),
        "origin": _club_attribute_payload(index.origin),
        "pronouns": _club_attribute_payload(index.pronouns),
        "political_tags": [clean_display_text(tag) for tag in index.political_tags],
        "relationship_count": len(index.relationships),
        "goal_count": len(index.current_goals),
        "grievance_count": len(index.grievances),
        "unresolved_business_count": len(index.unresolved_business),
        "rumor_count": len(index.rumor_notes),
        "recent_event_count": len(index.recent_relevant_events),
    }


def _typed_index_attributes(index: ClubIndex) -> list[tuple[str, ClubAttribute]]:
    return [
        (name, attribute)
        for name, attribute in (
            ("demeanor", index.demeanor),
            ("appearance", index.appearance),
            ("mask_identity", index.mask_identity),
            ("origin", index.origin),
            ("pronouns", index.pronouns),
        )
        if attribute is not None
    ]


def _club_attribute_payload(attribute: ClubAttribute | None) -> dict[str, Any] | None:
    if attribute is None:
        return None
    return {
        "value": clean_display_text(attribute.value),
        "sources": [source.to_dict() for source in attribute.sources],
    }


def _source_ids_from_items(items: Iterable[dict[str, Any]]) -> set[str]:
    source_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for source in item.get("sources") or []:
            if isinstance(source, dict) and source.get("source_id"):
                source_ids.add(str(source["source_id"]))
    return source_ids


def _source_ids_from_attribute_payloads(index: dict[str, Any]) -> set[str]:
    source_ids: set[str] = set()
    for field in ("demeanor", "appearance", "mask_identity", "origin", "pronouns"):
        attribute = index.get(field)
        if not isinstance(attribute, dict):
            continue
        for source in attribute.get("sources") or []:
            if isinstance(source, dict) and source.get("source_id"):
                source_ids.add(str(source["source_id"]))
    return source_ids


def _compact_source_map(source_map: dict[str, dict[str, Any]], source_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    compact: dict[str, dict[str, Any]] = {}
    for source_id in sorted(str(source_id) for source_id in source_ids):
        entry = source_map.get(source_id)
        if not isinstance(entry, dict):
            continue
        source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        compact[source_id] = {
            "source_id": source_id,
            "npc_id": str(entry.get("npc_id") or source.get("character_id") or ""),
            "name": clean_display_text(str(entry.get("name") or "")),
            "section": clean_display_text(str(source.get("section") or "")),
            "attribute_type": clean_display_text(str(entry.get("attribute_type") or "")),
            "attribute_value": clean_display_text(str(entry.get("attribute_value") or ""))[:PROMPT_SOURCE_EXCERPT_CHARS],
            "fact_type": clean_display_text(str(entry.get("fact_type") or "")),
            "summary": clean_display_text(str(entry.get("summary") or ""))[:PROMPT_SOURCE_EXCERPT_CHARS],
            "target_name": clean_display_text(str(entry.get("target_name") or "")),
            "target_npc_id": None if str(entry.get("fact_type") or "").lower() == "rumor" else str(entry.get("target_npc_id") or ""),
        }
    return compact


def _compact_sources_for_prompt(sources: Iterable[Any]) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if source_id:
            compact.append({"source_id": source_id})
    return compact


def _context_from_result(result: ClubBuildResult) -> dict[str, Any]:
    return {
        "attendee_ids": list(result.event.attendee_ids),
        "identities": [_identity_for_prompt(identity) for identity in result.identities],
        "indexes": [_compact_index_for_prompt(index) for index in result.indexes_by_id.values()],
        "attendee_relationships": list(result.attendee_relationships),
        "attendee_facts": list(result.attendee_facts),
        "source_ids": sorted(result.source_map),
    }


def _item_id_for(item: dict[str, Any], section: str) -> str:
    return stable_hash(
        {
            "section": section,
            "key": grounded_fact_key(item),
            "characters": list(item.get("characters") or []),
            "sources": sorted(_source_ids_from_items([item])),
        }
    )[:16]


def _clone_grounded_item(
    item: dict[str, Any],
    section: str,
    *,
    identity_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean = dict(item)
    clean["summary"] = clean_display_text(str(clean.get("summary") or ""))
    clean["item_id"] = str(clean.get("item_id") or _item_id_for(clean, section))
    clean["section"] = section
    clean["sources"] = _dedupe_sources(clean.get("sources") or [])
    clean["characters"] = [str(character) for character in clean.get("characters") or []]
    clean["display_label"] = _display_label_for_item(clean)
    clean["prep_text"] = _prep_text_for_item(clean, section, identity_by_id=identity_by_id or {})
    return clean


def _with_prep_text(item: dict[str, Any], section: str, *, identity_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    clean = dict(item)
    clean["summary"] = clean_display_text(str(clean.get("summary") or ""))
    clean["display_label"] = clean_display_text(str(clean.get("display_label") or _display_label_for_item(clean)))
    if not clean_display_text(str(clean.get("prep_text") or "")):
        clean["prep_text"] = _prep_text_for_item(clean, section, identity_by_id=identity_by_id)
    else:
        clean["prep_text"] = _sentence(str(clean["prep_text"]))
    return clean


def _prep_text_for_item(item: dict[str, Any], section: str, *, identity_by_id: dict[str, dict[str, Any]]) -> str:
    label = clean_display_text(str(item.get("display_label") or item.get("type") or ""))
    label_lower = label.lower()
    summary = _strip_fact_prefix(clean_display_text(str(item.get("summary") or "")), item, label=label, identity_by_id=identity_by_id)
    subject, target = _named_pair_for_item(item, identity_by_id)
    pressure = _room_pressure_label(item)
    pressure_phrase = _pressure_phrase(pressure)
    if section == "possible_drama":
        break_reason = _dashboard_break_reason(item)
        if break_reason:
            if subject and target:
                return _sentence(f"{subject} and {target}: {break_reason}. {summary}")
            return _sentence(f"{break_reason}: {summary}")
        if subject and target:
            if pressure == "connection":
                return _sentence(f"{subject} and {target} have a tension the PCs might notice tonight: {summary}")
            return _sentence(f"{subject} and {target} carry {pressure_phrase} that could surface tonight: {summary}")
        return _sentence(f"This could become visible trouble tonight: {summary}")
    if section == "opportunities":
        if subject and target:
            return _sentence(f"The PCs can press {subject} about {target}: {summary}")
        if subject:
            return _sentence(f"The PCs can press {subject}: {summary}")
        return _sentence(f"The PCs can act on this tonight: {summary}")
    if section == "rumors":
        return _sentence(f"People may be whispering: {summary}")
    if section == "unresolved_business":
        if subject and target:
            return _sentence(f"{subject} has unresolved business involving {target}: {summary}")
        return _sentence(f"Unresolved business is still live tonight: {summary}")
    if section in {"people_here", "top_connections", "interesting_connections"}:
        if subject and target and label_lower and label_lower not in {"relationship", "topic"}:
            return _sentence(f"{_relationship_opening(subject, target, label_lower)}: {summary}")
        if subject and target:
            return _sentence(f"{subject} and {target} matter to each other tonight: {summary}")
    if section == "current_desire" and subject:
        return _sentence(f"{subject} may be pursuing this tonight: {summary}")
    if section == "sensitive":
        return _sentence(f"Do not volunteer this unless the table pushes for it: {summary}")
    if section == "likely_subjects":
        return _sentence(f"They might talk about this if approached: {summary}")
    if subject:
        return _sentence(f"{subject}: {summary}")
    return _sentence(summary)


def _strip_fact_prefix(
    summary: str,
    item: dict[str, Any],
    *,
    label: str,
    identity_by_id: dict[str, dict[str, Any]],
) -> str:
    text = clean_display_text(summary)
    names = [name for name in _names_for_item(item, identity_by_id) if name]
    fragments = [label, *names]
    for fragment in fragments:
        if fragment:
            text = re.sub(rf"^\s*{re.escape(fragment)}\s*:\s*", "", text, flags=re.IGNORECASE)
    for name in names:
        if label:
            text = re.sub(rf"^\s*{re.escape(name)}\s*\({re.escape(label)}\)\s*:\s*", "", text, flags=re.IGNORECASE)
    if label:
        text = re.sub(rf"^\s*[^:()]{{1,80}}\s*\({re.escape(label)}\)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"^\s*[^:]{{1,120}}\s+{re.escape(label)}\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*[^:()]{1,80}\s*\([^)]{1,80}\)\s*:\s*", "", text)
    text = re.sub(r"^\s*[A-Z][A-Za-z0-9' -]{1,50}\s*:\s*", "", text)
    if len(names) >= 2 and label:
        text = re.sub(
            rf"^\s*{re.escape(names[0])}\s+{re.escape(names[1])}\s+{re.escape(label)}\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
    return text.strip()


def _named_pair_for_item(item: dict[str, Any], identity_by_id: dict[str, dict[str, Any]]) -> tuple[str, str]:
    characters = [str(character) for character in item.get("characters") or [] if str(character)]
    if item.get("source_npc_id") and item.get("target_npc_id"):
        characters = [str(item["source_npc_id"]), str(item["target_npc_id"])]
    names = [_identity_display_name(npc_id, identity_by_id) for npc_id in characters[:2]]
    return (names[0] if names else "", names[1] if len(names) > 1 else "")


def _names_for_item(item: dict[str, Any], identity_by_id: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name in _named_pair_for_item(item, identity_by_id) if name]


def _sentence(text: str) -> str:
    clean = clean_display_text(text).strip()
    if not clean:
        return ""
    return clean if clean.endswith((".", "!", "?")) else f"{clean}."


def _relationship_opening(subject: str, target: str, label: str) -> str:
    clean_label = clean_display_text(label).lower()
    if clean_label in {"toy", "pawn", "tool"}:
        return f"{subject} treats {target} as a {label}"
    if clean_label in {"paramour", "lover"}:
        return f"{subject} is visibly entangled with {target}"
    if clean_label in {"friend", "friendship", "ally"}:
        return f"{subject} can rely on {target}"
    if clean_label in {"useful", "contact"}:
        return f"{target} is politically useful to {subject}"
    if clean_label in {"business"}:
        return f"{subject} has business with {target}"
    if clean_label in {"offended", "offense"}:
        return f"{subject} is offended by {target}"
    if clean_label in {"admired", "admiration"}:
        return f"{subject} admires {target}, but the tie has an edge"
    if clean_label in {"blackmail", "leverage"}:
        return f"{subject} has leverage over {target}"
    if clean_label in {"debt", "favor"}:
        return f"{subject} owes or holds a debt involving {target}"
    if clean_label in {"distrust"}:
        return f"{subject} distrusts {target}"
    if clean_label in {"business", "contact"}:
        return f"{subject} has business leverage with {target}"
    return f"{subject} and {target} have a grounded relationship ({clean_display_text(label).title()})"


def _pressure_phrase(pressure: str) -> str:
    if pressure == "coercive bond":
        return "blood-bond pressure"
    if pressure in {"blackmail", "coercion", "manipulation", "exploitation"}:
        return f"{pressure} pressure"
    return f"{pressure} pressure"


def article_for(word: str) -> str:
    return "an" if str(word or "").strip()[:1].lower() in {"a", "e", "i", "o", "u"} else "a"


def _reserve_items(items: Sequence[dict[str, Any]], used_keys: set[tuple[str, str, str]], *, limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in items:
        key = _selection_key(item)
        if key in used_keys:
            continue
        used_keys.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _selection_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("source_npc_id") or (item.get("characters") or [""])[0]),
        str(item.get("type") or ""),
        _first_source_id(item),
    )


def _rank_directed_panel_facts(
    items: Sequence[dict[str, Any]],
    *,
    identity_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    identity_by_id = identity_by_id or {}
    return sorted(
        dedupe_grounded_facts(items),
        key=lambda item: (
            -_prompt_item_score(item),
            _identity_display_name(str(item.get("target_npc_id") or ""), identity_by_id).lower(),
            _normalized_display_key(str(item.get("summary") or "")),
            _first_source_id(item),
        ),
    )


def _display_label_for_item(item: dict[str, Any]) -> str:
    fact_type = clean_display_text(str(item.get("type") or ""))
    fact_scope = str(item.get("fact_scope") or "")
    if fact_scope == "mention":
        return "Topic"
    if fact_scope == "relationship":
        return fact_type.title() if fact_type else "Relationship"
    if fact_type == "goal":
        return "Goal"
    if fact_type == "rumor":
        return "Rumor"
    if fact_type == "recent_event":
        return "Recent Event"
    if fact_type == "unresolved_business":
        return "Unresolved Business"
    return fact_type.title() if fact_type else ""


def room_facing_social_map(
    attendee_ids: Sequence[str],
    relationships: Sequence[dict[str, Any]],
    *,
    identity_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = cluster_social_map(attendee_ids, relationships)
    result: list[dict[str, Any]] = []
    for group in groups:
        npc_ids = [str(npc_id) for npc_id in group.get("npc_ids") or []]
        if not npc_ids:
            continue
        if str(group.get("location") or "").startswith("Connected Group"):
            strongest = _strongest_room_label_connection(
                npc_ids,
                relationships,
                attendee_ids=attendee_ids,
            )
            label = _room_group_label(strongest)
            summary = _room_group_summary(npc_ids, identity_by_id=identity_by_id, strongest=strongest)
        else:
            label = "Circulating / Unanchored"
            summary = "No attendee relationship edge was found in the current club indexes."
        result.append({"location": label, "npc_ids": npc_ids, "summary": summary})
    return result


def _strongest_room_label_connection(
    npc_ids: Sequence[str],
    relationships: Sequence[dict[str, Any]],
    *,
    attendee_ids: Sequence[str],
) -> dict[str, Any] | None:
    component = {str(npc_id) for npc_id in npc_ids}
    attendee_order = {str(npc_id): index for index, npc_id in enumerate(attendee_ids)}
    candidates: list[dict[str, Any]] = []
    for item in relationships:
        if not isinstance(item, dict):
            continue
        source_npc_id = str(item.get("source_npc_id") or "")
        target_npc_id = str(item.get("target_npc_id") or "")
        sources = item.get("sources") or []
        first_source = sources[0] if sources and isinstance(sources[0], dict) else {}
        source_id = str(first_source.get("source_id") or "")
        fact_type = _room_label_normalized_key(item.get("type"))
        if (
            not source_id.strip()
            or str(item.get("fact_scope") or "") != "relationship"
            or not source_npc_id
            or not target_npc_id
            or source_npc_id == target_npc_id
            or source_npc_id not in component
            or target_npc_id not in component
            or source_npc_id not in attendee_order
            or target_npc_id not in attendee_order
            or fact_type in GENERIC_ROOM_RELATIONSHIP_TYPES
        ):
            continue
        candidates.append(item)
    if not candidates:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        sources = item.get("sources") or []
        first_source = sources[0] if sources and isinstance(sources[0], dict) else {}
        source_npc_id = str(item.get("source_npc_id") or "")
        target_npc_id = str(item.get("target_npc_id") or "")
        return (
            -_dramatic_item_score(item),
            attendee_order[source_npc_id],
            _room_label_normalized_key(first_source.get("path")),
            _room_label_normalized_key(first_source.get("section")),
            str(first_source.get("source_id") or ""),
            _room_label_normalized_key(item.get("type")),
            _room_label_normalized_key(item.get("summary")),
            source_npc_id,
            target_npc_id,
        )

    return deepcopy(sorted(candidates, key=sort_key)[0])


def _room_group_label(strongest: dict[str, Any] | None) -> str:
    if strongest:
        fact_type = _room_label_display(strongest.get("type")).title()
        return f"{fact_type} pressure point"
    return "Connected cluster"


def _room_label_display(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", clean_display_text(str(value or ""))).split())


def _room_label_normalized_key(value: Any) -> str:
    return _room_label_display(value).casefold()


def _room_group_summary(
    npc_ids: Sequence[str],
    *,
    identity_by_id: dict[str, dict[str, Any]],
    strongest: dict[str, Any] | None,
) -> str:
    if strongest:
        fact_type = _room_pressure_label(strongest)
        pressure_phrase = _pressure_phrase(fact_type)
        strongest_ids = [
            str(npc_id)
            for npc_id in (strongest.get("source_npc_id"), strongest.get("target_npc_id"))
            if str(npc_id) in set(npc_ids)
        ]
        if len(strongest_ids) >= 2:
            first = _identity_display_name(strongest_ids[0], identity_by_id)
            second = _identity_display_name(strongest_ids[1], identity_by_id)
            others = [npc_id for npc_id in npc_ids if npc_id not in set(strongest_ids[:2])]
            if others:
                other_names = ", ".join(_identity_display_name(npc_id, identity_by_id) for npc_id in others)
                verb = "remain" if len(others) > 1 else "remains"
                return f"{first} and {second} sit at the center of {pressure_phrase}; {other_names} {verb} tied into the same knot."
            return f"{first} and {second} sit at the center of {pressure_phrase}."
        names = ", ".join(_identity_display_name(npc_id, identity_by_id) for npc_id in npc_ids)
        return f"{names} share {pressure_phrase}."
    names = ", ".join(_identity_display_name(npc_id, identity_by_id) for npc_id in npc_ids)
    return f"{names} share grounded relationship edges."


def _room_pressure_label(item: dict[str, Any]) -> str:
    fact_type = clean_display_text(str(item.get("type") or "")).lower()
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    for key, label in PRESSURE_LABELS.items():
        if key in fact_type or key in summary:
            return label
    return "connection"


def canonical_target_npc_id(item: dict[str, Any]) -> str | None:
    value = item.get("target_npc_id")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("target_npc_id must be a string or null")
    normalized = value.strip()
    return normalized or None


def canonical_involved_npc_ids(item: dict[str, Any]) -> tuple[str, ...]:
    ordered: list[str] = []

    def add(value: Any, *, field: str) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise ValueError(f"{field} entries must be strings")
        normalized = value.strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    add(item.get("source_npc_id"), field="source_npc_id")
    add(canonical_target_npc_id(item), field="target_npc_id")
    mentioned = item.get("mentioned_npc_ids")
    if mentioned is None:
        mentioned = ()
    if not isinstance(mentioned, (list, tuple)):
        raise ValueError("mentioned_npc_ids must be a list or tuple")
    for npc_id in mentioned:
        add(npc_id, field="mentioned_npc_ids")
    return tuple(ordered)


def _normalize_attendee_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.translate(
        {
            ord("\u2018"): "'",
            ord("\u2019"): "'",
            ord("\u02bc"): "'",
            ord("\uff07"): "'",
        }
    )
    return " ".join(normalized.casefold().split())


def _candidate_occurrences(text: str, candidate: str, occupied: list[tuple[int, int]]) -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int]] = []
    start = 0
    while True:
        start = text.find(candidate, start)
        if start < 0:
            return occurrences
        end = start + len(candidate)
        before_ok = start == 0 or not text[start - 1].isalnum()
        match_end = end
        if end + 1 < len(text) and text[end] == "'" and text[end + 1] == "s":
            possessive_end = end + 2
            if possessive_end == len(text) or not text[possessive_end].isalnum():
                match_end = possessive_end
        after_ok = match_end == len(text) or not text[match_end].isalnum()
        overlaps = any(start < used_end and match_end > used_start for used_start, used_end in occupied)
        if before_ok and after_ok and not overlaps:
            occurrences.append((start, match_end))
        start = max(start + 1, end)


def _resolved_attendee_ids(text: str, attendees: Sequence[Any]) -> tuple[str, ...]:
    normalized_text = _normalize_attendee_match_text(clean_display_text(text))
    form_ids: dict[str, set[str]] = {}
    full_forms: set[str] = set()
    alias_forms: set[str] = set()
    for attendee in attendees:
        if not isinstance(attendee, dict):
            continue
        npc_id = _normalized_string(attendee.get("npc_id"))
        if npc_id is None:
            continue
        display_name = attendee.get("display_name")
        if isinstance(display_name, str):
            form = _normalize_attendee_match_text(display_name)
            if form:
                form_ids.setdefault(form, set()).add(npc_id)
                full_forms.add(form)
        aliases = attendee.get("aliases")
        if not isinstance(aliases, (list, tuple)):
            continue
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            form = _normalize_attendee_match_text(alias)
            if form and sum(character.isalnum() for character in form) >= 3:
                form_ids.setdefault(form, set()).add(npc_id)
                alias_forms.add(form)

    unique = {form: next(iter(npc_ids)) for form, npc_ids in form_ids.items() if len(npc_ids) == 1}
    occupied: list[tuple[int, int]] = []
    matches: list[tuple[int, str]] = []

    def scan(forms: Iterable[str]) -> None:
        candidates = sorted(
            (form for form in forms if form in unique),
            key=lambda form: (-len(form), form, unique[form]),
        )
        for form in candidates:
            for match_start, match_end in _candidate_occurrences(normalized_text, form, occupied):
                occupied.append((match_start, match_end))
                matches.append((match_start, unique[form]))

    scan(full_forms)
    scan(alias_forms - full_forms)
    resolved: list[str] = []
    for _start, npc_id in sorted(matches):
        if npc_id not in resolved:
            resolved.append(npc_id)
    return tuple(resolved)


def _presentation_diagnostic(section: str, item_id: str, category: str) -> _PresentationDiagnostic:
    return _PresentationDiagnostic(section, item_id, category, "omitted")


def _emit_presentation_diagnostics(diagnostics: Sequence[_PresentationDiagnostic]) -> None:
    for diagnostic in sorted(set(diagnostics)):
        print(
            "club_presentation "
            f"section={_sanitized_diagnostic_token(diagnostic.section)} "
            f"item_id={_sanitized_diagnostic_token(diagnostic.item_id)} "
            f"failure_category={_sanitized_diagnostic_token(diagnostic.failure_category)} "
            f"disposition={_sanitized_diagnostic_token(diagnostic.disposition)}"
        )


def _sanitized_diagnostic_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:<>{}-]", "_", value)[:80]


def _normalized_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _render_factual_text(section: str, summary: str) -> str:
    prefixes = {
        "top_connections": "Connection — ",
        "possible_drama": "Possible pressure — ",
        "rumors": "Unverified rumor — ",
    }
    return f"{prefixes[section]}{summary}"


def _admit_factual_section(
    items: Sequence[Any],
    *,
    section: str,
    attendees: Sequence[Any],
    attendee_ids: set[str],
    source_ids: set[str],
) -> tuple[tuple[_AdmittedFactRecord, ...], tuple[_PresentationDiagnostic, ...]]:
    normalized_ids = [
        _normalized_string(item.get("item_id")) if isinstance(item, dict) else None
        for item in items
    ]
    id_counts: dict[str, int] = {}
    for item_id in normalized_ids:
        if item_id:
            id_counts[item_id] = id_counts.get(item_id, 0) + 1
    duplicate_ids = {item_id for item_id, count in id_counts.items() if count > 1}
    records: list[_AdmittedFactRecord] = []
    diagnostics: list[_PresentationDiagnostic] = []

    for index, item in enumerate(items):
        diagnostic_id = normalized_ids[index] or f"<missing:{index}>"
        if not isinstance(item, dict):
            diagnostics.append(_presentation_diagnostic(section, diagnostic_id, "invalid_item_shape"))
            continue
        item_section = _normalized_string(item.get("section"))
        fact_type = _normalized_string(item.get("type"))
        fact_scope = _normalized_string(item.get("fact_scope"))
        characters_value = item.get("characters")
        if (
            item_section != section
            or fact_type is None
            or fact_scope not in {"relationship", "mention", "self"}
            or not isinstance(characters_value, (list, tuple))
            or any(not isinstance(character, str) or not character.strip() for character in characters_value)
            or (section == "rumors" and fact_type.casefold() != "rumor")
        ):
            diagnostics.append(_presentation_diagnostic(section, diagnostic_id, "invalid_item_shape"))
            continue
        item_id = normalized_ids[index]
        if item_id is None:
            diagnostics.append(_presentation_diagnostic(section, diagnostic_id, "missing_item_id"))
            continue
        if item_id in duplicate_ids:
            diagnostics.append(_presentation_diagnostic(section, item_id, "duplicate_item_id"))
            continue

        source_npc_id = _normalized_string(item.get("source_npc_id"))
        if source_npc_id is None or source_npc_id not in attendee_ids:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_source_npc_id"))
            continue

        try:
            target_npc_id = canonical_target_npc_id(item)
        except ValueError:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_target_npc_id"))
            continue
        if (
            (section == "top_connections" and target_npc_id is None)
            or (section == "rumors" and target_npc_id is not None)
            or (target_npc_id is not None and (target_npc_id not in attendee_ids or target_npc_id == source_npc_id))
        ):
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_target_npc_id"))
            continue

        mentioned_value = item.get("mentioned_npc_ids")
        if mentioned_value is None:
            mentioned_value = ()
        if not isinstance(mentioned_value, (list, tuple)):
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_mentioned_npc_ids"))
            continue
        mentioned_npc_ids: list[str] = []
        invalid_mentioned = False
        for mentioned in mentioned_value:
            normalized = _normalized_string(mentioned)
            if normalized is None or normalized not in attendee_ids:
                invalid_mentioned = True
                break
            if normalized not in mentioned_npc_ids:
                mentioned_npc_ids.append(normalized)
        if invalid_mentioned:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_mentioned_npc_ids"))
            continue

        expected_scope = "relationship" if target_npc_id else ("mention" if mentioned_npc_ids else "self")
        normalized_characters = tuple(character.strip() for character in characters_value)
        expected_characters = (source_npc_id, target_npc_id) if target_npc_id else (source_npc_id,)
        if fact_scope != expected_scope or normalized_characters != expected_characters:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_item_shape"))
            continue

        summary_value = item.get("summary")
        if not isinstance(summary_value, str):
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_summary"))
            continue
        summary = clean_display_text(summary_value)
        if not summary:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_summary"))
            continue

        sources_value = item.get("sources")
        if not isinstance(sources_value, list) or not sources_value:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_sources"))
            continue
        sources: list[dict[str, Any]] = []
        invalid_sources = False
        for source in sources_value:
            source_id = _normalized_string(source.get("source_id")) if isinstance(source, dict) else None
            if source_id is None or source_id not in source_ids:
                invalid_sources = True
                break
            sources.append(dict(source))
        if invalid_sources:
            diagnostics.append(_presentation_diagnostic(section, item_id, "invalid_sources"))
            continue

        support = dict(item)
        support.update(
            {
                "item_id": item_id,
                "section": section,
                "type": fact_type,
                "fact_scope": fact_scope,
                "source_npc_id": source_npc_id,
                "target_npc_id": target_npc_id,
                "mentioned_npc_ids": list(mentioned_npc_ids),
                "characters": list(expected_characters),
                "summary": summary,
                "sources": sources,
            }
        )
        involved_npc_ids = canonical_involved_npc_ids(support)
        if not set(_resolved_attendee_ids(summary, attendees)).issubset(involved_npc_ids):
            diagnostics.append(_presentation_diagnostic(section, item_id, "unsupported_attendee_reference"))
            continue
        text = _render_factual_text(section, summary)
        if not set(_resolved_attendee_ids(text, attendees)).issubset(involved_npc_ids):
            diagnostics.append(_presentation_diagnostic(section, item_id, "unsupported_attendee_reference"))
            continue
        support["prep_text"] = text
        records.append(_AdmittedFactRecord(section, item_id, text, involved_npc_ids, support))

    return tuple(records), tuple(sorted(set(diagnostics)))


def _format_room_group(names: Sequence[str]) -> str:
    if len(names) == 1:
        return f"{names[0]} stands apart from the other groups."
    if len(names) == 2:
        return f"{names[0]} and {names[1]} form one conversation group."
    return f"{', '.join(names[:-1])}, and {names[-1]} form one conversation group."


def _compose_dashboard_presentation(skeleton: dict[str, Any]) -> _DashboardPresentation:
    attendees = tuple(item for item in skeleton.get("attendees") or [] if isinstance(item, dict))
    attendee_order = tuple(
        npc_id
        for item in attendees
        if (npc_id := _normalized_string(item.get("npc_id"))) is not None
    )
    canonical_attendee_order = tuple(
        npc_id
        for value in skeleton.get("attendee_ids") or []
        if (npc_id := _normalized_string(value)) is not None
    )
    if attendee_order != canonical_attendee_order or len(set(attendee_order)) != len(attendee_order):
        raise ClubGenerationError("Dashboard attendees must match canonical attendee_ids in order.")
    attendee_ids = set(attendee_order)
    source_ids = {
        source_id
        for value in skeleton.get("source_ids") or []
        if (source_id := _normalized_string(value)) is not None
    }

    def selected(field: str, limit: int | None = None) -> tuple[Any, ...]:
        value = skeleton.get(field)
        items = tuple(value) if isinstance(value, (list, tuple)) else ()
        return items if limit is None else items[:limit]

    top, top_diagnostics = _admit_factual_section(
        selected("top_connections", 3), section="top_connections", attendees=attendees, attendee_ids=attendee_ids, source_ids=source_ids
    )
    pressure, pressure_diagnostics = _admit_factual_section(
        selected("possible_drama", 4), section="possible_drama", attendees=attendees, attendee_ids=attendee_ids, source_ids=source_ids
    )
    rumors, rumor_diagnostics = _admit_factual_section(
        selected("rumors"), section="rumors", attendees=attendees, attendee_ids=attendee_ids, source_ids=source_ids
    )
    diagnostics = tuple(sorted(set((*top_diagnostics, *pressure_diagnostics, *rumor_diagnostics))))

    name_by_id = {
        npc_id: clean_display_text(str(item.get("display_name") or npc_id))
        for item in attendees
        if (npc_id := _normalized_string(item.get("npc_id"))) is not None
    }
    event = skeleton.get("event") if isinstance(skeleton.get("event"), dict) else {}
    late_arrival_id = _normalized_string(event.get("late_arrival_id")) or ""
    guest_rows: list[tuple[str, str, str]] = []
    for npc_id in attendee_order:
        guest_name = name_by_id[npc_id]
        record = next((candidate for candidate in pressure if npc_id in candidate.involved_npc_ids), None)
        if record is None:
            record = next((candidate for candidate in top if npc_id in candidate.involved_npc_ids), None)
        label = f"{guest_name} (late arrival)" if npc_id == late_arrival_id else guest_name
        if record is None:
            text = f"{label}: no grounded attendee tie surfaced."
            support_key = npc_id
        else:
            other_ids = [
                other_id for other_id in record.involved_npc_ids if other_id != npc_id and other_id in attendee_ids
            ][:2]
            other_names = [name_by_id[other_id] for other_id in other_ids]
            text = (
                f"{label}: watch {', '.join(other_names)}; {record.text}"
                if other_names
                else f"{label}: {record.text}"
            )
            support_key = record.item_id
        guest_rows.append((npc_id, text, support_key))
    if tuple(row[0] for row in guest_rows) != attendee_order or len({row[0] for row in guest_rows}) != len(guest_rows):
        raise ClubGenerationError("Guest presentation IDs must match canonical attendees in order.")

    room_parts: list[_RoomPresentationPart] = [
        _RoomPresentationPart(f"{len(attendee_order)} attendees are present.", attendee_order)
    ]
    groups = skeleton.get("room_groups")
    if isinstance(groups, (list, tuple)):
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("npc_ids"), (list, tuple)):
                continue
            normalized_group_ids = tuple(_normalized_string(value) for value in group["npc_ids"])
            if (
                not normalized_group_ids
                or any(npc_id is None or npc_id not in attendee_ids for npc_id in normalized_group_ids)
                or len(set(normalized_group_ids)) != len(normalized_group_ids)
            ):
                continue
            group_ids = tuple(npc_id for npc_id in normalized_group_ids if npc_id is not None)
            room_parts.append(
                _RoomPresentationPart(_format_room_group([name_by_id[npc_id] for npc_id in group_ids]), group_ids)
            )
    if late_arrival_id in attendee_ids:
        room_parts.append(_RoomPresentationPart(f"{name_by_id[late_arrival_id]} is the late arrival.", late_arrival_id))
    if top:
        room_parts.append(_RoomPresentationPart(top[0].text, top[0].item_id))

    return _DashboardPresentation(
        hot_connections=top,
        possible_pressure=pressure,
        rumors=rumors,
        guest_brief=tuple(row[1] for row in guest_rows),
        guest_support_keys=tuple(row[2] for row in guest_rows),
        room_parts=tuple(room_parts),
        diagnostics=diagnostics,
    )


def dashboard_from_skeleton(skeleton: dict[str, Any], *, first_impression: str | None = None) -> dict[str, Any]:
    attendees = skeleton.get("attendees") if isinstance(skeleton.get("attendees"), list) else []
    event = dict(skeleton.get("event") or {})
    venue = str(event.get("venue") or "The Lantern Room")
    names = ", ".join(clean_display_text(str(item.get("display_name") or "")) for item in attendees[:4] if isinstance(item, dict))
    if len(attendees) > 4:
        names = f"{names}, and {len(attendees) - 4} others"
    impression = first_impression or (
        f"{venue} is already crowded when the group arrives; {names or 'the regulars'} draw the eye in separate knots of conversation."
    )
    presentation = _compose_dashboard_presentation(skeleton)
    social_map = list(skeleton.get("room_groups") or skeleton.get("social_map") or [])
    return {
        "event": {
            "venue": venue,
            "host": str(event.get("host") or ""),
            "event_type": str(event.get("event_type") or "social gathering"),
            "mood": str(event.get("mood") or "tense"),
            "unusual_circumstance": str(event.get("unusual_circumstance") or ""),
        },
        "first_impression": impression,
        "room_situation": presentation.room_situation,
        "hot_connections": [record.text for record in presentation.hot_connections],
        "possible_pressure": [record.text for record in presentation.possible_pressure],
        "rumors_in_circulation": [record.text for record in presentation.rumors],
        "rumor_guidance": [],
        "guest_brief": list(presentation.guest_brief),
        "social_map": social_map,
        "notable_details": list(skeleton.get("notable_details") or []),
        "rumors": [record.support for record in presentation.rumors],
        "rumor_selection": dict(skeleton.get("rumor_selection") or {}),
        "possible_drama": [record.support for record in presentation.possible_pressure],
        "interesting_connections": list(skeleton.get("interesting_connections") or []),
        "unresolved_business": list(skeleton.get("unresolved_business") or []),
        "opportunities": list(skeleton.get("opportunities") or []),
        "top_connections": [record.support for record in presentation.hot_connections],
        "background_details": list(skeleton.get("background_details") or []),
        "late_arrival_id": str(event.get("late_arrival_id") or ""),
        "skeleton": {
            "schema_version": skeleton.get("schema_version"),
            "source_ids": list(skeleton.get("source_ids") or []),
        },
    }


def npc_panel_from_skeleton(skeleton: dict[str, Any]) -> dict[str, Any]:
    return _compose_npc_panel_factual_fields(
        {
            "tonight": dict(skeleton.get("tonight") or {}),
        },
        skeleton,
    )


def _panel_presentation_entry(item: Any, *, text: str = "") -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    return {
        "item_id": str(item.get("item_id") or ""),
        "text": _sentence(text or str(item.get("prep_text") or item.get("summary") or "")),
    }


def _deterministic_panel_presentation(skeleton: dict[str, Any]) -> dict[str, Any]:
    conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    basis = skeleton.get("current_read_basis") if isinstance(skeleton.get("current_read_basis"), dict) else {}
    demeanor = basis.get("demeanor") if isinstance(basis.get("demeanor"), dict) else {}
    demeanor_value = clean_display_text(str(demeanor.get("value") or ""))
    personality = [clean_display_text(str(value)) for value in skeleton.get("personality") or [] if clean_display_text(str(value))]
    play_cue = ""
    if demeanor_value:
        play_cue = _sentence(f"Keep the delivery {demeanor_value.lower()}")
    elif personality:
        play_cue = _sentence(f"Play them as {', '.join(personality[:3])}")

    def entries(items: Sequence[Any], *, use_summary: bool = False) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("summary") if use_summary else item.get("prep_text") or item.get("summary") or "")
            entry = _panel_presentation_entry(item, text=text)
            if entry is not None:
                rows.append(entry)
        return rows

    focus = tonight.get("current_desire")
    hook = skeleton.get("interesting_detail")
    return {
        "play_cue": play_cue,
        "agenda": _panel_presentation_entry(focus, text=str(focus.get("summary") or "")) if isinstance(focus, dict) else None,
        "people_here": entries(skeleton.get("people_here") or []),
        "if_approached": entries(conversation.get("likely_subjects") or [], use_summary=True),
        "keep_guarded": entries(conversation.get("sensitive") or [], use_summary=True),
        "hook": _panel_presentation_entry(hook) if isinstance(hook, dict) else None,
    }


def _npc_panel_sourced_demeanor(skeleton: dict[str, Any]) -> str:
    basis = skeleton.get("current_read_basis") if isinstance(skeleton.get("current_read_basis"), dict) else {}
    demeanor = basis.get("demeanor") if isinstance(basis.get("demeanor"), dict) else {}
    value = clean_display_text(str(demeanor.get("value") or ""))
    has_source = any(
        isinstance(source, dict) and clean_display_text(str(source.get("source_id") or ""))
        for source in demeanor.get("sources") or []
    )
    return value if value and has_source else ""


def _npc_panel_has_portrayal_basis(skeleton: dict[str, Any]) -> bool:
    has_sourced_demeanor = bool(_npc_panel_sourced_demeanor(skeleton))
    has_personality = any(clean_display_text(str(value)) for value in skeleton.get("personality") or [])
    return has_sourced_demeanor or has_personality


def _validated_panel_presentation(data: Any, skeleton: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ClubGenerationError("NPC panel presentation response must be a JSON object")
    if set(data) != {"npc_id", "presentation"}:
        raise ClubGenerationError("NPC panel presentation response must contain only npc_id and presentation")
    if str(data.get("npc_id") or "") != str(skeleton.get("npc_id") or ""):
        raise ClubGenerationError("npc_id must match the clicked NPC")
    presentation = data.get("presentation")
    expected_keys = {"play_cue", "agenda", "people_here", "if_approached", "keep_guarded", "hook"}
    if not isinstance(presentation, dict) or set(presentation) != expected_keys:
        raise ClubGenerationError("presentation must contain exactly play_cue, agenda, people_here, if_approached, keep_guarded, and hook")

    errors: list[str] = []

    def clean_text(value: Any, field: str, *, required: bool) -> str:
        if not isinstance(value, str):
            errors.append(f"{field}.text must be a string" if field != "play_cue" else "play_cue must be a string")
            return ""
        text = clean_display_text(value)
        if required and not text:
            errors.append(f"{field} must be non-empty" if field == "play_cue" else f"{field}.text must be non-empty")
        if len(text) > NPC_PANEL_PRESENTATION_TEXT_LIMIT:
            errors.append(f"{field} text must be at most {NPC_PANEL_PRESENTATION_TEXT_LIMIT} characters")
        if text and _looks_like_extraction_record(text):
            errors.append(f"{field} must be GM-facing prose, not serialized extraction output")
        return text

    has_portrayal_basis = _npc_panel_has_portrayal_basis(skeleton)
    play_cue = clean_text(presentation.get("play_cue"), "play_cue", required=has_portrayal_basis)
    if play_cue and not has_portrayal_basis:
        errors.append("play_cue must be empty when the skeleton has no demeanor or personality basis")
    conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}

    def validate_entry(actual: Any, expected: Any, field: str) -> dict[str, str] | None:
        if expected is None:
            if actual is not None:
                errors.append(f"{field} must be null when its deterministic support is absent")
            return None
        if not isinstance(actual, dict) or set(actual) != {"item_id", "text"}:
            errors.append(f"{field} must contain exactly item_id and text")
            return None
        expected_id = str(expected.get("item_id") or "")
        if str(actual.get("item_id") or "") != expected_id:
            errors.append(f"{field}.item_id must match deterministic support")
        return {"item_id": expected_id, "text": clean_text(actual.get("text"), field, required=True)}

    def validate_entries(actual: Any, expected: Sequence[Any], field: str) -> list[dict[str, str]]:
        if not isinstance(actual, list):
            errors.append(f"{field} must be a list")
            return []
        if len(actual) != len(expected):
            errors.append(f"{field} must map every deterministic support item exactly once and in order")
        rows: list[dict[str, str]] = []
        for index, expected_item in enumerate(expected):
            actual_item = actual[index] if index < len(actual) else None
            entry = validate_entry(actual_item, expected_item, f"{field}[{index}]")
            if entry is not None:
                rows.append(entry)
        return rows

    cleaned = {
        "play_cue": play_cue,
        "agenda": validate_entry(presentation.get("agenda"), tonight.get("current_desire"), "agenda"),
        "people_here": validate_entries(presentation.get("people_here"), skeleton.get("people_here") or [], "people_here"),
        "if_approached": validate_entries(presentation.get("if_approached"), conversation.get("likely_subjects") or [], "if_approached"),
        "keep_guarded": validate_entries(presentation.get("keep_guarded"), conversation.get("sensitive") or [], "keep_guarded"),
        "hook": validate_entry(presentation.get("hook"), skeleton.get("interesting_detail"), "hook"),
    }
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return cleaned


def postprocess_npc_presentation(data: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    presentation = _validated_panel_presentation(data, skeleton)
    panel = npc_panel_from_skeleton(skeleton)
    panel["presentation"] = presentation
    tonight = dict(panel.get("tonight") or {})
    tonight["current_demeanor"] = presentation["play_cue"]
    panel["tonight"] = tonight
    panel["likely_conversation"] = [entry["text"] for entry in presentation["if_approached"]]
    panel["sensitive_subjects"] = [entry["text"] for entry in presentation["keep_guarded"]]
    panel["useful_hook"] = presentation["hook"]["text"] if presentation["hook"] else ""
    return validate_ai_npc_panel(
        panel,
        source_ids=set(skeleton.get("source_ids") or []),
        npc_id=str(skeleton.get("npc_id") or ""),
        skeleton=skeleton,
    )


def _copy_panel_support(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    support = deepcopy(item)
    support["summary"] = clean_display_text(str(item.get("summary") or ""))
    support["sources"] = _dedupe_sources_in_order(item.get("sources") or [])
    support["characters"] = [str(value) for value in item.get("characters") or []]
    support["mentioned_npc_ids"] = [str(value) for value in item.get("mentioned_npc_ids") or []]
    support["link_targets"] = [str(value) for value in item.get("link_targets") or []]
    support["prep_text"] = _sentence(str(item.get("prep_text") or support["summary"]))
    return support


def _focus_category(item: dict[str, Any], npc_id: str) -> str:
    if _is_explicit_self_focus(item, npc_id):
        return "goal"
    if str(item.get("type") or "").casefold() != "rumor" and _is_panel_sensitive(item):
        return "pressure"
    if (
        str(item.get("source_npc_id") or "") == npc_id
        and str(item.get("fact_scope") or "") == "relationship"
        and item.get("target_npc_id")
    ):
        return "relationship"
    return "concern"


def _compose_npc_panel_factual_fields(panel: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    processed = dict(panel)
    npc_id = str(skeleton.get("npc_id") or "")
    skeleton_conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    people_here = [
        support
        for item in skeleton.get("people_here") or []
        if (support := _copy_panel_support(item)) is not None
    ]
    likely_subjects = [
        support
        for item in skeleton_conversation.get("likely_subjects") or []
        if (support := _copy_panel_support(item)) is not None
    ]
    sensitive = [
        support
        for item in skeleton_conversation.get("sensitive") or []
        if (support := _copy_panel_support(item)) is not None
    ]
    skeleton_tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    tonight = dict(processed.get("tonight") or {})
    presentation = (
        deepcopy(processed.get("presentation"))
        if isinstance(processed.get("presentation"), dict)
        else _deterministic_panel_presentation(skeleton)
    )
    focus_basis = skeleton_tonight.get("current_desire")
    tonight["current_desire"] = (
        _rebuild_focus_support(focus_basis, _focus_category(focus_basis, npc_id))
        if isinstance(focus_basis, dict)
        else None
    )
    tonight["current_demeanor"] = clean_display_text(
        str(presentation.get("play_cue") or tonight.get("current_demeanor") or skeleton_tonight.get("current_demeanor") or "")
    )
    interesting = _copy_panel_support(skeleton.get("interesting_detail"))
    processed.update({
        "npc_id": str(skeleton.get("npc_id") or ""),
        "name": clean_display_text(str(skeleton.get("name") or skeleton.get("npc_id") or "")),
        "identity": dict(skeleton.get("identity") or {}),
        "personality": list(skeleton.get("personality") or []),
        "tonight": tonight,
        "current_read": str(skeleton.get("current_read") or _fallback_current_read(skeleton)),
        "current_read_basis": deepcopy(skeleton.get("current_read_basis")),
        "likely_conversation": [str(item.get("text") or "") for item in presentation.get("if_approached") or []],
        "sensitive_subjects": [str(item.get("text") or "") for item in presentation.get("keep_guarded") or []],
        "useful_hook": str((presentation.get("hook") or {}).get("text") or ""),
        "presentation": presentation,
        "people_here": people_here,
        "conversation": {"likely_subjects": likely_subjects, "sensitive": sensitive},
        "interesting_detail": interesting,
        "interesting_detail_sources": list(interesting.get("sources") or []) if interesting else [],
        "presentation_suppressed": list(skeleton.get("presentation_suppressed") or []),
        "empty_state": str(skeleton.get("empty_state") or ""),
        "skeleton": {
            "schema_version": skeleton.get("schema_version"),
            "source_ids": list(skeleton.get("source_ids") or []),
        },
    })
    return processed


def _current_read_basis(index: dict[str, Any]) -> dict[str, Any] | None:
    demeanor = _validated_attribute_payload(index.get("demeanor"))
    if demeanor is None:
        return None
    pronouns = _validated_attribute_payload(index.get("pronouns"))
    if pronouns is not None and str(pronouns.get("value") or "") not in PRONOUN_SUBJECTS:
        pronouns = None
    basis = {"demeanor": demeanor}
    if pronouns is not None:
        basis["pronouns"] = pronouns
    return basis


def _validated_attribute_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized = clean_display_text(str(value.get("value") or ""))
    sources = _dedupe_sources_in_order(value.get("sources") or [])
    if not normalized or not sources:
        return None
    return {
        "value": normalized,
        "sources": sources,
    }


def _fallback_current_read(skeleton: dict[str, Any]) -> str:
    name = clean_display_text(str(skeleton.get("name") or "This NPC"))
    basis = skeleton.get("current_read_basis") if isinstance(skeleton.get("current_read_basis"), dict) else {}
    demeanor = basis.get("demeanor") if isinstance(basis.get("demeanor"), dict) else {}
    demeanor_value = clean_display_text(str(demeanor.get("value") or ""))
    if demeanor_value:
        pronouns = basis.get("pronouns") if isinstance(basis.get("pronouns"), dict) else {}
        subject = PRONOUN_SUBJECTS.get(str(pronouns.get("value") or ""), name)
        return _sentence(f"{subject}: {demeanor_value}")
    return _sentence(f"{name} has limited attendee-specific prep in the current indexes")


def _fallback_room_situation(
    *,
    impression: str,
    social_map: Sequence[Any],
    attendees: Sequence[str],
    late_arrival_id: str,
    attendee_lookup: dict[str, str],
) -> str:
    late_arrival = attendee_lookup.get(late_arrival_id, late_arrival_id)
    map_text = ""
    for item in social_map:
        if isinstance(item, dict) and item.get("summary"):
            map_text = clean_display_text(str(item["summary"]))
            break
    details = [clean_display_text(impression)]
    if map_text:
        details.append(map_text)
    if late_arrival:
        details.append(f"{late_arrival} is the late arrival.")
    if not details and attendees:
        details.append(f"{', '.join(attendees[:4])} are in the room.")
    return " ".join(detail for detail in details if detail)


def _fallback_guest_brief(attendees: Sequence[Any], *, social_map: Sequence[Any], visible_items: Sequence[Any] = ()) -> list[str]:
    rows: list[str] = []
    name_by_id = {
        str(item.get("npc_id") or ""): clean_display_text(str(item.get("display_name") or item.get("name") or item.get("npc_id") or ""))
        for item in attendees
        if isinstance(item, dict)
    }
    best_item_by_id: dict[str, dict[str, Any]] = {}
    for item in visible_items:
        if not isinstance(item, dict):
            continue
        for npc_id in item.get("characters") or []:
            npc_key = str(npc_id)
            if npc_key and npc_key not in best_item_by_id:
                best_item_by_id[npc_key] = item
    for item in attendees:
        if not isinstance(item, dict):
            continue
        name = clean_display_text(str(item.get("display_name") or item.get("name") or item.get("npc_id") or ""))
        if not name:
            continue
        npc_id = str(item.get("npc_id") or "")
        visible_item = best_item_by_id.get(npc_id)
        if visible_item:
            other_names = _brief_other_names(npc_id, visible_item, name_by_id)
            cue = f"watch {other_names}; " if other_names else ""
            rows.append(_sentence(f"{name}: {cue}{_display_text_for_render(visible_item)}"))
        else:
            rows.append(f"{name}: no grounded attendee tie surfaced.")
    return rows


def _brief_other_names(npc_id: str, item: dict[str, Any], name_by_id: dict[str, str]) -> str:
    characters = [str(character) for character in item.get("characters") or [] if str(character)]
    others = [character for character in characters if character != npc_id]
    if not others:
        return ""
    names = [name_by_id.get(other, other) for other in others if name_by_id.get(other, other)]
    return ", ".join(names[:2])


def _display_text_for_render(item: Any) -> str:
    if isinstance(item, dict):
        return _sentence(str(item.get("prep_text") or item.get("summary") or ""))
    return _sentence(str(item or ""))


def _prioritized_prompt_items(items: Sequence[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return sorted(
        list(items),
        key=lambda item: (
            -_prompt_item_score(item),
            _normalized_display_key(str(item.get("summary") or "")),
            _first_source_id(item),
            "/".join(str(character) for character in item.get("characters") or []),
        ),
    )[:limit]


def _prompt_item_score(item: dict[str, Any]) -> int:
    fact_type = str(item.get("type") or "").lower()
    character_bonus = 2 if len(item.get("characters") or []) > 1 else 0
    source_bonus = min(len(item.get("sources") or []), 3)
    return _dramatic_item_score(item) + character_bonus + source_bonus


def _dramatic_item_score(item: dict[str, Any]) -> int:
    fact_type = str(item.get("type") or "").lower()
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    score = FACT_TYPE_WEIGHTS.get(fact_type, 1)
    for key, weight in DRAMA_TYPE_WEIGHTS.items():
        if key in fact_type or key in summary:
            score = max(score, weight)
    return score


def _is_attendee_facing_fact(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    fact_scope = str(item.get("fact_scope") or "")
    if fact_scope == "relationship":
        return True
    return bool(item.get("mentioned_npc_ids"))


def _is_dashboard_candidate_fact(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if _is_attendee_facing_fact(item):
        return True
    return str(item.get("type") or "").lower() == "rumor" and bool(str(item.get("source_npc_id") or "").strip())


def is_dashboard_pressure(item: dict[str, Any]) -> bool:
    return _dashboard_break_reason(item) is not None


def _is_panel_sensitive(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if _dashboard_break_reason(item) is not None:
        return True
    fact_type = str(item.get("type") or "").lower()
    display_label = str(item.get("display_label") or "").lower()
    if fact_type in {
        "ally",
        "business",
        "contact",
        "distrust",
        "enemy",
        "grievance",
        "hostile",
        "hostility",
        "opposition",
        "patron",
        "political opposition",
        "relationship",
        "rival",
        "rivalry",
    }:
        return False
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    haystack = " ".join([fact_type, display_label, summary])
    return _contains_any(haystack, PRESSURE_KEYWORDS)


def _dashboard_break_reason(item: dict[str, Any]) -> str | None:
    if not _is_attendee_facing_fact(item):
        return None
    fact_type = str(item.get("type") or "").lower()
    display_label = str(item.get("display_label") or "").lower()
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    if not summary:
        return None
    haystack = " ".join([fact_type, display_label, summary])
    if _contains_any(haystack, ("grave misconduct",)):
        return "grave misconduct could be exposed or force a confrontation tonight"
    if _contains_any(haystack, ("coercive bond", "coercively bound", "blood bound", "compulsion")):
        return "a coercive bond or compulsion could be exposed or leveraged tonight"
    if _contains_any(haystack, ("blackmail", "blackmail material")):
        if "secret private" in summary:
            return "blackmail material around secret private could be exposed or leveraged tonight"
        return "blackmail material could be exposed or leveraged tonight"
    if _contains_any(haystack, ("coerc", "manipulat", "using", "toy", "test subject", "studying", "exploit")):
        return "manipulation or exploitation could be exposed or resisted tonight"
    if _contains_any(haystack, ("betray", "traitor")):
        return "betrayal could surface or trigger confrontation tonight"
    if _contains_any(haystack, ("scandal", "expose", "exposure")):
        return "a scandal could be exposed or used as leverage tonight"
    if _contains_any(haystack, ("debt", "favor", "owes", "owed", "favor", "leverage")):
        return "a debt or favor could be called in as leverage tonight"
    if _contains_any(haystack, ("dependen",)):
        return "dependency could be exploited or tested tonight"
    if _contains_any(haystack, ("danger", "death threat", "threaten")):
        return "a threat or danger could surface tonight"
    conflict_labels = ("enemy", "rival", "rivalry", "hostile", "hostility", "distrust", "grievance")
    active_conflict = (
        "attack",
        "bitter",
        "confront",
        "feud",
        "fears",
        "hostil",
        "kill",
        "resent",
        "revenge",
        "sabotage",
        "suspects",
        "undermine",
        "warn",
    )
    if _contains_any(haystack, conflict_labels) and _contains_any(summary, active_conflict):
        return "active conflict could snap into confrontation tonight"
    if _contains_any(haystack, ("paramour", "lover", "intimacy")) and _contains_any(summary, ("secret", "scandal", "blood", "expose", "leverage")):
        return "intimacy could be exposed or exploited tonight"
    return None


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    return any(needle in text for needle in needles)


def _is_actionable_opportunity(item: dict[str, Any]) -> bool:
    if not _is_attendee_facing_fact(item):
        return False
    fact_type = str(item.get("type") or "").lower()
    if fact_type not in OPPORTUNITY_TYPES:
        return False
    if not item.get("mentioned_npc_ids") and not item.get("target_npc_id"):
        return False
    haystack = clean_display_text(str(item.get("summary") or "")).lower()
    return any(keyword in haystack for keyword in OPPORTUNITY_ACTION_KEYWORDS)


def _rank_panel_hook_items(facts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    unresolved = _prioritized_prompt_items(
        [item for item in facts if str(item.get("type") or "").lower() == "unresolved_business"],
        limit=12,
    )
    story_hooks = _prioritized_prompt_items(
        [item for item in facts if str(item.get("type") or "").lower() == "story_hook"],
        limit=12,
    )
    return dedupe_grounded_facts([*story_hooks, *unresolved])


def _select_panel_hook_items(
    candidates: Sequence[dict[str, Any]],
    occupied: Sequence[dict[str, Any]] | dict[tuple[str, ...], str],
    *,
    identity_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(occupied, dict):
        occupied_fact_ids = dict(occupied)
    else:
        occupied_fact_ids = {
            _canonical_fact_identity(item): "sensitive"
            for item in occupied
            if isinstance(item, dict)
        }
    suppressed: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        cloned = _clone_grounded_item(candidate, "interesting_detail", identity_by_id=identity_by_id)
        identity = _canonical_fact_identity(cloned)
        if identity in occupied_fact_ids:
            suppressed.append(
                {
                    "reason": f"suppressed_duplicate_of_{occupied_fact_ids[identity]}",
                    "slot": "interesting_detail",
                    "item": cloned,
                }
            )
            continue
        return [cloned], suppressed
    return [], suppressed


def _skeleton_fact_count(skeleton: dict[str, Any]) -> int:
    return len(_skeleton_grounded_items(skeleton))


def _skeleton_grounded_item_count(skeleton: dict[str, Any]) -> int:
    return sum(1 for item in _skeleton_grounded_items(skeleton) if item.get("sources"))


def _skeleton_grounded_items(skeleton: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(skeleton, dict):
        return []
    items: list[dict[str, Any]] = []
    for field in (
        "rumors",
        "rumor_pool",
        "possible_drama",
        "interesting_connections",
        "unresolved_business",
        "opportunities",
        "top_connections",
        "people_here",
    ):
        for item in skeleton.get(field) or []:
            if isinstance(item, dict):
                items.append(item)
    conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    for field in ("likely_subjects", "sensitive"):
        for item in conversation.get(field) or []:
            if isinstance(item, dict):
                items.append(item)
    tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    if isinstance(tonight.get("current_desire"), dict):
        items.append(tonight["current_desire"])
    if isinstance(skeleton.get("interesting_detail"), dict):
        items.append(skeleton["interesting_detail"])
    return items


def _is_sensitive_debug_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_DEBUG_KEY_EXCEPTIONS:
        return False
    return any(part in lowered for part in SENSITIVE_DEBUG_KEY_PARTS)


def _generation_metadata(
    generation_mode: str,
    *,
    model_name: str,
    from_cache: bool,
    validation_error: str | None = None,
    fallback_reason: str | None = None,
    ai_attempted: bool | None = None,
    fallback_used: bool | None = None,
    ai_error_type: str | None = None,
    ai_error_stage: str | None = None,
    prompt_too_large: bool = False,
    estimated_input_tokens: int = 0,
    prompt_budget: int = 0,
    skeleton: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skeleton = skeleton if isinstance(skeleton, dict) else {}
    if ai_attempted is None:
        ai_attempted = generation_mode == "ai"
    if fallback_used is None:
        fallback_used = generation_mode == "deterministic_fallback"
    return _normalize_generation_metadata({
        "generation_mode": generation_mode,
        "model_name": model_name,
        "from_cache": from_cache,
        "generated_at": int(time.time()),
        "ai_attempted": bool(ai_attempted),
        "fallback_used": bool(fallback_used),
        "ai_error_type": ai_error_type,
        "ai_error_stage": ai_error_stage,
        "validation_error": _sanitize_validation_error(validation_error) if validation_error else None,
        "fallback_reason": fallback_reason or None,
        "prompt_too_large": prompt_too_large,
        "estimated_input_tokens": estimated_input_tokens,
        "prompt_budget": prompt_budget,
        "from_skeleton": bool(skeleton),
        "skeleton_fact_count": _skeleton_fact_count(skeleton),
        "grounded_item_count": _skeleton_grounded_item_count(skeleton),
        "dropped_item_count": int(skeleton.get("dropped_item_count") or 0) if isinstance(skeleton, dict) else 0,
    })


def _normalize_generation_metadata(metadata: Any, *, default_mode: str = "ai") -> dict[str, Any]:
    result = dict(metadata) if isinstance(metadata, dict) else {}
    generation_mode = str(result.get("generation_mode") or default_mode)
    result["generation_mode"] = generation_mode
    result["from_cache"] = bool(result.get("from_cache", False))
    if "ai_attempted" not in result:
        result["ai_attempted"] = bool(generation_mode == "ai" and not result["from_cache"])
    else:
        result["ai_attempted"] = bool(result["ai_attempted"])
    if "fallback_used" not in result:
        result["fallback_used"] = generation_mode == "deterministic_fallback"
    else:
        result["fallback_used"] = bool(result["fallback_used"])
    result["ai_error_type"] = str(result["ai_error_type"]) if result.get("ai_error_type") else None
    result["ai_error_stage"] = str(result["ai_error_stage"]) if result.get("ai_error_stage") else None
    result["validation_error"] = _sanitize_validation_error(result.get("validation_error")) if result.get("validation_error") else None
    result["fallback_reason"] = str(result["fallback_reason"]) if result.get("fallback_reason") else None
    return result


def _ai_failure_details(exc: BaseException) -> dict[str, Any]:
    auth = authentication_failure(exc)
    if auth == "missing_credentials":
        return _failure_metadata("provider_missing_credentials", "provider_config", ai_attempted=False)
    if auth:
        return _failure_metadata("provider_authentication_failed", "provider_request")
    if isinstance(exc, ClubPromptTooLargeError):
        return _failure_metadata("prompt_too_large", "prompt_budget")
    if _exception_chain_has_name(exc, "Timeout") or _message_has_all(exc, ("timed out",)):
        return _failure_metadata("provider_timeout", "provider_request")
    if _message_has_all(exc, ("api key", "not provided")) or _message_has_all(exc, ("missing", "credential")):
        return _failure_metadata("provider_missing_credentials", "provider_config", ai_attempted=False)
    if _looks_like_missing_message_content(exc):
        return _failure_metadata("provider_malformed_response", "provider_response")
    if isinstance(exc, ClubGenerationError):
        return _failure_metadata("ai_output_validation_failed", "ai_output_validation")
    if _exception_chain_has_name(exc, "HTTPError") or _message_has_all(exc, ("request failed",)):
        return _failure_metadata("provider_request_failed", "provider_request")
    return _failure_metadata("unknown_ai_error", "ai_generation")


def _failure_metadata(error_type: str, error_stage: str, *, ai_attempted: bool = True) -> dict[str, Any]:
    return {
        "ai_attempted": ai_attempted,
        "ai_error_type": error_type,
        "ai_error_stage": error_stage,
    }


def _exception_chain_has_name(exc: BaseException, name_part: str) -> bool:
    lowered = name_part.lower()
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if lowered in current.__class__.__name__.lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _message_has_all(exc: BaseException, needles: Sequence[str]) -> bool:
    text = str(exc).lower()
    return all(needle.lower() in text for needle in needles)


def _looks_like_missing_message_content(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "choices" in text
        and "message" in text
        and "content" in text
        and ("non-empty" in text or "empty" in text or "missing" in text or "did not include" in text)
    )


def _metadata_mode(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return "ai"
    return str(metadata.get("generation_mode") or "ai")


def _event_from_cached(cached: Any, *, cache_key: str, default_mode: str = "ai") -> ClubEvent | None:
    if not isinstance(cached, dict) or cached.get("cache_key") != cache_key:
        return None
    if not isinstance(cached.get("dashboard"), dict):
        return None
    metadata = dict(cached.get("metadata") or {})
    if not metadata:
        metadata = _generation_metadata(default_mode, model_name="", from_cache=False, ai_attempted=False, fallback_used=False)
    else:
        metadata = _normalize_generation_metadata(metadata, default_mode=default_mode)
    return ClubEvent(
        event_id=str(cached["event_id"]),
        attendee_ids=tuple(cached["attendee_ids"]),
        late_arrival_id=str(cached["late_arrival_id"]),
        dashboard=cached["dashboard"],
        cache_key=cache_key,
        seed=int(cached["seed"]),
        metadata=metadata,
    )


def _with_event_metadata(event: ClubEvent, updates: dict[str, Any]) -> ClubEvent:
    metadata = _normalize_generation_metadata({**dict(event.metadata), **updates}, default_mode=_metadata_mode(event.metadata))
    return ClubEvent(
        event_id=event.event_id,
        attendee_ids=event.attendee_ids,
        late_arrival_id=event.late_arrival_id,
        dashboard=event.dashboard,
        cache_key=event.cache_key,
        seed=event.seed,
        metadata=metadata,
    )


def _panel_from_cached(cached: Any, *, cache_key: str, default_mode: str = "ai") -> dict[str, Any] | None:
    if not isinstance(cached, dict) or cached.get("cache_key") != cache_key:
        return None
    panel = cached.get("panel")
    if not isinstance(panel, dict):
        return None
    metadata = dict(panel.get("metadata") or cached.get("metadata") or {})
    if not metadata:
        metadata = _generation_metadata(default_mode, model_name="", from_cache=False, ai_attempted=False, fallback_used=False)
    else:
        metadata = _normalize_generation_metadata(metadata, default_mode=default_mode)
    return _with_panel_metadata(panel, metadata)


def _with_panel_metadata(panel: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = dict(panel)
    default_mode = _metadata_mode(result.get("metadata"))
    result["metadata"] = _normalize_generation_metadata({**dict(result.get("metadata") or {}), **updates}, default_mode=default_mode)
    return result


def relationship_digest_for(npc_id: str, relationships: Sequence[dict[str, Any]]) -> str:
    relevant = [
        relationship
        for relationship in relationships
        if npc_id in set(relationship.get("characters") or [])
    ]
    return stable_hash(relevant)


def select_late_arrival(identities: Sequence[NpcIdentity], *, host_path: str | None = None, seed: int | None = None) -> str:
    if not identities:
        return ""
    host_stem = Path(host_path).stem.lower() if host_path else ""
    eligible = [identity for identity in identities if identity.display_name.lower() != host_stem]
    pool = eligible or list(identities)
    rng = random.Random(seed)
    return rng.choice(pool).npc_id


def default_rumor_limit(attendee_count: int) -> int:
    if attendee_count <= 4:
        return 3
    if attendee_count >= 11:
        return 7
    return 5


def normalize_rumor_limit(value: int | str | None, *, attendee_count: int) -> int:
    if value is None or value == "":
        return default_rumor_limit(attendee_count)
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ClubGenerationError("Rumors shown must be 3, 5, or 7.") from exc
    if limit not in RUMOR_LIMIT_OPTIONS:
        raise ClubGenerationError("Rumors shown must be 3, 5, or 7.")
    return limit


def deterministic_dashboard(
    context: dict[str, Any],
    *,
    venue: str,
    event_type: str,
    late_arrival_id: str,
    rumor_limit: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    identities = context.get("identities") or []
    relationships = dedupe_grounded_facts(context.get("attendee_relationships") or [])
    facts = dedupe_grounded_facts(context.get("attendee_facts") or relationships)
    identity_by_id = {str(item.get("npc_id")): item for item in identities if isinstance(item, dict)}
    social_map = cluster_social_map(context.get("attendee_ids") or [], relationships)
    attendee_count = len(identities)
    first_names = ", ".join(clean_display_text(str(item.get("display_name") or item.get("name") or "")) for item in identities[:4])
    if attendee_count > 4:
        first_names = f"{first_names}, and {attendee_count - 4} others"
    first_impression = (
        f"{venue} is already crowded when the group arrives; {first_names or 'the regulars'} "
        "draw the eye in separate knots of conversation."
    )
    dashboard_facts = [item for item in facts if _is_dashboard_candidate_fact(item)]
    curated = _curate_dashboard_visible_sections(
        relationships=relationships,
        dashboard_facts=dashboard_facts,
        ranked_connections=rank_connections(relationships, identity_by_id=identity_by_id),
        rumor_limit=normalize_rumor_limit(rumor_limit, attendee_count=attendee_count),
        seed=seed,
    )
    return validate_dashboard(
        {
            "event": {
                "venue": venue,
                "host": "",
                "event_type": event_type,
                "mood": "tense",
                "unusual_circumstance": "",
            },
            "first_impression": first_impression,
            "social_map": social_map,
            "notable_details": [
                f"{attendee_count} attendees are in the room.",
                f"{len(relationships)} attendee-to-attendee connection{'s' if len(relationships) != 1 else ''} are grounded in the indexes.",
            ],
            "rumors_in_circulation": [_display_text_for_render(item) for item in curated["rumors"]],
            "rumors": curated["rumors"],
            "rumor_selection": {
                "limit": normalize_rumor_limit(rumor_limit, attendee_count=attendee_count),
                "selected_count": len(curated["rumors"]),
                "grounded_count": len(curated["rumor_pool"]),
            },
            "possible_drama": curated["possible_drama"],
            "interesting_connections": curated["interesting_connections"],
            "unresolved_business": curated["unresolved_business"],
            "opportunities": curated["opportunities"],
            "top_connections": curated["top_connections"],
            "background_details": [],
            "late_arrival_id": late_arrival_id,
        },
        source_ids=set(context.get("source_ids") or []),
        attendee_ids=set(context.get("attendee_ids") or []),
    )


def deterministic_npc_panel(context: dict[str, Any], npc_id: str) -> dict[str, Any]:
    identities = {
        str(item.get("npc_id") or ""): item
        for item in context.get("identities") or []
        if isinstance(item, dict)
    }
    indexes = {
        str(item.get("npc_id") or ""): item
        for item in context.get("indexes") or []
        if isinstance(item, dict)
    }
    relationships = [
        item
        for item in context.get("attendee_relationships") or []
        if isinstance(item, dict) and str(item.get("source_npc_id") or "") == npc_id
    ]
    relationship_keys = {grounded_fact_key(item) for item in relationships}
    facts = [
        item
        for item in context.get("attendee_facts") or []
        if isinstance(item, dict)
        and is_relevant_to_clicked_npc(item, npc_id)
        and grounded_fact_key(item) not in relationship_keys
    ]
    source_ids = _source_ids_from_items([*relationships, *facts])
    panel_context = {
        "npc_id": npc_id,
        "identity": identities.get(npc_id, {}),
        "index": indexes.get(npc_id, {}),
        "attendees": list(identities.values()),
        "attendee_relationships": relationships,
        "attendee_facts": facts,
        "source_ids": sorted(source_ids),
        "source_map": _compact_source_map(context.get("source_map") or {}, source_ids),
    }
    skeleton = _build_npc_panel_skeleton_from_context(panel_context, npc_id)
    panel = npc_panel_from_skeleton(skeleton)
    return validate_npc_panel(panel, source_ids=set(skeleton.get("source_ids") or []), npc_id=npc_id, skeleton=skeleton)


def _curate_dashboard_visible_sections(
    *,
    relationships: Sequence[dict[str, Any]],
    dashboard_facts: Sequence[dict[str, Any]],
    ranked_connections: Sequence[dict[str, Any]],
    rumor_limit: int,
    seed: int = 0,
) -> dict[str, list[dict[str, Any]]]:
    pressure_candidates = _prioritized_prompt_items(
        [item for item in dedupe_grounded_facts([*relationships, *dashboard_facts]) if is_dashboard_pressure(item)],
        limit=24,
    )
    possible_drama = _dedupe_visible_items(pressure_candidates)[:6]
    connection_candidates = _dedupe_visible_items([*relationships, *ranked_connections])
    top_connections = _dedupe_visible_items(_suppress_equivalent_items(connection_candidates, possible_drama))[:3]
    interesting_connections = _dedupe_visible_items(_suppress_equivalent_items(connection_candidates, [*possible_drama, *top_connections]))[:8]
    unresolved_business = _dedupe_visible_items(
        _suppress_equivalent_items(
            [item for item in dashboard_facts if str(item.get("type") or "").lower() == "unresolved_business"],
            possible_drama,
        )
    )[:4]
    opportunities = _dedupe_visible_items(
        _suppress_equivalent_items([item for item in dashboard_facts if _is_actionable_opportunity(item)], possible_drama)
    )[:4]
    rumor_pool = [item for item in dashboard_facts if str(item.get("type") or "").lower() == "rumor"]
    rumors, rumor_audit = _select_dashboard_rumors(rumor_pool, limit=rumor_limit, seed=seed)
    return {
        "possible_drama": possible_drama,
        "top_connections": top_connections,
        "interesting_connections": interesting_connections,
        "unresolved_business": unresolved_business,
        "opportunities": opportunities,
        "rumors": rumors,
        "rumor_pool": list(rumor_pool),
        "rumor_selection_audit": rumor_audit,
    }


def build_npc_quick_panel(build_result: ClubBuildResult, npc_id: str) -> dict[str, Any]:
    return npc_panel_from_skeleton(build_npc_panel_skeleton(build_result, npc_id))


def grounded_fact_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    characters = [str(character) for character in item.get("characters") or []]
    subject_id = str(item.get("source_npc_id") or (characters[0] if characters else ""))
    object_id = str(item.get("target_npc_id") or (characters[1] if len(characters) > 1 else ""))
    sources = item.get("sources") or []
    source_id = ""
    if sources and isinstance(sources[0], dict):
        source_id = str(sources[0].get("source_id") or "")
    return (subject_id, object_id, str(item.get("type") or ""), source_id)


def _canonical_fact_identity(item: dict[str, Any]) -> tuple[str, ...]:
    source_npc_id = _normalized_identifier(str(item.get("source_npc_id") or ""))
    primary_source_id = _primary_source_id(item)
    if primary_source_id:
        return ("source_id", source_npc_id, primary_source_id)
    return (
        "fallback",
        source_npc_id,
        _normalized_fact_text(str(item.get("type") or "")),
        *_source_location_for_identity(item),
        _normalized_fact_text(str(item.get("summary") or "")),
    )


def _rumor_selection_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        "rumor_claim",
        *_visible_item_signature(item),
        "topic",
        *_stable_rumor_topic_key(item),
    )


def _primary_source_id(item: dict[str, Any]) -> str:
    for source in item.get("sources") or []:
        if isinstance(source, dict):
            source_id = _normalized_identifier(str(source.get("source_id") or ""))
            if source_id:
                return source_id
    return ""


def _source_location_for_identity(item: dict[str, Any]) -> tuple[str, str, str, str]:
    sources = item.get("sources") or []
    first = sources[0] if sources and isinstance(sources[0], dict) else {}
    return (
        _normalized_identifier(str(first.get("character_id") or "")),
        _normalized_identifier(str(first.get("path") or "")),
        _normalized_identifier(str(first.get("section") or "")),
        _normalized_identifier(str(first.get("excerpt") or "")),
    )


def _normalized_identifier(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _normalized_fact_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _select_dashboard_rumors(items: Sequence[dict[str, Any]], *, limit: int, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked, audit_by_identity = _rank_deduped_rumor_candidates(items, seed=seed)
    selected: list[dict[str, Any]] = []
    selected_identities: set[tuple[Any, ...]] = set()
    source_counts: dict[str, int] = {}
    attendee_counts: dict[str, int] = {}
    topic_counts: dict[tuple[str, str], int] = {}
    passes = (
        ("selected_primary", True, True, True),
        ("selected_after_topic_relaxation", False, True, True),
        ("selected_after_attendee_relaxation", False, False, True),
        ("selected_after_source_relaxation", False, False, False),
    )
    for selection_state, enforce_topic, enforce_attendee, enforce_source in passes:
        if len(selected) >= limit:
            break
        for candidate in ranked:
            if len(selected) >= limit:
                break
            identity = _rumor_selection_identity(candidate)
            if identity in selected_identities:
                continue
            reason = _rumor_diversity_exclusion(
                candidate,
                limit=limit,
                source_counts=source_counts,
                attendee_counts=attendee_counts,
                topic_counts=topic_counts,
                enforce_topic=enforce_topic,
                enforce_attendee=enforce_attendee,
                enforce_source=enforce_source,
            )
            if reason:
                _audit_exclusion(audit_by_identity[identity], reason)
                continue
            selected.append(candidate)
            selected_identities.add(identity)
            _audit_selection(audit_by_identity[identity], selection_state)
            source_id = str(candidate.get("source_npc_id") or "")
            if source_id:
                source_counts[source_id] = source_counts.get(source_id, 0) + 1
            for npc_id in candidate.get("mentioned_npc_ids") or []:
                npc_id = str(npc_id or "")
                if npc_id:
                    attendee_counts[npc_id] = attendee_counts.get(npc_id, 0) + 1
            topic_key = _stable_rumor_topic_key(candidate)
            if topic_key:
                topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
    for candidate in ranked:
        identity = _rumor_selection_identity(candidate)
        entry = audit_by_identity[identity]
        if not entry.get("selection_state"):
            reasons = entry.get("prior_exclusion_reasons") or ["not_selected"]
            entry["selection_state"] = reasons[0]
    return selected, [audit_by_identity[identity] for identity in sorted(audit_by_identity)]


def _rank_deduped_rumor_candidates(items: Sequence[dict[str, Any]], *, seed: int) -> tuple[list[dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]]]:
    winners: dict[tuple[Any, ...], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    audit_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not clean_display_text(str(item.get("summary") or "")):
            continue
        identity = _rumor_selection_identity(item)
        audit_by_identity.setdefault(identity, {"fact": item, "selection_state": "", "prior_exclusion_reasons": []})
        duplicate_key = (
            tuple(-part for part in _rumor_real_score_tuple(item)),
            _normalized_fact_text(str(item.get("summary") or "")),
            _first_source_id(item),
            _stable_rumor_candidate_fingerprint(item),
        )
        if identity in winners:
            _audit_exclusion(audit_by_identity[identity], "excluded_provenance_duplicate")
        if identity not in winners or duplicate_key < winners[identity][0]:
            winners[identity] = (duplicate_key, item)
            audit_by_identity[identity]["fact"] = item
    ranked = [item for _sort_key, item in sorted(winners.values(), key=lambda entry: _rumor_sort_key(entry[1], seed=seed))]
    return ranked, audit_by_identity


def _rumor_sort_key(item: dict[str, Any], *, seed: int) -> tuple[tuple[int, ...], str, tuple[str, ...]]:
    identity = _canonical_fact_identity(item)
    return (
        tuple(-part for part in _rumor_real_score_tuple(item)),
        _seeded_rumor_tie_break(seed, identity),
        identity,
    )


def _seeded_rumor_tie_break(seed: int, identity: tuple[str, ...]) -> str:
    return stable_hash({"seed": int(seed), "canonical_provenance_identity": list(identity)})


def _stable_rumor_candidate_fingerprint(item: dict[str, Any]) -> str:
    return stable_hash(
        {
            "identity": list(_canonical_fact_identity(item)),
            "summary": _normalized_fact_text(str(item.get("summary") or "")),
            "prep_text": _normalized_fact_text(str(item.get("prep_text") or "")),
            "display_label": _normalized_identifier(str(item.get("display_label") or "")),
            "mentioned_npc_ids": [str(npc_id) for npc_id in item.get("mentioned_npc_ids") or []],
            "link_targets": [_normalized_identifier(str(target)) for target in item.get("link_targets") or []],
            "characters": [str(character) for character in item.get("characters") or []],
            "sources": item.get("sources") or [],
        }
    )


def _rumor_real_score_tuple(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    return (
        1 if item.get("mentioned_npc_ids") else 0,
        1 if _contains_any(summary, RUMOR_ACTION_KEYWORDS) or bool(item.get("link_targets")) else 0,
        1 if _is_event_relevant_rumor(item) else 0,
        _dramatic_item_score(item),
        min(len(item.get("sources") or []), 3),
    )


def _is_event_relevant_rumor(item: dict[str, Any]) -> bool:
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    if _contains_any(summary, ("tonight", "club", "bar", "court", "event", "party", "venue", "night market", "red no")):
        return True
    return bool(item.get("link_targets"))


def _rumor_diversity_exclusion(
    item: dict[str, Any],
    *,
    limit: int,
    source_counts: dict[str, int],
    attendee_counts: dict[str, int],
    topic_counts: dict[tuple[str, str], int],
    enforce_topic: bool,
    enforce_attendee: bool,
    enforce_source: bool,
) -> str:
    if enforce_topic:
        topic_key = _stable_rumor_topic_key(item)
        if topic_key and topic_counts.get(topic_key, 0) >= _topic_cap_for_limit(limit):
            return "excluded_topic_cap"
    if enforce_attendee:
        for npc_id in item.get("mentioned_npc_ids") or []:
            if attendee_counts.get(str(npc_id), 0) >= 2:
                return "excluded_attendee_cap"
    if enforce_source:
        source_id = str(item.get("source_npc_id") or "")
        if source_id and source_counts.get(source_id, 0) >= 2:
            return "excluded_source_cap"
    return ""


def _topic_cap_for_limit(limit: int) -> int:
    return 1 if limit <= 3 else 2


def _stable_rumor_topic_key(item: dict[str, Any]) -> tuple[str, ...]:
    for container in [item, *[source for source in item.get("sources") or [] if isinstance(source, dict)]]:
        if not isinstance(container, dict):
            continue
        for key in ("topic_key", "topic_id", "taxonomy", "category", "source_category"):
            value = _normalized_identifier(str(container.get(key) or ""))
            if value:
                return (key, value)
    return ()


def _audit_exclusion(entry: dict[str, Any], reason: str) -> None:
    reasons = entry.setdefault("prior_exclusion_reasons", [])
    if reason and reason not in reasons:
        reasons.append(reason)


def _audit_selection(entry: dict[str, Any], selection_state: str) -> None:
    entry["selection_state"] = selection_state


def _dedupe_dashboard_rumors(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    winners: dict[tuple[str, ...], tuple[tuple[int, int, int, str, str, int], dict[str, Any]]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        identity = _canonical_fact_identity(item)
        sort_key = _visible_item_sort_key(item, index)
        if identity not in winners or sort_key < winners[identity][0]:
            winners[identity] = (sort_key, item)
    return [item for _sort_key, item in sorted(winners.values(), key=lambda entry: entry[0])]


def _visible_item_signature(item: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], str, str, tuple[str, str, str]]:
    directed = _directed_endpoint_feature(item)
    pair = tuple(sorted(endpoint for endpoint in directed if endpoint))
    return (
        pair,
        directed,
        _visible_stakes_tier(item),
        _visible_summary_stem(item),
        _first_source_signature(item),
    )


def _visible_item_sort_key(item: dict[str, Any], original_index: int = 0) -> tuple[int, int, int, str, str, int]:
    pressure_strength = _visible_rank_tier(item) if _dashboard_break_reason(item) else 0
    return (
        -_visible_rank_tier(item),
        -pressure_strength,
        -len(item.get("sources") or []),
        _normalized_display_key(str(item.get("prep_text") or item.get("summary") or "")),
        _first_source_id(item),
        original_index,
    )


def _dedupe_visible_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    winners: dict[tuple[tuple[str, ...], tuple[str, ...], str, str, tuple[str, str, str]], tuple[tuple[int, int, int, str, str, int], dict[str, Any]]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        signature = _visible_item_signature(item)
        sort_key = _visible_item_sort_key(item, index)
        if signature not in winners or sort_key < winners[signature][0]:
            winners[signature] = (sort_key, item)
    return [item for _sort_key, item in sorted(winners.values(), key=lambda entry: entry[0])]


def _suppress_equivalent_items(items: Sequence[dict[str, Any]], selected: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_keys = {grounded_fact_key(item) for item in selected if isinstance(item, dict)}
    selected_signatures = {_visible_item_signature(item) for item in selected if isinstance(item, dict)}
    return [
        item
        for item in items
        if isinstance(item, dict)
        and grounded_fact_key(item) not in selected_keys
        and _visible_item_signature(item) not in selected_signatures
    ]


def _directed_endpoint_feature(item: dict[str, Any]) -> tuple[str, ...]:
    source_id = _normalized_endpoint(str(item.get("source_npc_id") or ""))
    target_id = _normalized_endpoint(str(item.get("target_npc_id") or ""))
    if source_id or target_id:
        return (source_id, target_id)
    return tuple(_normalized_endpoint(str(character)) for character in item.get("characters") or [] if str(character))


def _normalized_endpoint(value: str) -> str:
    return _normalized_display_key(str(value or ""))


def _first_source_signature(item: dict[str, Any]) -> tuple[str, str, str]:
    sources = item.get("sources") or []
    first = sources[0] if sources and isinstance(sources[0], dict) else {}
    path = str(first.get("path") or "")
    path_key = _normalized_display_key(Path(path).stem if path else "")
    return (
        _normalized_endpoint(str(first.get("character_id") or item.get("source_npc_id") or "")),
        path_key,
        _normalized_display_key(str(first.get("section") or "")),
    )


def _visible_stakes_tier(item: dict[str, Any]) -> str:
    fact_type = str(item.get("type") or "").lower()
    display_label = str(item.get("display_label") or "").lower()
    summary = clean_display_text(str(item.get("summary") or "")).lower()
    haystack = " ".join([fact_type, display_label, summary])
    if _contains_any(haystack, ("grave misconduct",)):
        return "grave misconduct"
    if _contains_any(haystack, ("coercive bond", "coercively bound", "blood bound", "compulsion")):
        return "blood_bond"
    if _contains_any(haystack, ("coerc", "manipulat", "using", "toy", "test subject", "studying", "exploit")):
        return "manipulation"
    if _contains_any(haystack, ("blackmail", "leverage", "expose", "exposure", "scandal")):
        return "leverage"
    if _contains_any(haystack, ("betray", "traitor")):
        return "betrayal"
    if _contains_any(haystack, ("debt", "favor", "owes", "owed", "favor")):
        return "debt"
    if _contains_any(haystack, ("dependen",)):
        return "dependency"
    if _contains_any(haystack, ("danger", "threat", "sacrifice")):
        return "danger"
    conflict_labels = ("enemy", "rival", "rivalry", "hostile", "hostility", "distrust", "grievance")
    active_conflict = (
        "attack",
        "bitter",
        "confront",
        "feud",
        "fears",
        "hostil",
        "kill",
        "resent",
        "revenge",
        "sabotage",
        "suspects",
        "undermine",
        "warn",
    )
    if _contains_any(haystack, conflict_labels) and _contains_any(summary, active_conflict):
        return "active_conflict"
    if _contains_any(haystack, ("paramour", "lover", "intimacy")):
        return "intimacy"
    if _contains_any(haystack, conflict_labels):
        return "conflict"
    if _contains_any(haystack, ("political opposition", "opposition", "useful", "business", "contact", "ally", "patron")):
        return "political_usefulness"
    return f"fact:{fact_type or 'unknown'}"


def _visible_rank_tier(item: dict[str, Any]) -> int:
    ranks = {
        "grave misconduct": 120,
        "blood_bond": 115,
        "manipulation": 110,
        "leverage": 105,
        "betrayal": 100,
        "debt": 95,
        "dependency": 90,
        "danger": 85,
        "active_conflict": 80,
        "intimacy": 75,
        "conflict": 55,
        "political_usefulness": 10,
    }
    return max(ranks.get(_visible_stakes_tier(item), 20), _dramatic_item_score(item))


def _visible_summary_stem(item: dict[str, Any]) -> str:
    text = _normalized_display_key(str(item.get("summary") or ""))
    fact_type = _normalized_display_key(str(item.get("type") or ""))
    display_label = _normalized_display_key(str(item.get("display_label") or ""))
    for prefix in (display_label, fact_type):
        if prefix:
            text = re.sub(rf"^\s*{re.escape(prefix)}\s*:\s*", "", text)
    text = re.sub(r"^\s*[^:]{1,80}\s*:\s*", "", text)
    replacements = {
        r"\bblackmail material\b": "blackmail",
        r"\bblackmail leverage\b": "blackmail",
        r"\bblood[- ]bound\b": "coercive bond",
        r"\bfavors\b": "favor",
        r"\bfavors\b": "favor",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    stopwords = {
        "a",
        "an",
        "and",
        "about",
        "around",
        "has",
        "have",
        "holds",
        "keeps",
        "keep",
        "of",
        "on",
        "over",
        "the",
        "to",
        "uses",
        "with",
    }
    words = [word for word in re.findall(r"[a-z0-9']+", text) if word not in stopwords]
    return " ".join(words[:18])


def dedupe_grounded_facts(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = grounded_fact_key(item)
        if key in seen:
            continue
        seen.add(key)
        clean = dict(item)
        clean["summary"] = clean_display_text(str(clean.get("summary") or ""))
        result.append(clean)
    return result


def dedupe_display_strings(items: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = clean_display_text(str(item or ""))
        key = _normalized_display_key(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def clean_display_text(text: str) -> str:
    original = str(text or "")
    try:
        cleaned = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", lambda match: match.group(2).strip(), original)
        cleaned = re.sub(r"\[\[([^\]]+)\]\]", lambda match: match.group(1).strip(), cleaned)
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned
    except re.error:
        return original


def cluster_social_map(attendee_ids: Sequence[str], relationships: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_ids = [str(npc_id) for npc_id in attendee_ids]
    attendee_set = set(ordered_ids)
    guest_index = {npc_id: idx for idx, npc_id in enumerate(ordered_ids)}
    adjacency: dict[str, set[str]] = {npc_id: set() for npc_id in ordered_ids}
    for item in relationships:
        characters = [str(character) for character in item.get("characters") or [] if str(character) in attendee_set]
        for idx, subject in enumerate(characters):
            for object_id in characters[idx + 1 :]:
                if subject == object_id:
                    continue
                adjacency[subject].add(object_id)
                adjacency[object_id].add(subject)

    components: list[list[str]] = []
    seen: set[str] = set()
    for npc_id in ordered_ids:
        if npc_id in seen:
            continue
        stack = [npc_id]
        component: list[str] = []
        seen.add(npc_id)
        while stack:
            current = stack.pop()
            component.append(current)
            neighbors = sorted(adjacency[current], key=lambda item: guest_index.get(item, 10**9))
            for neighbor in neighbors:
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        components.append(sorted(component, key=lambda item: guest_index.get(item, 10**9)))

    components = sorted(components, key=lambda component: (-len(component), _earliest_guest_index(component, guest_index)))
    result: list[dict[str, Any]] = []
    isolated: list[str] = []
    group_no = 1
    for component in components:
        if len(component) == 1:
            isolated.extend(component)
            continue
        result.append(
            {
                "location": f"Connected Group {group_no}",
                "npc_ids": component,
                "summary": f"{len(component)} attendees share grounded relationship edges.",
            }
        )
        group_no += 1
    if isolated:
        result.append(
            {
                "location": "Circulating / Unanchored",
                "npc_ids": sorted(isolated, key=lambda item: guest_index.get(item, 10**9)),
                "summary": "No attendee relationship edge was found in the current club indexes.",
            }
        )
    return result


def rank_connections(
    items: Sequence[dict[str, Any]],
    *,
    identity_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    identity_by_id = identity_by_id or {}
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in dedupe_grounded_facts(items):
        key = _connection_group_key(item, identity_by_id)
        if len(key) < 2:
            continue
        grouped.setdefault(key, []).append(item)

    ranked_items: list[tuple[int, tuple[str, ...], dict[str, Any]]] = []
    for group_key, group_items in grouped.items():
        distinct_fact_keys: set[tuple[str, str]] = set()
        score = 0
        for item in group_items:
            fact_type = str(item.get("type") or "").lower()
            for source in item.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                fact_key = (fact_type, str(source.get("source_id") or ""))
                if fact_key in distinct_fact_keys:
                    continue
                distinct_fact_keys.add(fact_key)
                score += _dramatic_item_score(item)
        representative = sorted(
            group_items,
            key=lambda item: (
                -_dramatic_item_score(item),
                _normalized_display_key(str(item.get("summary") or "")),
                _first_source_id(item),
            ),
        )[0]
        merged = dict(representative)
        merged["characters"] = _directed_characters_for_item(representative) if representative.get("source_npc_id") and representative.get("target_npc_id") else list(group_key)
        merged["sources"] = _dedupe_sources(representative.get("sources") or [])
        ranked_items.append((score, group_key, merged))

    return [
        item
        for _score, _group_key, item in sorted(
            ranked_items,
            key=lambda entry: (
                -entry[0],
                " / ".join(_identity_display_name(npc_id, identity_by_id) for npc_id in entry[1]),
                "/".join(entry[1]),
            ),
        )
    ]


def _all_index_facts(index: ClubIndex) -> list[ClubFact]:
    return [
        *index.relationships,
        *index.current_goals,
        *index.grievances,
        *index.unresolved_business,
        *index.rumor_notes,
        *index.story_hooks,
        *index.recent_relevant_events,
    ]


def _normalized_display_key(text: str) -> str:
    return re.sub(r"\s+", " ", clean_display_text(text).strip().lower())


def _earliest_guest_index(component: Sequence[str], guest_index: dict[str, int]) -> int:
    return min((guest_index.get(npc_id, 10**9) for npc_id in component), default=10**9)


def _connection_group_key(item: dict[str, Any], identity_by_id: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    characters = [str(character) for character in item.get("characters") or []]
    unique = list(dict.fromkeys(character for character in characters if character))
    return tuple(
        sorted(
            unique,
            key=lambda npc_id: (
                _identity_display_name(npc_id, identity_by_id).lower(),
                npc_id,
            ),
        )
    )


def _directed_characters_for_item(item: dict[str, Any]) -> list[str]:
    source_id = str(item.get("source_npc_id") or "")
    target_id = str(item.get("target_npc_id") or "")
    if source_id and target_id:
        return [source_id, target_id]
    characters = [str(character) for character in item.get("characters") or [] if str(character)]
    return list(dict.fromkeys(characters))


def _connection_display_name(item: dict[str, Any], identity_by_id: dict[str, dict[str, Any]]) -> str:
    return " / ".join(_identity_display_name(npc_id, identity_by_id) for npc_id in item.get("characters") or [])


def _identity_display_name(npc_id: str, identity_by_id: dict[str, dict[str, Any]]) -> str:
    identity = identity_by_id.get(str(npc_id)) or {}
    return clean_display_text(str(identity.get("display_name") or identity.get("name") or npc_id))


def _connection_npc_id(item: dict[str, Any]) -> str:
    return "/".join(str(npc_id) for npc_id in item.get("characters") or [])


def _first_source_id(item: dict[str, Any]) -> str:
    sources = item.get("sources") or []
    if not sources or not isinstance(sources[0], dict):
        return ""
    return str(sources[0].get("source_id") or "")


def _dedupe_sources(sources: Iterable[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if not source_id or source_id in by_id:
            continue
        by_id[source_id] = dict(source)
    return [by_id[source_id] for source_id in sorted(by_id)]


def _dedupe_sources_in_order(sources: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        result.append(deepcopy(source))
    return result


def validate_dashboard(
    data: Any,
    *,
    source_ids: set[str],
    attendee_ids: set[str],
    skeleton: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ClubGenerationError("dashboard must be a JSON object")
    for key in FORBIDDEN_DASHBOARD_KEYS:
        if key in data:
            errors.append(f"{key} is not allowed")
    event = data.get("event")
    if not isinstance(event, dict):
        errors.append("event must be an object")
    elif not isinstance(event.get("venue", ""), str):
        errors.append("event.venue must be a string")
    elif any(key in event for key in FORBIDDEN_DASHBOARD_KEYS):
        errors.append("event must not include timeline or planned movement fields")
    if not isinstance(data.get("first_impression", ""), str):
        errors.append("first_impression must be a string")
    if data.get("room_situation") is not None and not isinstance(data.get("room_situation"), str):
        errors.append("room_situation must be a string")
    if not isinstance(data.get("social_map", []), list):
        errors.append("social_map must be a list")
    for field in ("hot_connections", "possible_pressure", "rumors_in_circulation", "guest_brief"):
        if data.get(field) is not None and not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")
        elif any(not isinstance(item, str) for item in data.get(field) or []):
            errors.append(f"{field} must contain strings")
    if skeleton is not None:
        _validate_skeleton_social_map(data.get("social_map", []), skeleton=skeleton, errors=errors)
    for field in GROUNDED_DASHBOARD_FIELDS:
        _validate_grounded_list(data.get(field, []), field=field, source_ids=source_ids, attendee_ids=attendee_ids, errors=errors)
        if skeleton is not None:
            _validate_skeleton_list(data.get(field, []), field=field, skeleton=skeleton, errors=errors)
    for idx, rumor in enumerate(data.get("rumors") or []):
        if isinstance(rumor, dict) and any(key in rumor for key in RUMOR_TRUTH_KEYS):
            errors.append(f"rumors[{idx}] must not include truth ratings")
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return data


def validate_npc_panel(
    data: Any,
    *,
    source_ids: set[str],
    npc_id: str,
    skeleton: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ClubGenerationError("npc panel must be a JSON object")
    if data.get("npc_id") != npc_id:
        errors.append("npc_id must match the clicked NPC")
    for field in ("current_read", "useful_hook"):
        if data.get(field) is not None and not isinstance(data.get(field), str):
            errors.append(f"{field} must be a string")
    for field in ("likely_conversation", "sensitive_subjects"):
        if data.get(field) is not None and not isinstance(data.get(field), list):
            errors.append(f"{field} must be a list")
        elif any(not isinstance(item, str) for item in data.get(field) or []):
            errors.append(f"{field} must contain strings")
    _validate_grounded_list(data.get("people_here", []), field="people_here", source_ids=source_ids, attendee_ids=set(), errors=errors)
    if skeleton is not None:
        _validate_skeleton_list(data.get("people_here", []), field="people_here", skeleton=skeleton, errors=errors)
    tonight = data.get("tonight")
    if isinstance(tonight, dict) and tonight.get("current_desire"):
        _validate_grounded_item(tonight.get("current_desire"), field="tonight.current_desire", source_ids=source_ids, errors=errors)
        if skeleton is not None:
            _validate_skeleton_item(tonight.get("current_desire"), field="tonight.current_desire", skeleton=skeleton, errors=errors)
    if isinstance(tonight, dict) and tonight.get("attitude_toward_pcs"):
        _validate_grounded_item(tonight.get("attitude_toward_pcs"), field="tonight.attitude_toward_pcs", source_ids=source_ids, errors=errors)
    if data.get("attitude_toward_pcs"):
        _validate_grounded_item(data.get("attitude_toward_pcs"), field="attitude_toward_pcs", source_ids=source_ids, errors=errors)
    conversation = data.get("conversation") if isinstance(data.get("conversation"), dict) else {}
    for field in ("likely_subjects", "sensitive"):
        path = f"conversation.{field}"
        _validate_grounded_list(conversation.get(field, []), field=path, source_ids=source_ids, attendee_ids=set(), errors=errors)
        if skeleton is not None:
            _validate_skeleton_list(conversation.get(field, []), field=path, skeleton=skeleton, errors=errors)
    if data.get("interesting_detail"):
        _validate_grounded_item(data.get("interesting_detail"), field="interesting_detail", source_ids=source_ids, errors=errors)
        if skeleton is not None:
            _validate_skeleton_item(data.get("interesting_detail"), field="interesting_detail", skeleton=skeleton, errors=errors)
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return data


def validate_ai_dashboard(
    data: Any,
    *,
    source_ids: set[str],
    attendee_ids: set[str],
    skeleton: dict[str, Any],
) -> dict[str, Any]:
    dashboard = validate_dashboard(data, source_ids=source_ids, attendee_ids=attendee_ids, skeleton=skeleton)
    errors: list[str] = []
    if _dashboard_requires_room_situation(skeleton):
        _validate_required_visible_text(dashboard.get("room_situation"), field="room_situation", errors=errors)
    for visible_field, grounded_field in (
        ("hot_connections", "top_connections"),
        ("possible_pressure", "possible_drama"),
        ("rumors_in_circulation", "rumors"),
        ("guest_brief", "attendees"),
    ):
        if skeleton.get(grounded_field):
            _validate_required_visible_list(dashboard.get(visible_field), field=visible_field, errors=errors)
    _validate_visible_text_shape(dashboard.get("first_impression"), field="first_impression", errors=errors)
    _validate_visible_text_shape(dashboard.get("room_situation"), field="room_situation", errors=errors)
    for field in DASHBOARD_VISIBLE_LIST_FIELDS:
        _validate_visible_list_shape(dashboard.get(field), field=field, errors=errors)
    for field in GROUNDED_DASHBOARD_FIELDS:
        _validate_grounded_prep_texts(dashboard.get(field) or [], field=field, errors=errors, require_non_empty=False)
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return dashboard


def validate_ai_npc_panel(
    data: Any,
    *,
    source_ids: set[str],
    npc_id: str,
    skeleton: dict[str, Any],
) -> dict[str, Any]:
    panel = validate_npc_panel(data, source_ids=source_ids, npc_id=npc_id, skeleton=skeleton)
    errors: list[str] = []
    if isinstance(panel.get("presentation"), dict):
        cleaned_presentation = _validated_panel_presentation(
            {"npc_id": panel.get("npc_id"), "presentation": panel.get("presentation")},
            skeleton,
        )
        if panel.get("presentation") != cleaned_presentation:
            errors.append("presentation must contain normalized GM-facing cue text")
        expected_likely = [entry["text"] for entry in cleaned_presentation["if_approached"]]
        expected_sensitive = [entry["text"] for entry in cleaned_presentation["keep_guarded"]]
        expected_hook = cleaned_presentation["hook"]["text"] if cleaned_presentation["hook"] else ""
        tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
        if str(tonight.get("current_demeanor") or "") != cleaned_presentation["play_cue"]:
            errors.append("tonight.current_demeanor must match presentation.play_cue")
        if panel.get("likely_conversation") != expected_likely:
            errors.append("likely_conversation must match presentation.if_approached")
        if panel.get("sensitive_subjects") != expected_sensitive:
            errors.append("sensitive_subjects must match presentation.keep_guarded")
        if str(panel.get("useful_hook") or "") != expected_hook:
            errors.append("useful_hook must match presentation.hook")
    else:
        _validate_npc_panel_presentation_contract(panel, skeleton=skeleton, errors=errors)
    if _npc_panel_has_read_basis(skeleton):
        _validate_required_visible_text(panel.get("current_read"), field="current_read", errors=errors)
    expected_current_read = str(skeleton.get("current_read") or _fallback_current_read(skeleton))
    if str(panel.get("current_read") or "") != expected_current_read:
        errors.append("current_read must match the deterministic skeleton current_read")
    conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    if conversation.get("likely_subjects"):
        _validate_required_visible_list(panel.get("likely_conversation"), field="likely_conversation", errors=errors)
    if conversation.get("sensitive"):
        _validate_required_visible_list(panel.get("sensitive_subjects"), field="sensitive_subjects", errors=errors)
    if skeleton.get("interesting_detail"):
        _validate_required_visible_text(panel.get("useful_hook"), field="useful_hook", errors=errors)
    for field in ("current_read", "useful_hook"):
        _validate_visible_text_shape(panel.get(field), field=field, errors=errors)
    for field in NPC_PANEL_VISIBLE_LIST_FIELDS:
        _validate_visible_list_shape(panel.get(field), field=field, errors=errors)
    _validate_grounded_prep_texts(panel.get("people_here") or [], field="people_here", errors=errors)
    tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
    if isinstance(tonight.get("current_desire"), dict):
        _validate_grounded_prep_texts([tonight["current_desire"]], field="tonight.current_desire", errors=errors)
    conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}
    for field in ("likely_subjects", "sensitive"):
        _validate_grounded_prep_texts(conversation.get(field) or [], field=f"conversation.{field}", errors=errors)
    if isinstance(panel.get("interesting_detail"), dict):
        _validate_grounded_prep_texts([panel["interesting_detail"]], field="interesting_detail", errors=errors)
    if errors:
        raise ClubGenerationError("; ".join(errors))
    return panel


def _validate_npc_panel_presentation_contract(
    panel: dict[str, Any],
    *,
    skeleton: dict[str, Any],
    errors: list[str],
) -> None:
    skeleton_conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    panel_conversation = panel.get("conversation") if isinstance(panel.get("conversation"), dict) else {}

    def compare_items(field: str, actual: Any, expected: Any) -> None:
        actual_items = actual if isinstance(actual, list) else []
        expected_items = expected if isinstance(expected, list) else []
        if len(actual_items) != len(expected_items):
            errors.append(f"{field} must match deterministic skeleton item order")
            return
        for index, (actual_item, expected_item) in enumerate(zip(actual_items, expected_items)):
            if not isinstance(actual_item, dict) or not isinstance(expected_item, dict):
                continue
            if str(actual_item.get("prep_text") or "") != str(expected_item.get("prep_text") or ""):
                errors.append(f"{field}[{index}].prep_text must match deterministic skeleton presentation")

    compare_items("people_here", panel.get("people_here"), skeleton.get("people_here"))
    compare_items(
        "conversation.likely_subjects",
        panel_conversation.get("likely_subjects"),
        skeleton_conversation.get("likely_subjects"),
    )
    compare_items(
        "conversation.sensitive",
        panel_conversation.get("sensitive"),
        skeleton_conversation.get("sensitive"),
    )

    skeleton_tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    panel_tonight = panel.get("tonight") if isinstance(panel.get("tonight"), dict) else {}
    expected_focus = skeleton_tonight.get("current_desire")
    actual_focus = panel_tonight.get("current_desire")
    if isinstance(expected_focus, dict):
        if not isinstance(actual_focus, dict):
            errors.append("tonight.current_desire must match deterministic skeleton support")
        elif str(actual_focus.get("prep_text") or "") != str(expected_focus.get("prep_text") or ""):
            errors.append("tonight.current_desire.prep_text must match deterministic skeleton presentation")
    elif actual_focus not in (None, ""):
        errors.append("tonight.current_desire must be absent when the deterministic skeleton has no focus")

    expected_detail = skeleton.get("interesting_detail")
    actual_detail = panel.get("interesting_detail")
    if isinstance(expected_detail, dict):
        if not isinstance(actual_detail, dict):
            errors.append("interesting_detail must match deterministic skeleton support")
        elif str(actual_detail.get("prep_text") or "") != str(expected_detail.get("prep_text") or ""):
            errors.append("interesting_detail.prep_text must match deterministic skeleton presentation")
    elif actual_detail not in (None, ""):
        errors.append("interesting_detail must be absent when the deterministic skeleton has no hook")

    expected_likely = [_display_text_for_render(item) for item in skeleton_conversation.get("likely_subjects") or []]
    expected_sensitive = [_display_text_for_render(item) for item in skeleton_conversation.get("sensitive") or []]
    expected_hook = _display_text_for_render(expected_detail) if isinstance(expected_detail, dict) else ""
    if panel.get("likely_conversation") != expected_likely:
        errors.append("likely_conversation must match deterministic skeleton presentation")
    if panel.get("sensitive_subjects") != expected_sensitive:
        errors.append("sensitive_subjects must match deterministic skeleton presentation")
    if str(panel.get("useful_hook") or "") != expected_hook:
        errors.append("useful_hook must match deterministic skeleton presentation")


def _dashboard_requires_room_situation(skeleton: dict[str, Any]) -> bool:
    return bool(skeleton.get("attendees") or skeleton.get("room_groups") or _skeleton_grounded_items(skeleton))


def _npc_panel_has_read_basis(skeleton: dict[str, Any]) -> bool:
    identity = skeleton.get("identity") if isinstance(skeleton.get("identity"), dict) else {}
    tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
    conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
    return bool(
        any(identity.get(field) for field in ("affiliation", "faction", "status", "roles"))
        or skeleton.get("personality")
        or tonight.get("current_desire")
        or skeleton.get("people_here")
        or conversation.get("likely_subjects")
        or conversation.get("sensitive")
        or skeleton.get("interesting_detail")
    )


def _validate_required_visible_text(value: Any, *, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not clean_display_text(value):
        errors.append(f"{field} must contain non-empty GM-facing prose")


def _validate_required_visible_list(value: Any, *, field: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not any(isinstance(item, str) and clean_display_text(item) for item in value):
        errors.append(f"{field} must contain at least one non-empty GM-facing string")


def _validate_visible_list_shape(value: Any, *, field: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        return
    for idx, item in enumerate(value):
        _validate_visible_text_shape(item, field=f"{field}[{idx}]", errors=errors)


def _validate_visible_text_shape(value: Any, *, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        return
    text = clean_display_text(value)
    if text and _looks_like_extraction_record(text):
        errors.append(f"{field} must be GM-facing prose, not serialized extraction output")


def _validate_grounded_prep_texts(
    items: Sequence[Any],
    *,
    field: str,
    errors: list[str],
    require_non_empty: bool = True,
) -> None:
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        prep_text = item.get("prep_text")
        if not isinstance(prep_text, str) or not clean_display_text(prep_text):
            if not require_non_empty:
                continue
            errors.append(f"{field}[{idx}].prep_text must contain non-empty GM-facing prose")
            continue
        _validate_visible_text_shape(prep_text, field=f"{field}[{idx}].prep_text", errors=errors)


def _looks_like_extraction_record(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    machine_keys = (
        "source_npc_id",
        "target_npc_id",
        "mentioned_npc_ids",
        "item_id",
        "fact_type",
        "fact_scope",
        "source_id",
        "display_label",
        "characters",
        "sources",
    )
    key_pattern = "|".join(re.escape(key) for key in machine_keys)
    if re.search(rf'(?i)(?:^|[\s{{,\[])"?(?:{key_pattern})"?\s*[:=]', stripped):
        return True
    if re.match(
        r"(?i)^\s*(?:subject\s+object\s+type|fact\s+scope|source\s+id|database\s+row|item\s+id|source\s+npc\s+id|target\s+npc\s+id|fact\s+type)\s*:",
        stripped,
    ):
        return True
    if stripped.startswith("{") and stripped.endswith("}") and ":" in stripped:
        return True
    if stripped.startswith("[") and stripped.endswith("]") and ":" in stripped:
        return True
    assignments = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{1,40}\s*=\s*[^,;]+", stripped)
    return len(assignments) >= 2


def estimate_prompt_tokens(prompt: str) -> int:
    return max(1, len(prompt or "") // 4)


def compact_skeleton_for_prompt(skeleton: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(skeleton, dict):
        return {}
    kind = skeleton.get("kind")
    if kind == "dashboard":
        return {
            "kind": "dashboard",
            "schema_version": skeleton.get("schema_version"),
            "event": skeleton.get("event") or {},
            "attendee_ids": list(skeleton.get("attendee_ids") or []),
            "attendees": [
                {
                    "npc_id": str(item.get("npc_id") or ""),
                    "display_name": clean_display_text(str(item.get("display_name") or item.get("name") or "")),
                }
                for item in skeleton.get("attendees") or []
                if isinstance(item, dict)
            ],
            "room_groups": list(skeleton.get("room_groups") or [])[:5],
            "social_map": list(skeleton.get("social_map") or [])[:5],
            "notable_details": list(skeleton.get("notable_details") or [])[:4],
            "room_situation": str(skeleton.get("room_situation") or ""),
            "hot_connections": list(skeleton.get("hot_connections") or [])[:4],
            "possible_pressure": list(skeleton.get("possible_pressure") or [])[:4],
            "rumors_in_circulation": list(skeleton.get("rumors_in_circulation") or [])[:7],
            "guest_brief": list(skeleton.get("guest_brief") or [])[:8],
            "rumors": [_compact_prompt_item(item) for item in (skeleton.get("rumors") or [])[:7]],
            "possible_drama": [_compact_prompt_item(item) for item in (skeleton.get("possible_drama") or [])[:4]],
            "interesting_connections": [_compact_prompt_item(item) for item in (skeleton.get("interesting_connections") or [])[:4]],
            "unresolved_business": [_compact_prompt_item(item) for item in (skeleton.get("unresolved_business") or [])[:3]],
            "opportunities": [_compact_prompt_item(item) for item in (skeleton.get("opportunities") or [])[:3]],
            "top_connections": [_compact_prompt_item(item) for item in (skeleton.get("top_connections") or [])[:5]],
            "background_details": list(skeleton.get("background_details") or [])[:3],
            "source_ids": list(skeleton.get("source_ids") or []),
        }
    if kind == "npc_panel":
        conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
        tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
        return {
            "kind": "npc_panel",
            "schema_version": skeleton.get("schema_version"),
            "npc_id": str(skeleton.get("npc_id") or ""),
            "name": clean_display_text(str(skeleton.get("name") or "")),
            "identity": skeleton.get("identity") or {},
            "personality": list(skeleton.get("personality") or []),
            "portrayal_basis": {"demeanor": _npc_panel_sourced_demeanor(skeleton)},
            "current_read": str(skeleton.get("current_read") or ""),
            "likely_conversation": list(skeleton.get("likely_conversation") or [])[:4],
            "sensitive_subjects": list(skeleton.get("sensitive_subjects") or [])[:4],
            "useful_hook": str(skeleton.get("useful_hook") or ""),
            "tonight": {
                "current_demeanor": str(tonight.get("current_demeanor") or ""),
                "current_desire": _compact_prompt_item(tonight.get("current_desire")) if isinstance(tonight.get("current_desire"), dict) else None,
            },
            "people_here": [_compact_prompt_item(item) for item in skeleton.get("people_here") or []],
            "conversation": {
                "likely_subjects": [_compact_prompt_item(item) for item in conversation.get("likely_subjects") or []],
                "sensitive": [_compact_prompt_item(item) for item in conversation.get("sensitive") or []],
            },
            "interesting_detail": _compact_prompt_item(skeleton.get("interesting_detail")) if isinstance(skeleton.get("interesting_detail"), dict) else None,
            "empty_state": str(skeleton.get("empty_state") or ""),
            "source_ids": list(skeleton.get("source_ids") or []),
        }
    return safe_debug_data(skeleton)


def _compact_prompt_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    summary = clean_display_text(str(item.get("summary") or ""))
    if len(summary) > PROMPT_SUMMARY_CHARS:
        summary = summary[: PROMPT_SUMMARY_CHARS - 1].rstrip() + "..."
    return {
        "item_id": str(item.get("item_id") or ""),
        "section": str(item.get("section") or ""),
        "type": str(item.get("type") or ""),
        "fact_scope": str(item.get("fact_scope") or ""),
        "source_npc_id": str(item.get("source_npc_id") or ""),
        "target_npc_id": None if str(item.get("type") or "").lower() == "rumor" else str(item.get("target_npc_id") or ""),
        "mentioned_npc_ids": list(item.get("mentioned_npc_ids") or []),
        "link_targets": list(item.get("link_targets") or []),
        "characters": list(item.get("characters") or []),
        "display_label": clean_display_text(str(item.get("display_label") or "")),
        "summary": summary,
        "prep_text": clean_display_text(str(item.get("prep_text") or "")),
        "sources": _compact_sources_for_prompt(item.get("sources") or []),
    }


def dashboard_prompt(skeleton: dict[str, Any]) -> str:
    prompt_skeleton = compact_skeleton_for_prompt(skeleton)
    return (
        "Return only valid JSON for a system-neutral social-event dashboard from this skeleton. "
        "Write the dashboard as table-ready GM prep, not as database rows. "
        "The visible dashboard is room_situation, hot_connections, possible_pressure, rumors_in_circulation, and guest_brief. "
        "room_situation must be a string. hot_connections, possible_pressure, rumors_in_circulation, and guest_brief must be arrays of strings only, never objects. "
        "Keep those strings compact, human, and immediately playable. Do not write raw record labels such as 'Subject Object Type:', 'Source ID:', 'item_id=', or JSON-like key/value fragments. "
        "The skeleton is the complete list of grounded facts you may use. "
        "For grounded object arrays, copy each object from its matching skeleton section and preserve item_id, section, type, fact_scope, source_npc_id, target_npc_id, mentioned_npc_ids, link_targets, characters, display_label, summary, and sources[].source_id exactly, including list order. "
        "Do not summarize, paraphrase, translate, trim, expand, or normalize any summary field. Do not move facts between sections. Do not add grounded facts that are not in the skeleton. Only prep_text may be rewritten on grounded objects. "
        "You may invent only connective presentation: mood, first-impression prose, atmosphere, table-facing wording, and room situation framing. "
        "Do not promote loyalty, duty, reporting, usefulness, or generic political contact into possible_pressure unless the skeleton text includes conflict, leverage, exposure, coercion, betrayal, debt, coercive bond, blackmail, scandal, exploitation, rivalry, or danger. "
        "Do not turn private goals into opportunities unless the PCs could notice, ask about, interrupt, pressure, exploit, or act on them tonight. "
        "Every item in rumors, possible_drama, interesting_connections, unresolved_business, opportunities, and top_connections must be copied from the matching skeleton section with sources[]. "
        "For copied grounded items, preserve summary as the source-derived text and write/retain prep_text as the GM-facing sentence. "
        "Do not create an event timeline, planned NPC movement, or rumor truth ratings.\n\n"
        "Required JSON fields: event, first_impression, room_situation, hot_connections, possible_pressure, rumors_in_circulation, guest_brief, "
        "social_map, notable_details, rumors, possible_drama, interesting_connections, unresolved_business, opportunities, top_connections, background_details.\n\n"
        f"Skeleton JSON:\n{json.dumps(prompt_skeleton, ensure_ascii=False, separators=(',', ':'))}"
    )


def npc_panel_prompt(skeleton: dict[str, Any], *, npc_id: str) -> str:
    prompt_skeleton = compact_skeleton_for_prompt(skeleton)
    return (
        "Return only valid JSON containing a presentation layer for the clicked NPC's deterministic, grounded play card. "
        "Do not copy, rewrite, or return canonical fact objects, summaries, sources, identity fields, current_read, or metadata. "
        "The application owns all factual support and will join your cues back to it by item_id. "
        "Return exactly two top-level fields: npc_id and presentation. presentation must contain exactly play_cue, agenda, people_here, if_approached, keep_guarded, and hook. "
        "play_cue is a string. Write a non-empty play_cue only when portrayal_basis.demeanor or personality provides a portrayal basis; when both are absent, play_cue must be empty. Do not derive play_cue from affiliation, faction, status, roles, relationships, goals, rumors, or other facts. agenda and hook are either null when their skeleton support is absent or an object with exactly item_id and text. "
        "people_here, if_approached, and keep_guarded are arrays of objects with exactly item_id and text. "
        "Map every supplied item exactly once, preserve each section's item_id order, and never move an item between sections. "
        "Write each text as a concrete, immediately playable GM cue of at most 180 characters. Use play_cue for delivery or manner, agenda for what to pursue tonight, people_here for how to treat that person, if_approached for a likely response, keep_guarded for what not to volunteer, and hook for how to put the pressure point into play. "
        "The skeleton is the complete factual basis. Do not introduce new people, events, motives, knowledge, plans, outcomes, or relationships. "
        "Do not write raw record labels such as 'Source ID:', 'item_id=', provenance, citations, or JSON-like key/value fragments inside text.\n\n"
        "Required JSON shape: {\"npc_id\":\"...\",\"presentation\":{\"play_cue\":\"...\",\"agenda\":null-or-item,\"people_here\":[],\"if_approached\":[],\"keep_guarded\":[],\"hook\":null-or-item}}.\n"
        f"Clicked npc_id: {npc_id}\n\n"
        f"Skeleton JSON:\n{json.dumps(prompt_skeleton, ensure_ascii=False, separators=(',', ':'))}"
    )


def _generate_valid_json(provider: ClubProvider, prompt: str, *, validator) -> dict[str, Any]:
    request_options = _club_json_request_options()
    messages = [
        {"role": "system", "content": "You are a citation-grounded JSON generator. Return JSON only."},
        {"role": "user", "content": prompt},
    ]
    first = provider.generate_from_messages(messages, strip_response=True, request_options=request_options)
    try:
        return validator(_parse_json_object(first))
    except ClubGenerationError as exc:
        validation_error = _sanitize_validation_error(str(exc))
        retry_prompt = (
            f"{prompt}\n\nThe previous JSON failed validation with these errors: {validation_error}. "
            "Return corrected JSON only. Do not add unsupported grounded claims."
        )
        retry_messages = [
            {"role": "system", "content": "You are a citation-grounded JSON generator. Return JSON only."},
            {"role": "user", "content": retry_prompt},
        ]
        second = provider.generate_from_messages(
            retry_messages,
            strip_response=True,
            request_options=_club_json_request_options(),
        )
        try:
            return validator(_parse_json_object(second))
        except ClubGenerationError as second_exc:
            raise ClubGenerationError(_sanitize_validation_error(str(second_exc))) from second_exc


def _club_json_request_options() -> dict[str, Any]:
    return {
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }


def _sanitize_validation_error(text: Any, *, limit: int = 700) -> str:
    clean = " ".join(str(text or "").split())
    clean = re.sub(r"(?is)Traceback \(most recent call last\):.*", "Traceback <redacted>", clean)
    clean = re.sub(
        r"(?i)\b(api[_-]?key|authorization|bearer|password|secret|token)\b\s*[:=]\s*['\"]?[^'\"\s,;}\]]+",
        r"\1=<redacted>",
        clean,
    )
    clean = re.sub(r"(?i)(['\"]?Authorization['\"]?\s*[:=]\s*)['\"]?Bearer\s+[^'\"\s,;}\]]+", r"\1<redacted>", clean)
    clean = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", clean)
    clean = re.sub(r"(?i)\bsk-[A-Za-z0-9._-]+", "sk-<redacted>", clean)
    clean = re.sub(r"\b[A-Z0-9_]*DO_NOT_LEAK[A-Z0-9_]*\b", "<redacted>", clean)
    clean = re.sub(r"(?i)\b(payload|messages|prompt|context)\b\s*[:=]\s*(\{.*?\}|\[.*?\]|\".*?\"|'.*?')", r"\1=<redacted>", clean)
    clean = re.sub(r"(?i)(?:[A-Z]:\\|\\\\\?\\|\\\\)[^'\"\s,;)\]}]+", "<path>", clean)
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
    return clean


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ClubGenerationError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClubGenerationError("JSON root must be an object")
    return data


def _validate_grounded_list(
    value: Any,
    *,
    field: str,
    source_ids: set[str],
    attendee_ids: set[str],
    errors: list[str],
) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, list):
        errors.append(f"{field} must be a list")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{field}[{idx}] must be an object with sources[]")
            continue
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{field}[{idx}].sources must be a non-empty list")
            continue
        for source in sources:
            if not isinstance(source, dict):
                errors.append(f"{field}[{idx}].sources entries must be objects")
                continue
            if source.get("source_id") not in source_ids:
                errors.append(f"{field}[{idx}] uses unknown source_id {source.get('source_id')!r}")
        if attendee_ids:
            for character in item.get("characters") or []:
                if character not in attendee_ids:
                    errors.append(f"{field}[{idx}] references non-attendee {character!r}")


def _validate_skeleton_list(value: Any, *, field: str, skeleton: dict[str, Any], errors: list[str]) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, list):
        return
    seen_item_ids: set[str] = set()
    for idx, item in enumerate(value):
        if isinstance(item, dict):
            item_id = str(item.get("item_id") or "")
            if item_id:
                if item_id in seen_item_ids:
                    errors.append(f"{field}[{idx}].item_id duplicates returned skeleton item {item_id}")
                seen_item_ids.add(item_id)
        _validate_skeleton_item(item, field=f"{field}[{idx}]", skeleton=skeleton, errors=errors, map_field=field)


def _validate_skeleton_item(
    value: Any,
    *,
    field: str,
    skeleton: dict[str, Any],
    errors: list[str],
    map_field: str | None = None,
) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, dict):
        errors.append(f"{field} must be a skeleton grounded object")
        return
    maps = _skeleton_item_maps(skeleton)
    lookup_field = map_field or field
    expected_by_id = maps.get(lookup_field) or {}
    item_id = str(value.get("item_id") or "")
    expected = expected_by_id.get(item_id)
    if not item_id or expected is None:
        errors.append(f"{field}.item_id must match an item from skeleton section {lookup_field}")
        return
    allowed_keys = {
        "item_id",
        "section",
        "type",
        "fact_scope",
        "source_npc_id",
        "target_npc_id",
        "mentioned_npc_ids",
        "link_targets",
        "characters",
        "display_label",
        "summary",
        "sources",
        "prep_text",
    }
    extra_keys = sorted(str(key) for key in value if str(key) not in allowed_keys)
    if extra_keys:
        errors.append(f"{field} has unsupported grounded fields: {', '.join(extra_keys)}")
    for key in ("section", "type", "fact_scope", "source_npc_id", "display_label"):
        if clean_display_text(str(value.get(key) or "")) != clean_display_text(str(expected.get(key) or "")):
            errors.append(f"{field}.{key} must match skeleton item {item_id}")
    if str(expected.get("type") or "").lower() == "rumor":
        if value.get("target_npc_id") is not None:
            errors.append(f"{field}.target_npc_id must be null for rumor item {item_id}")
    elif clean_display_text(str(value.get("target_npc_id") or "")) != clean_display_text(str(expected.get("target_npc_id") or "")):
        errors.append(f"{field}.target_npc_id must match skeleton item {item_id}")
    if clean_display_text(str(value.get("summary") or "")) != clean_display_text(str(expected.get("summary") or "")):
        errors.append(f"{field}.summary must match skeleton item {item_id}")
    if [str(item) for item in value.get("characters") or []] != [str(item) for item in expected.get("characters") or []]:
        errors.append(f"{field}.characters must match skeleton item {item_id}")
    if [str(item) for item in value.get("mentioned_npc_ids") or []] != [str(item) for item in expected.get("mentioned_npc_ids") or []]:
        errors.append(f"{field}.mentioned_npc_ids must match skeleton item {item_id}")
    if [str(item) for item in value.get("link_targets") or []] != [str(item) for item in expected.get("link_targets") or []]:
        errors.append(f"{field}.link_targets must match skeleton item {item_id}")
    _validate_sources_match_skeleton(value, expected, field=field, item_id=item_id, errors=errors)


def _ordered_source_ids(item: dict[str, Any]) -> list[str]:
    return [
        str(source.get("source_id") or "")
        for source in item.get("sources") or []
        if isinstance(source, dict) and source.get("source_id")
    ]


def _validate_sources_match_skeleton(
    value: dict[str, Any],
    expected: dict[str, Any],
    *,
    field: str,
    item_id: str,
    errors: list[str],
) -> None:
    actual_sources = value.get("sources") or []
    expected_sources = expected.get("sources") or []
    if len(actual_sources) != len(expected_sources):
        errors.append(f"{field}.sources must match skeleton item {item_id}")
        return
    for idx, (actual_source, expected_source) in enumerate(zip(actual_sources, expected_sources)):
        source_field = f"{field}.sources[{idx}]"
        if not isinstance(actual_source, dict) or not isinstance(expected_source, dict):
            errors.append(f"{source_field} must match skeleton source object")
            continue
        expected_source_id = str(expected_source.get("source_id") or "")
        if {str(key) for key in actual_source} == COMPACT_SOURCE_REF_FIELD_SET:
            if str(actual_source.get("source_id") or "") != expected_source_id:
                errors.append(f"{source_field}.source_id must match skeleton source order for item {item_id}")
            continue
        actual_keys = {str(key) for key in actual_source}
        if actual_keys != SOURCE_REF_FIELD_SET:
            errors.append(f"{source_field} must be compact source_id ref or complete skeleton source object")
            continue
        expected_keys = {str(key) for key in expected_source}
        if expected_keys != SOURCE_REF_FIELD_SET:
            errors.append(f"{source_field} cannot expand beyond skeleton source data")
            continue
        if _normalized_source_ref(actual_source) != _normalized_source_ref(expected_source):
            errors.append(f"{source_field} must match skeleton source object for item {item_id}")


def _normalized_source_ref(source: dict[str, Any]) -> dict[str, str]:
    return {field: str(source.get(field) or "") for field in SOURCE_REF_FIELDS}


def _skeleton_item_maps(skeleton: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    maps: dict[str, dict[str, dict[str, Any]]] = {}

    def add(field: str, item: Any) -> None:
        if not isinstance(item, dict):
            return
        item_id = str(item.get("item_id") or "")
        if not item_id:
            return
        maps.setdefault(field, {})[item_id] = item

    if skeleton.get("kind") == "dashboard":
        for field in GROUNDED_DASHBOARD_FIELDS:
            for item in skeleton.get(field) or []:
                add(field, item)
    if skeleton.get("kind") == "npc_panel":
        for item in skeleton.get("people_here") or []:
            add("people_here", item)
        conversation = skeleton.get("conversation") if isinstance(skeleton.get("conversation"), dict) else {}
        for item in conversation.get("likely_subjects") or []:
            add("conversation.likely_subjects", item)
        for item in conversation.get("sensitive") or []:
            add("conversation.sensitive", item)
        tonight = skeleton.get("tonight") if isinstance(skeleton.get("tonight"), dict) else {}
        add("tonight.current_desire", tonight.get("current_desire"))
        add("interesting_detail", skeleton.get("interesting_detail"))
    return maps


def _validate_skeleton_social_map(value: Any, *, skeleton: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(value, list):
        return
    expected_groups = {
        tuple(item.get("npc_ids") or []): item
        for item in skeleton.get("room_groups") or skeleton.get("social_map") or []
        if isinstance(item, dict)
    }
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        npc_ids = tuple(item.get("npc_ids") or item.get("characters") or [])
        if npc_ids not in expected_groups:
            errors.append(f"social_map[{idx}].npc_ids must match a skeleton room group")


def _validate_grounded_item(value: Any, *, field: str, source_ids: set[str], errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be omitted or be an object with sources[]")
        return
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{field}.sources must be a non-empty list")
        return
    for source in sources:
        if not isinstance(source, dict) or source.get("source_id") not in source_ids:
            errors.append(f"{field} uses unknown source_id {source.get('source_id') if isinstance(source, dict) else source!r}")


def _grounded_item_from_fact(fact: ClubFact, *, characters: list[str], mentioned_npc_ids: list[str] | None = None) -> dict[str, Any]:
    source_npc_id = str(characters[0]) if characters else ""
    target_npc_id = None if fact.fact_type == "rumor" else (str(characters[1]) if len(characters) > 1 else "")
    item = {
        "type": fact.fact_type,
        "characters": characters,
        "source_npc_id": source_npc_id,
        "target_npc_id": target_npc_id,
        "mentioned_npc_ids": [str(npc_id) for npc_id in mentioned_npc_ids or []],
        "fact_scope": "relationship" if target_npc_id else ("mention" if mentioned_npc_ids else "self"),
        "summary": clean_display_text(fact.summary),
        "sources": [source.to_dict() for source in fact.sources],
    }
    if fact.link_targets:
        item["link_targets"] = [clean_display_text(target) for target in fact.link_targets if clean_display_text(target)]
    item["display_label"] = _display_label_for_item(item)
    return item


__all__ = [
    "CLUB_DASHBOARD_AI_REQUEST_VERSION",
    "CLUB_DASHBOARD_PROMPT_VERSION",
    "CLUB_DASHBOARD_PROMPT_TOKEN_BUDGET",
    "CLUB_DASHBOARD_SCHEMA_VERSION",
    "CLUB_PANEL_AI_REQUEST_VERSION",
    "NPC_PANEL_PRESENTATION_TEXT_LIMIT",
    "CLUB_PANEL_PROMPT_TOKEN_BUDGET",
    "CLUB_PANEL_PROMPT_VERSION",
    "CLUB_PANEL_SCHEMA_VERSION",
    "CLUB_RUMOR_MATERIALIZATION_VERSION",
    "ClubGenerationError",
    "ClubPromptTooLargeError",
    "ClubGenerationService",
    "ClubBuildResult",
    "club_cache_owner_versions",
    "build_npc_quick_panel",
    "build_attendee_context",
    "build_dashboard_skeleton",
    "build_npc_panel_context",
    "build_npc_panel_skeleton",
    "build_source_map",
    "clean_display_text",
    "cluster_social_map",
    "dashboard_prompt",
    "dashboard_prompt_context",
    "dedupe_display_strings",
    "default_rumor_limit",
    "normalize_rumor_limit",
    "dedupe_grounded_facts",
    "deterministic_dashboard",
    "deterministic_npc_panel",
    "dashboard_from_skeleton",
    "estimate_prompt_tokens",
    "grounded_fact_key",
    "is_dashboard_pressure",
    "is_relevant_to_clicked_npc",
    "npc_panel_prompt",
    "npc_panel_from_skeleton",
    "postprocess_npc_presentation",
    "rank_connections",
    "relationship_digest_for",
    "safe_debug_data",
    "safe_debug_json",
    "select_late_arrival",
    "validate_dashboard",
    "validate_npc_panel",
]
