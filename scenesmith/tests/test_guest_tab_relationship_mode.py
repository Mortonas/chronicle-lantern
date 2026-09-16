from __future__ import annotations

from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHeaderView

from app.guest_list import GuestPick
from ui.guest_tab import GuestTab, NpcPickerDialog


def test_public_guest_presets_are_generic_and_have_no_private_aliases() -> None:
    preset_path = Path(__file__).resolve().parents[1] / "config" / "guest_presets.yaml"
    presets = yaml.safe_load(preset_path.read_text(encoding="utf-8"))["presets"]

    assert set(presets) == {
        "Community Gathering", "Professional Reception", "Night Market", "Private Salon", "Open Social"
    }
    assert presets["Community Gathering"]["choose_from"] == ["community"]
    assert presets["Private Salon"]["must_have"] == ["invited"]
    assert yaml.safe_load(preset_path.read_text(encoding="utf-8"))["display_names"] == {}


def test_guest_tab_emits_mode(qapp) -> None:
    tab = GuestTab({})
    payloads: list[dict] = []
    tab.generateRequested.connect(payloads.append)
    tab.tagsEdit.setText("council")
    tab.modeCombo.setCurrentIndex(tab.modeCombo.findData("web"))

    tab._on_generate()

    assert payloads[0]["mode"] == "web"


def test_guest_tab_extra_tags_placeholder_uses_bare_tags(qapp) -> None:
    tab = GuestTab({})

    assert tab.tagsEdit.placeholderText() == "faction scholar outsider"


def test_guest_tab_emits_preset_rule_fields(qapp) -> None:
    tab = GuestTab(
        {
            "Official": {
                "choose_from": ["Independent"],
                "must_include": ["IndependentCenter"],
                "must_have": ["Independent"],
                "prefer": ["Riverton"],
                "exclude": ["Council"],
                "eligible_tags": ["Council"],
                "allow_guests": ["Tyler"],
                "prefer_guests": ["Tyler"],
                "exclude_guests": ["Helena"],
            }
        }
    )
    payloads: list[dict] = []
    tab.generateRequested.connect(payloads.append)
    tab.presetCombo.setCurrentIndex(tab.presetCombo.findData("Official"))

    tab._on_generate()

    assert payloads[0]["preset_tags"] == ["Independent"]
    assert payloads[0]["must_include_tags"] == ["IndependentCenter"]
    assert payloads[0]["must_have_tags"] == ["Independent"]
    assert payloads[0]["prefer_tags"] == ["Riverton"]
    assert payloads[0]["exclude_tags"] == ["Council"]
    assert payloads[0]["eligible_tags"] == ["Council"]
    assert payloads[0]["allow_guests"] == ["Tyler"]
    assert payloads[0]["prefer_guests"] == ["Tyler"]
    assert payloads[0]["exclude_guests"] == ["Helena"]


def test_guest_list_surfaces_display_configured_public_name(qapp, tmp_path) -> None:
    helena = tmp_path / "Helena.md"
    helena.write_text("#NPC #council", encoding="utf-8")
    display_names = {"helena": "Portia"}
    tab = GuestTab({}, display_names=display_names)

    tab.set_host_file(str(helena))
    tab.add_forced_files([str(helena)])
    tab.show_results([GuestPick(file_path=str(helena), single_tag="council", anchor_tags=())])
    picker = NpcPickerDialog(str(tmp_path), mode="guest", display_names=display_names)
    try:
        assert tab.hostLabel.text() == "Portia"
        assert tab.selectedGuestTable.item(0, 0).text() == "Portia"
        assert tab.table.item(0, 1).text() == "Portia"
        assert picker.table.item(0, 0).text() == "Portia"
        assert tab.table.item(0, 2).text() == str(helena)
        assert picker.table.item(0, 1).text() == str(helena)
    finally:
        picker.close()
        tab.close()


def test_guest_tab_emits_host_and_forced_files(qapp, tmp_path) -> None:
    host = tmp_path / "Host.md"
    guest = tmp_path / "Guest.md"
    host.write_text("", encoding="utf-8")
    guest.write_text("", encoding="utf-8")
    tab = GuestTab({})
    payloads: list[dict] = []
    tab.generateRequested.connect(payloads.append)

    tab.set_host_file(str(host))
    tab.add_forced_files([str(guest)])
    tab._on_generate()

    assert payloads[0]["host_file"] == str(host)
    assert payloads[0]["forced_files"] == [str(guest)]
    assert tab.hostLabel.text() == "Host"


def test_guest_tab_selected_guest_table_add_remove_clear(qapp, tmp_path) -> None:
    first = tmp_path / "First.md"
    second = tmp_path / "Second.md"
    for path in (first, second):
        path.write_text("", encoding="utf-8")
    tab = GuestTab({})

    tab.add_forced_files([str(first), str(first), str(second)])
    assert tab.selectedGuestTable.rowCount() == 2
    assert tab._forced_files == [str(first), str(second)]

    tab.selectedGuestTable.selectRow(0)
    tab.remove_selected_guests()
    assert tab._forced_files == [str(second)]

    tab.clear_selected_guests()
    assert tab._forced_files == []
    assert tab.selectedGuestTable.rowCount() == 0


def test_npc_picker_dialog_shared_modes_and_filter(qapp, tmp_path) -> None:
    alpha = tmp_path / "Alpha.md"
    beta = tmp_path / "Beta.md"
    non_npc = tmp_path / "Independent Location.md"
    template = tmp_path / "character template.md"
    alpha.write_text("#NPC #council", encoding="utf-8")
    beta.write_text("#NPC #independent", encoding="utf-8")
    non_npc.write_text("#independent", encoding="utf-8")
    template.write_text("#NPC #independent", encoding="utf-8")

    host_dialog = NpcPickerDialog(str(tmp_path), mode="host")
    guest_dialog = NpcPickerDialog(str(tmp_path), mode="guest")
    try:
        assert host_dialog.mode == "host"
        assert guest_dialog.mode == "guest"

        guest_dialog.searchEdit.setText("independent")
        assert guest_dialog.table.rowCount() == 1
        assert guest_dialog.table.item(0, 0).text() == "Beta"
    finally:
        host_dialog.close()
        guest_dialog.close()


def test_npc_picker_dialog_shows_and_copies_portrait(qapp, tmp_path) -> None:
    art_dir = tmp_path / "Assets" / "Character art"
    art_dir.mkdir(parents=True)
    image = art_dir / "portrait.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.red)
    assert pixmap.save(str(image))

    npc = tmp_path / "Maldavis.md"
    npc.write_text("![[portrait.png]]\n#NPC #independent\n", encoding="utf-8")
    dialog = NpcPickerDialog(str(tmp_path), mode="guest")
    try:
        dialog.table.selectRow(0)

        assert dialog._current_portrait_path == str(image)
        assert dialog.copyImageBtn.isEnabled()

        dialog.copy_selected_image()
        mime = qapp.clipboard().mimeData()
        assert mime.hasImage()
        assert mime.hasUrls()
        assert Path(mime.urls()[0].toLocalFile()) == image
        qapp.clipboard().clear()
    finally:
        dialog.close()


def test_guest_tab_renders_why_column(qapp) -> None:
    tab = GuestTab({})

    tab.show_results(
        [
            GuestPick(
                file_path="C:/vault/Maldavis.md",
                single_tag="council",
                anchor_tags=("riverton",),
                why_picked="Linked to Bobby Weatherbottom",
            )
        ]
    )

    assert tab.table.columnCount() == 6
    assert tab.table.item(0, 5).text() == "Linked to Bobby Weatherbottom"


def test_guest_tab_result_columns_keep_why_readable(qapp) -> None:
    tab = GuestTab({})
    try:
        header = tab.table.horizontalHeader()

        assert header.sectionResizeMode(0) == QHeaderView.Fixed
        assert header.sectionResizeMode(1) == QHeaderView.Interactive
        assert header.sectionResizeMode(2) == QHeaderView.Interactive
        assert header.sectionResizeMode(5) == QHeaderView.Stretch
        assert tab.table.columnWidth(0) == 64
    finally:
        tab.close()


def test_guest_tab_result_portrait_can_be_copied(qapp, tmp_path) -> None:
    art_dir = tmp_path / "Assets" / "Character art"
    art_dir.mkdir(parents=True)
    image = art_dir / "portrait.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.blue)
    assert pixmap.save(str(image))

    npc = tmp_path / "Maldavis.md"
    npc.write_text("![[portrait.png]]\n#independent\n", encoding="utf-8")
    tab = GuestTab({})
    tab._vault_path = str(tmp_path)
    try:
        tab.show_results([GuestPick(file_path=str(npc), single_tag="independent", anchor_tags=())])
        tab.table.selectRow(0)
        tab.copy_selected_result_portrait()

        mime = qapp.clipboard().mimeData()
        assert mime.hasImage()
        assert Path(mime.urls()[0].toLocalFile()) == image
        qapp.clipboard().clear()
    finally:
        tab.close()
