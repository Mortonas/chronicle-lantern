from __future__ import annotations

from pathlib import Path

from core.tag_index import TagSummary
from ui.tag_browser_tab import TagBrowserTab


def test_tag_browser_populates_tags_and_files(qapp, tmp_path: Path) -> None:
    tab = TagBrowserTab()
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    summaries = [
        TagSummary("scholars", 2, (first, second)),
        TagSummary("lore", 1, (second,)),
    ]

    tab._on_scan_done(summaries)
    tab.tag_table.selectRow(0)

    assert tab.tag_table.item(0, 0).text() == "#scholars"
    assert tab.tag_table.item(0, 1).text() == "2"
    assert tab.file_table.rowCount() == 2
    assert tab.file_table.item(0, 0).text() == "first.md"


def test_tag_browser_filters_tags(qapp) -> None:
    tab = TagBrowserTab()
    tab._on_scan_done(
        [
            TagSummary("scholars", 2, tuple()),
            TagSummary("newcomer", 1, tuple()),
        ]
    )

    tab.filter_edit.setText("new")

    assert tab.tag_table.rowCount() == 1
    assert tab.tag_table.item(0, 0).text() == "#newcomer"


def test_tag_browser_emits_selected_tag_for_insert(qapp) -> None:
    tab = TagBrowserTab()
    emitted: list[str] = []
    tab.addGuestTagsRequested.connect(emitted.append)
    tab._on_scan_done([TagSummary("scholars", 1, tuple())])

    tab.tag_table.selectRow(0)
    tab.add_guest_tags_btn.click()

    assert emitted == ["#scholars"]


def test_tag_browser_emits_count_sorted_autocomplete_tags(qapp) -> None:
    tab = TagBrowserTab()
    emitted: list[list[str]] = []
    tab.tagIndexUpdated.connect(emitted.append)

    tab._on_scan_done(
        [
            TagSummary("scholars", 3, tuple()),
            TagSummary("lore", 2, tuple()),
            TagSummary("newcomer", 1, tuple()),
        ]
    )

    assert emitted == [["#scholars", "#lore", "#newcomer"]]


def test_tag_browser_autocomplete_scan_limits_to_top_20(qapp) -> None:
    tab = TagBrowserTab()
    emitted: list[list[str]] = []
    tab.tagIndexUpdated.connect(emitted.append)
    summaries = [
        TagSummary(f"tag{index:02}", 25 - index, tuple())
        for index in range(25)
    ]

    tab._on_autocomplete_scan_done(summaries, 20)

    assert len(emitted[0]) == 20
    assert emitted[0][0] == "#tag00"
    assert emitted[0][-1] == "#tag19"


def test_main_window_tag_insertions_avoid_duplicates(qapp) -> None:
    from app import MainWindow

    window = MainWindow()
    try:
        window._add_tag_to_scene_group("#Scholars")
        window._add_tag_to_scene_group("#scholars")
        window._append_unique_tag_text(window.guest_tab.tagsEdit, "#Scholars")
        window._append_unique_tag_text(window.guest_tab.tagsEdit, "#scholars")
        window._append_unique_tag_text(window.guest_tab.anchorEdit, "council")

        assert window.group_rows[0][1].text() == "Scholars"
        assert window.guest_tab.tagsEdit.text() == "Scholars"
        assert window.guest_tab.anchorEdit.text() == "council"
    finally:
        window.close()
