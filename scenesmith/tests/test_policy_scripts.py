from __future__ import annotations

from tools.check_dependency_review import check_paths as check_dependency_paths
from tools.check_schema_version_bump import check_paths as check_schema_paths


def test_dependency_review_requires_review_doc_for_manifest_change() -> None:
    errors = check_dependency_paths(["scenesmith/requirements.txt"])

    assert errors
    assert "Dependency manifests changed" in errors[0]


def test_dependency_review_allows_manifest_with_review_doc() -> None:
    errors = check_dependency_paths([
        "scenesmith/requirements.txt",
        "scenesmith/docs/DEPENDENCY_SECURITY.md",
    ])

    assert errors == []


def test_schema_check_path_only_allows_matching_version_owner() -> None:
    errors = check_schema_paths([
        "scenesmith/core/club_models.py",
        "scenesmith/core/club_generation.py",
    ])

    assert errors == []


def test_schema_check_with_diff_requires_version_constant_change() -> None:
    diff_text = """diff --git a/scenesmith/core/club_generation.py b/scenesmith/core/club_generation.py
--- a/scenesmith/core/club_generation.py
+++ b/scenesmith/core/club_generation.py
@@ -100 +100 @@
-old implementation
+new implementation
"""

    errors = check_schema_paths([
        "scenesmith/core/club_models.py",
        "scenesmith/core/club_generation.py",
    ], diff_text=diff_text)

    assert errors
    assert "club dashboard/event/panel cache" in errors[0]


def test_schema_check_with_diff_accepts_version_constant_change() -> None:
    diff_text = """diff --git a/scenesmith/core/club_generation.py b/scenesmith/core/club_generation.py
--- a/scenesmith/core/club_generation.py
+++ b/scenesmith/core/club_generation.py
@@ -18 +18 @@
-CLUB_DASHBOARD_SCHEMA_VERSION = "club_dashboard_v5"
+CLUB_DASHBOARD_SCHEMA_VERSION = "club_dashboard_v6"
"""

    errors = check_schema_paths([
        "scenesmith/core/club_models.py",
        "scenesmith/core/club_generation.py",
    ], diff_text=diff_text)

    assert errors == []


def test_prep_prompt_diff_accepts_narrow_encounter_cue_version_owner() -> None:
    diff_text = """diff --git a/scenesmith/core/club_prep.py b/scenesmith/core/club_prep.py
--- a/scenesmith/core/club_prep.py
+++ b/scenesmith/core/club_prep.py
@@ -19 +19 @@
-CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION = "club_prep_encounter_cue_v1"
+CLUB_PREP_ENCOUNTER_CUE_SCHEMA_VERSION = "club_prep_encounter_cue_v2"
"""

    errors = check_schema_paths([
        "scenesmith/core/club_prep.py",
        "scenesmith/templates/club_prep.j2",
    ], diff_text=diff_text)

    assert errors == []


def test_prep_prompt_diff_accepts_narrow_npc_conversation_version_owner() -> None:
    diff_text = """diff --git a/scenesmith/core/club_prep.py b/scenesmith/core/club_prep.py
--- a/scenesmith/core/club_prep.py
+++ b/scenesmith/core/club_prep.py
@@ -20 +20 @@
-CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION = "club_prep_npc_conversation_v1"
+CLUB_PREP_NPC_CONVERSATION_SCHEMA_VERSION = "club_prep_npc_conversation_v2"
"""

    errors = check_schema_paths([
        "scenesmith/core/club_prep.py",
        "scenesmith/templates/club_prep.j2",
    ], diff_text=diff_text)

    assert errors == []


def test_prep_prompt_diff_accepts_narrow_rumor_guidance_version_owner() -> None:
    diff_text = """diff --git a/scenesmith/core/club_prep.py b/scenesmith/core/club_prep.py
--- a/scenesmith/core/club_prep.py
+++ b/scenesmith/core/club_prep.py
@@ -21 +21 @@
-CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION = "club_prep_rumor_guidance_v1"
+CLUB_PREP_RUMOR_GUIDANCE_SCHEMA_VERSION = "club_prep_rumor_guidance_v2"
"""

    errors = check_schema_paths([
        "scenesmith/core/club_prep.py",
        "scenesmith/templates/club_prep.j2",
    ], diff_text=diff_text)

    assert errors == []
