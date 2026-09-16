from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QTabWidget, QWidget

from .ai_rewrite_tab import AiRewriteTab
from .campaign_tab import CampaignTab
from .tag_browser_tab import TagBrowserTab


def ensure_tag_browser_tab(
    tab_widget: QTabWidget,
    parent: Optional[QWidget] = None,
    config: Optional[dict] = None,
) -> TagBrowserTab:
    """Ensure the Tag Browser tab exists on the provided tab widget."""
    label = "Tag Browser"
    for index in range(tab_widget.count()):
        if tab_widget.tabText(index) == label:
            widget = tab_widget.widget(index)
            if isinstance(widget, TagBrowserTab):
                if config is not None:
                    widget.set_vault(config.get("vault_path"), config.get("vault_name"))
                return widget
    tab = TagBrowserTab(parent=parent)
    if config is not None:
        tab.set_vault(config.get("vault_path"), config.get("vault_name"))
    tab_widget.addTab(tab, label)
    return tab


def ensure_ai_rewrite_tab(
    tab_widget: QTabWidget,
    parent: Optional[QWidget] = None,
    config: Optional[dict] = None,
    config_path: Optional[str] = None,
    guest_presets: Optional[dict[str, dict[str, list[str]]]] = None,
) -> AiRewriteTab:
    """Ensure the AI Rewrite tab exists on the provided tab widget."""
    label = "AI Rewrite"
    for index in range(tab_widget.count()):
        if tab_widget.tabText(index) == label:
            widget = tab_widget.widget(index)
            if isinstance(widget, AiRewriteTab):
                if config is not None:
                    widget.set_app_config(config, config_path=config_path)
                if guest_presets is not None:
                    widget.set_guest_presets(guest_presets)
                return widget
    tab = AiRewriteTab(parent=parent)
    if config is not None:
        tab.set_app_config(config, config_path=config_path)
    if guest_presets is not None:
        tab.set_guest_presets(guest_presets)
    tab_widget.addTab(tab, label)
    return tab


def ensure_campaign_tab(
    tab_widget: QTabWidget,
    parent: Optional[QWidget] = None,
    state_path: Optional[str] = None,
) -> CampaignTab:
    """Ensure the Campaign tab exists on the provided tab widget."""
    label = "Campaign"
    for index in range(tab_widget.count()):
        if tab_widget.tabText(index).rstrip(" *") == label:
            widget = tab_widget.widget(index)
            if isinstance(widget, CampaignTab):
                return widget
    tab = CampaignTab(state_path=state_path, parent=parent)
    tab_widget.addTab(tab, label)
    return tab
