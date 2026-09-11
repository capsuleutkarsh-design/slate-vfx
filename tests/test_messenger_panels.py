import unittest
from PySide6.QtWidgets import QLabel, QPushButton

try:
    from ut_messenger.client.models import Contact
    from ut_messenger.client.panels.chat_panel import ChatPanel
    from ut_messenger.client.panels.contacts_panel import ContactsPanel
except ImportError:
    raise unittest.SkipTest("ut_messenger package not installed")


def _round_btn(_icon_name: str) -> QPushButton:
    return QPushButton()


def _small_btn(_icon_name: str) -> QPushButton:
    return QPushButton()


def test_contacts_panel_signal_and_filter_render(qtbot):
    panel = ContactsPanel()
    qtbot.addWidget(panel)

    alice = Contact("alice", "Alice", "Compositing", "online", "", 0)
    bob = Contact("bob", "Bob", "Lighting", "invisible", "", 0)

    panel.upsert_contact_card(alice)
    panel.upsert_contact_card(bob)

    selected = []
    panel.contact_selected_signal.connect(selected.append)
    panel.contact_cards["alice"].clicked.emit("alice")
    assert selected == ["alice"]

    panel.apply_contact_filters(
        text="alice",
        contacts={"alice": alice, "bob": bob},
        contact_meta={
            "alice": {"taxonomy": "direct", "department": "comp"},
            "bob": {"taxonomy": "direct", "department": "light"},
        },
        filter_mode="all",
        taxonomy_mode="all",
        recent_contacts=["alice"],
        current_chat_partner=None,
        user_department="comp",
    )

    assert not panel.contact_cards["alice"].isHidden()
    assert panel.contact_cards["bob"].isHidden()


def test_chat_panel_transcript_and_composer_contract(qtbot):
    panel = ChatPanel(_round_btn, _small_btn)
    qtbot.addWidget(panel)

    messages = [
        {"id": 1, "day": "2026-01-01"},
        {"id": 2, "day": "2026-01-01"},
        {"id": 3, "day": "2026-01-02"},
    ]

    panel.render_transcript(
        messages,
        bubble_builder=lambda msg, show_header, is_consecutive: QLabel(f"msg:{msg['id']}"),
        day_resolver=lambda msg: msg["day"],
        separator_builder=lambda day: QLabel(f"sep:{day}"),
    )
    assert panel.chat_messages_layout.count() == 5

    panel.set_composer_state(enabled=False, send_enabled=False, visible=False)
    assert not panel.line_edit_message.isEnabled()
    assert not panel.btn_send_message.isEnabled()
    assert panel.chat_bottom.isHidden()

    panel.show_new_messages_banner(3)
    assert not panel.new_messages_btn.isHidden()
    panel.hide_new_messages_banner()
    assert panel.new_messages_btn.isHidden()
