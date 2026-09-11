import unittest

try:
    from ut_messenger.client.models import Contact
    from ut_messenger.client.state import MessengerState
except ImportError:
    raise unittest.SkipTest("ut_messenger package not installed")


def test_state_tracks_navigation_and_quick_switch_entries():
    state = MessengerState(user_id="artist")
    state.upsert_contact(Contact("alice", "Alice", "Comp", "online", "", 0))
    state.upsert_contact(Contact("bob", "Bob", "FX", "online", "", 0))
    state.upsert_contact(Contact("System", "System", "Notifications", "online", "", 0))

    state.touch_recent("alice")
    state.touch_recent("bob")
    state.toggle_pinned("alice")
    state.set_current_chat("bob", None)

    entries = state.quick_switch_entries(max_entries=8)
    assert entries == ["alice"]


def test_state_registers_and_removes_conversation_mapping():
    state = MessengerState(user_id="artist")
    state.upsert_contact(Contact("alice", "Alice", "Comp", "online", "", 0))
    state.register_conversation_contact("alice", 101)

    assert state.contact_conversation_ids["alice"] == 101
    assert state.conversation_to_contact[101] == "alice"

    state.remove_contact("alice")
    assert "alice" not in state.contacts
    assert 101 not in state.conversation_to_contact


def test_state_unread_and_message_append():
    state = MessengerState(user_id="artist")
    state.upsert_contact(Contact("alice", "Alice", "Comp", "online", "", 0))

    state.append_message("alice", {"id": 1, "content": "hi"})
    state.append_message("alice", {"id": 2, "content": "there"})
    assert len(state.conversations["alice"]) == 2

    assert state.increment_unread("alice", 2) == 2
    state.mark_contact_read("alice")
    assert state.contacts["alice"].unread == 0
