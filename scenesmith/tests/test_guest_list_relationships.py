from __future__ import annotations

from pathlib import Path

import app.guest_list as guest_list
from app.guest_list import generate_guest_list_v2


def _write(path: Path, text: str = "") -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_random_mode_preserves_blank_reason(monkeypatch, tmp_path: Path) -> None:
    a = _write(tmp_path / "A.md")
    b = _write(tmp_path / "B.md")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: {a, b})

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["council"],
        free_text="",
        count=1,
        shuffle_seed=1,
        mode="random",
    )

    assert len(picks) == 1
    assert picks[0].why_picked == ""


def test_extra_tags_accept_bare_words_without_hash(monkeypatch, tmp_path: Path) -> None:
    independent = _write(tmp_path / "Independent.md")
    seen_tags: list[str] = []

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        seen_tags.append(base_tags[-1])
        return {independent} if base_tags[-1] == "independent" else set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="Independent Newcomer",
        count=1,
        shuffle_seed=1,
        mode="random",
    )

    assert picks[0].file_path == independent
    assert seen_tags[:2] == ["independent", "newcomer"]


def test_guest_list_excludes_template_notes_even_with_npc_tag(monkeypatch, tmp_path: Path) -> None:
    npc = _write(tmp_path / "Maldavis.md")
    template = _write(tmp_path / "character template.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "independent"]:
            return {npc, template}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["independent"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="random",
    )

    assert [pick.file_path for pick in picks] == [npc]


def test_host_is_first_and_always_marked_host(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "#court")
    other = _write(tmp_path / "Other.md", "#court")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: {host, other})

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="cohesive",
        host_file=host,
    )

    assert picks[0].file_path == host
    assert picks[0].why_picked == "Host"
    assert picks[0].why_picked != "Seed: #court"


def test_forced_guests_are_included_deduplicated_and_raise_effective_count(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md")
    forced = _write(tmp_path / "Forced.md")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: set())

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=1,
        mode="random",
        host_file=host,
        forced_files=[host, forced, forced],
    )

    assert [(pick.file_path, pick.why_picked) for pick in picks] == [
        (host, "Host"),
        (forced, "Selected guest"),
    ]


def test_missing_host_and_forced_files_are_skipped(monkeypatch, tmp_path: Path) -> None:
    valid = _write(tmp_path / "Valid.md")
    missing = str(tmp_path / "Missing.md")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: set())

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=1,
        mode="random",
        host_file=missing,
        forced_files=[valid],
    )

    assert [(pick.file_path, pick.why_picked) for pick in picks] == [(valid, "Selected guest")]


def test_random_mode_fills_after_host_and_forced_guests(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md")
    forced = _write(tmp_path / "Forced.md")
    fill = _write(tmp_path / "Fill.md")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: {host, forced, fill})

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="random",
        host_file=host,
        forced_files=[forced],
    )

    assert [pick.file_path for pick in picks] == [host, forced, fill]
    assert [pick.why_picked for pick in picks[:2]] == ["Host", "Selected guest"]


def test_must_include_tags_are_added_first_and_raise_count(monkeypatch, tmp_path: Path) -> None:
    gov = _write(tmp_path / "Government.md")
    regular = _write(tmp_path / "Regular.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {gov, regular}
        if base_tags == ["npc", "thegoverment"]:
            return {gov}
        if base_tags == ["npc", "council"]:
            return {gov, regular}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["council"],
        free_text="",
        count=1,
        shuffle_seed=1,
        mode="random",
        must_include_tags=["TheGoverment"],
    )

    assert [(pick.file_path, pick.why_picked) for pick in picks] == [(gov, "Must include: #thegoverment")]


def test_exclude_tags_remove_preset_candidates(monkeypatch, tmp_path: Path) -> None:
    independent = _write(tmp_path / "Independent.md")
    council = _write(tmp_path / "Council.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "independent"]:
            return {independent, council}
        if base_tags == ["council"]:
            return {council}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["independent"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="random",
        exclude_tags=["Council"],
    )

    assert [pick.file_path for pick in picks] == [independent]


def test_relationship_modes_do_not_show_excluded_linked_npcs(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Allowed]]\n- [[Blocked]]\n")
    allowed = _write(tmp_path / "Allowed.md")
    blocked = _write(tmp_path / "Blocked.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, allowed, blocked}
        if base_tags == ["blocked"]:
            return {blocked}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    for mode in ("web", "cohesive"):
        picks = generate_guest_list_v2(
            rg_path="rg",
            vault=str(tmp_path),
            preset_tags=[],
            free_text="",
            count=3,
            shuffle_seed=1,
            mode=mode,
            host_file=host,
            exclude_tags=["Blocked"],
        )

        assert [pick.file_path for pick in picks] == [host, allowed]
        assert blocked not in [pick.file_path for pick in picks]


def test_must_have_tags_constrain_normal_fill(monkeypatch, tmp_path: Path) -> None:
    official = _write(tmp_path / "Official.md")
    outsider = _write(tmp_path / "Outsider.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "independent", "dockworkers"]:
            return {official}
        if base_tags == ["npc", "dockworkers"]:
            return {official, outsider}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["dockworkers"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="random",
        must_have_tags=["Independent"],
    )

    assert [pick.file_path for pick in picks] == [official]


def test_prefer_tags_prioritize_only_eligible_candidates(monkeypatch, tmp_path: Path) -> None:
    local = _write(tmp_path / "Local.md")
    outsider = _write(tmp_path / "Outsider.md")
    local_non_candidate = _write(tmp_path / "Local Non Candidate.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "court"]:
            return {local, outsider}
        if base_tags == ["npc", "riverton"]:
            return {local, local_non_candidate}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="random",
        prefer_tags=["Riverton"],
    )

    assert picks[0].file_path == local
    assert picks[0].why_picked == "Preferred tag match"
    assert local_non_candidate not in [pick.file_path for pick in picks]


def test_random_mode_shuffles_preferred_candidates_reproducibly(monkeypatch, tmp_path: Path) -> None:
    preferred = [_write(tmp_path / f"Preferred {index}.md") for index in range(8)]
    outsider = _write(tmp_path / "Outsider.md")
    preferred_non_candidate = _write(tmp_path / "Preferred Non Candidate.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "court"]:
            return {*preferred, outsider}
        if base_tags == ["npc", "riverton"]:
            return {*preferred, preferred_non_candidate}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    def select(seed: int):
        return generate_guest_list_v2(
            rg_path="rg",
            vault=str(tmp_path),
            preset_tags=["court"],
            free_text="",
            count=3,
            shuffle_seed=seed,
            mode="random",
            prefer_tags=["Riverton"],
        )

    expected = tuple(pick.file_path for pick in select(17))
    select(999)
    assert tuple(pick.file_path for pick in select(17)) == expected

    selections = [select(seed) for seed in range(10)]
    path_selections = [tuple(pick.file_path for pick in picks) for picks in selections]
    assert len(set(path_selections)) > 1
    for picks in selections:
        paths = [pick.file_path for pick in picks]
        assert len(paths) == len(set(paths)) == 3
        assert set(paths) <= set(preferred)
        assert [pick.why_picked for pick in picks] == ["Preferred tag match"] * 3


def test_random_preferred_fill_preserves_manual_and_mandatory_precedence(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md")
    forced = _write(tmp_path / "Forced.md")
    required = _write(tmp_path / "Required.md")
    preferred = [_write(tmp_path / f"Preferred {index}.md") for index in range(6)]
    outsider = _write(tmp_path / "Outsider.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc", "government"]:
            return {required}
        if base_tags == ["npc", "court"]:
            return {*preferred, outsider}
        if base_tags == ["npc", "riverton"]:
            return set(preferred)
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    def select(seed: int):
        return generate_guest_list_v2(
            rg_path="rg",
            vault=str(tmp_path),
            preset_tags=["court"],
            free_text="",
            count=5,
            shuffle_seed=seed,
            mode="random",
            host_file=host,
            forced_files=[forced],
            must_include_tags=["Government"],
            prefer_tags=["Riverton"],
        )

    selections = [select(seed) for seed in range(10)]
    preferred_tails = set()
    for picks in selections:
        assert [(pick.file_path, pick.why_picked) for pick in picks[:3]] == [
            (host, "Host"),
            (forced, "Selected guest"),
            (required, "Must include: #government"),
        ]
        tail = tuple(pick.file_path for pick in picks[3:])
        assert len(tail) == len(set(tail)) == 2
        assert set(tail) <= set(preferred)
        assert [pick.why_picked for pick in picks[3:]] == ["Preferred tag match"] * 2
        preferred_tails.add(tail)
    assert len(preferred_tails) > 1


def test_automatic_eligibility_allows_council_and_exact_named_exceptions(monkeypatch, tmp_path: Path) -> None:
    council = _write(tmp_path / "Council Courtier.md")
    exceptions = [
        _write(tmp_path / "Tyler.md"),
        _write(tmp_path / "Anita Wainwright.md"),
        _write(tmp_path / "Talley.md"),
        _write(tmp_path / "Solon (Prias).md"),
    ]
    independent_government = _write(tmp_path / "Independent Government.md")
    independent_power_player = _write(tmp_path / "Independent Power Player.md")
    all_npcs = {council, *exceptions, independent_government, independent_power_player}

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return all_npcs
        if base_tags == ["npc", "council"]:
            return {council}
        if base_tags == ["npc", "thegovernment"]:
            return {independent_government}
        if base_tags == ["npc", "powerplayer"]:
            return {independent_power_player}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=10,
        shuffle_seed=4,
        eligible_tags=["Council"],
        allow_guests=["Tyler", " Anita   Wainwright ", "Talley", "Solon (Prias)"],
        prefer_tags=["TheGovernment", "PowerPlayer"],
        prefer_guests=["Tyler", "Anita Wainwright", "Talley", "Solon (Prias)"],
    )

    assert {pick.file_path for pick in picks} == {council, *exceptions}
    assert independent_government not in [pick.file_path for pick in picks]
    assert independent_power_player not in [pick.file_path for pick in picks]
    assert {pick.why_picked for pick in picks if pick.file_path in exceptions} == {"Preferred guest"}


def test_named_guest_resolution_failures_and_exclusions_fail_closed(monkeypatch, tmp_path: Path, capsys) -> None:
    valid = _write(tmp_path / "Valid Guest.md")
    excluded = _write(tmp_path / "Excluded Guest.md")
    (tmp_path / "First").mkdir()
    (tmp_path / "Second").mkdir()
    first_ambiguous = _write(tmp_path / "First" / "Duplicate.md")
    second_ambiguous = _write(tmp_path / "Second" / "Duplicate.md")
    non_npc = _write(tmp_path / "Non NPC.md")
    template = _write(tmp_path / "character template.md")
    npc_files = {valid, excluded, first_ambiguous, second_ambiguous, template}

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return npc_files
        if base_tags == ["excluded"]:
            return {excluded}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=10,
        shuffle_seed=2,
        eligible_tags=["Council"],
        allow_guests=[
            "Valid Guest",
            "Excluded Guest",
            "Duplicate",
            "Missing Guest",
            Path(non_npc).stem,
            Path(template).stem,
        ],
        prefer_guests=[
            "Valid Guest",
            "Excluded Guest",
            "Duplicate",
            "Missing Guest",
            Path(non_npc).stem,
            Path(template).stem,
        ],
        exclude_tags=["Excluded"],
    )

    assert [(pick.file_path, pick.why_picked) for pick in picks] == [(valid, "Preferred guest")]
    output = capsys.readouterr().out
    assert "ambiguous named guest 'Duplicate'" in output
    assert "named guest not found or ineligible as an NPC 'Missing Guest'" in output
    assert "named guest not found or ineligible as an NPC 'Non NPC'" in output
    assert "named guest not found or ineligible as an NPC 'character template'" in output


def test_named_exceptions_do_not_bypass_required_tags_and_mandatory_is_eligibility_gated(
    monkeypatch, tmp_path: Path
) -> None:
    eligible_required = _write(tmp_path / "Eligible Required.md")
    exception_required = _write(tmp_path / "Anita Wainwright.md")
    exception_without_required = _write(tmp_path / "Tyler.md")
    ineligible_mandatory = _write(tmp_path / "Independent Government.md")
    all_npcs = {eligible_required, exception_required, exception_without_required, ineligible_mandatory}

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        mapping = {
            ("npc",): all_npcs,
            ("npc", "court"): {eligible_required, exception_required},
            ("npc", "council"): {eligible_required},
            ("npc", "court", "council"): {eligible_required},
            ("npc", "government"): {eligible_required, ineligible_mandatory},
        }
        return mapping.get(tuple(base_tags), set())

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=5,
        shuffle_seed=3,
        must_include_tags=["Government"],
        must_have_tags=["Court"],
        eligible_tags=["Council"],
        allow_guests=["Anita Wainwright", "Tyler"],
        prefer_guests=["Anita Wainwright", "Tyler"],
    )

    assert [pick.file_path for pick in picks] == [eligible_required, exception_required]
    assert picks[0].why_picked == "Must include: #government"
    assert picks[1].why_picked == "Preferred guest"
    assert exception_without_required not in [pick.file_path for pick in picks]
    assert ineligible_mandatory not in [pick.file_path for pick in picks]


def test_named_preferred_pool_is_seeded_reproducible_varied_and_deduplicated(monkeypatch, tmp_path: Path) -> None:
    important = [_write(tmp_path / f"Important {index}.md") for index in range(8)]
    tyler = _write(tmp_path / "Tyler.md")
    all_npcs = {*important, tyler}

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return all_npcs
        if base_tags == ["npc", "council"]:
            return set(important)
        if base_tags == ["npc", "powerplayer"]:
            return set(important)
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    def select(seed: int):
        return generate_guest_list_v2(
            rg_path="rg",
            vault=str(tmp_path),
            preset_tags=["Council"],
            free_text="",
            count=4,
            shuffle_seed=seed,
            eligible_tags=["Council"],
            allow_guests=["Tyler"],
            prefer_tags=["PowerPlayer"],
            prefer_guests=["Tyler", "Important 0"],
        )

    expected = tuple(pick.file_path for pick in select(17))
    select(999)
    assert tuple(pick.file_path for pick in select(17)) == expected
    selections = [select(seed) for seed in range(10)]
    assert len({tuple(pick.file_path for pick in picks) for picks in selections}) > 1
    for picks in selections:
        paths = [pick.file_path for pick in picks]
        assert len(paths) == len(set(paths)) == 4
        assert set(paths) <= all_npcs
        for pick in picks:
            expected_reason = "Preferred guest" if pick.file_path == tyler else "Preferred tag match"
            assert pick.why_picked == expected_reason


def test_web_traverses_noneligible_connector_but_displays_only_eligible_guests(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Connector]]\n")
    connector = _write(tmp_path / "Connector.md", "### Relationships\n- [[Council Guest]]\n")
    guest = _write(tmp_path / "Council Guest.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, connector, guest}
        if base_tags == ["npc", "council"]:
            return {guest}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="web",
        host_file=host,
        eligible_tags=["Council"],
    )

    assert [pick.file_path for pick in picks] == [host, guest]
    assert [pick.why_picked for pick in picks] == ["Host", "Linked via Connector"]


def test_exact_named_exclusion_wins_over_eligibility_preference_and_mandatory_tags(
    monkeypatch, tmp_path: Path
) -> None:
    host = _write(tmp_path / "Host.md")
    helena = _write(tmp_path / "Helena.md")
    courtier = _write(tmp_path / "Courtier.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, helena, courtier}
        if base_tags in (["npc", "council"], ["npc", "government"], ["npc", "powerplayer"]):
            return {helena, courtier}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    automatic = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=3,
        shuffle_seed=1,
        eligible_tags=["Council"],
        allow_guests=["Helena"],
        prefer_tags=["PowerPlayer"],
        prefer_guests=["Helena"],
        must_include_tags=["Government"],
        exclude_guests=["Helena"],
    )
    manual = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=2,
        shuffle_seed=1,
        eligible_tags=["Council"],
        exclude_guests=["Helena"],
        host_file=helena,
    )

    assert [pick.file_path for pick in automatic] == [courtier]
    assert [pick.file_path for pick in manual] == [helena, courtier]
    assert manual[0].why_picked == "Host"


def test_web_can_traverse_exactly_excluded_guest_without_displaying_them(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Helena]]\n")
    helena = _write(tmp_path / "Helena.md", "### Relationships\n- [[Guest]]\n")
    guest = _write(tmp_path / "Guest.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, helena, guest}
        if base_tags == ["npc", "council"]:
            return {helena, guest}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Council"],
        free_text="",
        count=2,
        shuffle_seed=1,
        mode="web",
        host_file=host,
        eligible_tags=["Council"],
        exclude_guests=[" Helena "],
    )

    assert [pick.file_path for pick in picks] == [host, guest]
    assert [pick.why_picked for pick in picks] == ["Host", "Linked via Helena"]


def test_empty_new_eligibility_options_preserve_legacy_seeded_result(monkeypatch, tmp_path: Path) -> None:
    candidates = {_write(tmp_path / f"Candidate {index}.md") for index in range(6)}

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        return candidates if base_tags == ["npc", "court"] else set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    common = dict(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["Court"],
        free_text="",
        count=4,
        shuffle_seed=33,
        mode="random",
    )
    legacy = generate_guest_list_v2(**common)
    explicit_empty = generate_guest_list_v2(
        **common,
        eligible_tags=[],
        allow_guests=[],
        prefer_guests=[],
        exclude_guests=[],
    )

    assert explicit_empty == legacy


def test_cohesive_ranks_by_edges_into_seed_set(monkeypatch, tmp_path: Path) -> None:
    a = _write(tmp_path / "A.md", "### Relationships\n- [[C]]\n- [[D]]\n")
    b = _write(tmp_path / "B.md", "### Relationships\n- [[C]]\n")
    c = _write(tmp_path / "C.md")
    d = _write(tmp_path / "D.md")
    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {a, b, c, d}
        if base_tags == ["npc", "court"]:
            return {a, b}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=4,
        shuffle_seed=1,
        mode="cohesive",
    )

    picked_paths = [pick.file_path for pick in picks]
    assert set(picked_paths[:2]) == {a, b}
    assert picked_paths[2:] == [c, d]
    assert picks[2].why_picked == "Connected cluster"


def test_host_anchors_cohesive_expansion(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Linked]]\n")
    linked = _write(tmp_path / "Linked.md")
    fallback = _write(tmp_path / "Fallback.md")
    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, linked, fallback}
        if base_tags == ["npc", "court"]:
            return {fallback}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="cohesive",
        host_file=host,
    )

    assert [pick.file_path for pick in picks] == [host, linked, fallback]
    assert [pick.why_picked for pick in picks] == ["Host", "Connected cluster", "Fallback tag match"]


def test_host_anchors_web_bfs_expansion(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Middle]]\n")
    middle = _write(tmp_path / "Middle.md", "### Relationships\n- [[End]]\n")
    end = _write(tmp_path / "End.md")
    monkeypatch.setattr(
        guest_list,
        "_candidate_files",
        lambda rg_path, vault, base_tags, groups: {host, middle, end} if base_tags == ["npc"] else set(),
    )

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="web",
        host_file=host,
    )

    assert [pick.file_path for pick in picks] == [host, middle, end]
    assert [pick.why_picked for pick in picks] == ["Host", "Linked to Host", "Linked to Middle"]


def test_web_expansion_labels_hidden_npc_connectors(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Connector]]\n")
    connector = _write(tmp_path / "Connector.md", "### Relationships\n- [[Guest]]\n")
    guest = _write(tmp_path / "Guest.md")

    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {host, connector, guest}
        if base_tags == ["hidden"]:
            return {connector}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="web",
        host_file=host,
        exclude_tags=["Hidden"],
    )

    assert [pick.file_path for pick in picks] == [host, guest]
    assert [pick.why_picked for pick in picks] == ["Host", "Linked via Connector"]


def test_relationship_expansion_skips_linked_non_npc_notes(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Linked NPC]]\n- [[Atrium]]\n")
    linked_npc = _write(tmp_path / "Linked NPC.md")
    location = _write(tmp_path / "Atrium.md")

    monkeypatch.setattr(
        guest_list,
        "_candidate_files",
        lambda rg_path, vault, base_tags, groups: {host, linked_npc} if base_tags == ["npc"] else set(),
    )

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="web",
        host_file=host,
    )

    assert [pick.file_path for pick in picks] == [host, linked_npc]
    assert location not in [pick.file_path for pick in picks]


def test_web_expansion_does_not_traverse_non_npc_or_template_bridges(monkeypatch, tmp_path: Path) -> None:
    host = _write(
        tmp_path / "Host.md",
        "### Relationships\n- [[Location]]\n- [[character template]]\n",
    )
    location = _write(tmp_path / "Location.md", "### Relationships\n- [[Guest]]\n")
    template = _write(tmp_path / "character template.md", "### Relationships\n- [[Other]]\n")
    guest = _write(tmp_path / "Guest.md")
    other = _write(tmp_path / "Other.md")

    monkeypatch.setattr(
        guest_list,
        "_candidate_files",
        lambda rg_path, vault, base_tags, groups: {host, guest, other, template} if base_tags == ["npc"] else set(),
    )

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="web",
        host_file=host,
    )

    assert [pick.file_path for pick in picks] == [host]
    assert location not in [pick.file_path for pick in picks]
    assert template not in [pick.file_path for pick in picks]
    assert guest not in [pick.file_path for pick in picks]
    assert other not in [pick.file_path for pick in picks]


def test_web_expansion_is_depth_limited(monkeypatch, tmp_path: Path) -> None:
    a = _write(tmp_path / "A.md", "### Relationships\n- [[B]]\n")
    b = _write(tmp_path / "B.md", "### Relationships\n- [[C]]\n")
    c = _write(tmp_path / "C.md", "### Relationships\n- [[D]]\n")
    d = _write(tmp_path / "D.md", "### Relationships\n- [[E]]\n")
    e = _write(tmp_path / "E.md")
    def fake_candidate_files(rg_path, vault, base_tags, groups):
        if base_tags == ["npc"]:
            return {a, b, c, d, e}
        if base_tags == ["npc", "seed"]:
            return {a}
        return set()

    monkeypatch.setattr(guest_list, "_candidate_files", fake_candidate_files)

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["seed"],
        free_text="",
        count=5,
        shuffle_seed=1,
        mode="web",
    )

    picked_paths = [pick.file_path for pick in picks]
    assert picked_paths == [a, b, c, d]
    assert e not in picked_paths
    assert guest_list.MAX_WEB_HOPS == 3


def test_relationship_mode_falls_back_to_tag_candidates(monkeypatch, tmp_path: Path) -> None:
    a = _write(tmp_path / "A.md")
    b = _write(tmp_path / "B.md")
    c = _write(tmp_path / "C.md")
    monkeypatch.setattr(guest_list, "_candidate_files", lambda rg_path, vault, base_tags, groups: {a, b, c})

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=["court"],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="cohesive",
    )

    assert len(picks) == 3
    assert len({pick.file_path for pick in picks}) == 3
    assert any(pick.why_picked == "Fallback tag match" for pick in picks)


def test_relationship_ties_are_ordered_by_file_stem(monkeypatch, tmp_path: Path) -> None:
    host = _write(tmp_path / "Host.md", "### Relationships\n- [[Beta]]\n- [[Alpha]]\n")
    beta = _write(tmp_path / "Beta.md")
    alpha = _write(tmp_path / "Alpha.md")
    monkeypatch.setattr(
        guest_list,
        "_candidate_files",
        lambda rg_path, vault, base_tags, groups: {host, alpha, beta} if base_tags == ["npc"] else set(),
    )

    picks = generate_guest_list_v2(
        rg_path="rg",
        vault=str(tmp_path),
        preset_tags=[],
        free_text="",
        count=3,
        shuffle_seed=1,
        mode="cohesive",
        host_file=host,
    )

    assert [pick.file_path for pick in picks] == [host, alpha, beta]
