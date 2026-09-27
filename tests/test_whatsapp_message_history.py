import unittest
import tempfile
import sqlite3
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.whatsapp_history.models import WhatsAppMessage, WorkSession
from core.whatsapp_history.storage import (
    WhatsAppStorage,
    compute_fingerprint,
    detect_message_type
)
from core.whatsapp_history.query import (
    WhatsAppQueryEngine,
    parse_time_filter,
    format_time_friendly,
    format_card_view
)
import plugins.whatsapp_message_history as wmh
from core.plugin_loader import discover_plugins


class TestWhatsAppMessageHistory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test_whatsapp.db"
        self.storage = WhatsAppStorage(db_path=self.db_path)
        self.query_engine = WhatsAppQueryEngine(storage=self.storage)
        
        # Patch global storage and query engine in plugin module for testing
        wmh._storage = self.storage
        wmh._query_engine = self.query_engine
        wmh._history_active = True

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. Plugin loads via plugin discovery
    def test_plugin_loads(self):
        plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
        registry = discover_plugins(plugins_dir, core_tool_names=set())
        self.assertTrue(registry.has("whatsapp_message_history"))
        self.assertEqual(wmh.PLUGIN["name"], "whatsapp_message_history")
        self.assertIn("action", wmh.PLUGIN["parameters"]["properties"])

    # 2. Database creation and schema check
    def test_database_creation(self):
        self.assertTrue(self.db_path.exists())
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='whatsapp_messages'")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='work_sessions'")
            self.assertIsNotNone(cursor.fetchone())

    # 3. Message insertion
    def test_message_insertion(self):
        msg = WhatsAppMessage(
            conversation_name="Rahul",
            sender_name="Rahul",
            message_text="Hey, are you free for a call?",
            timestamp=datetime.now().isoformat(),
        )
        inserted, row_id = self.storage.save_message(msg)
        self.assertTrue(inserted)
        self.assertIsNotNone(row_id)
        stats = self.storage.get_stats()
        self.assertEqual(stats["total_messages"], 1)

    # 4. Duplicate prevention
    def test_duplicate_prevention(self):
        ts = "2026-09-24T14:30:00"
        msg1 = WhatsAppMessage(
            conversation_name="Rahul",
            sender_name="Rahul",
            message_text="Call me when you're free",
            timestamp=ts,
        )
        msg2 = WhatsAppMessage(
            conversation_name="Rahul",
            sender_name="Rahul",
            message_text="Call me when you're free",
            timestamp=ts,
        )
        inserted1, row_id1 = self.storage.save_message(msg1)
        inserted2, row_id2 = self.storage.save_message(msg2)
        self.assertTrue(inserted1)
        self.assertFalse(inserted2)
        self.assertIsNone(row_id2)
        self.assertEqual(self.storage.get_stats()["total_messages"], 1)

    # 5. Multiple contacts
    def test_multiple_contacts(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Msg 1", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Arun", "Arun", "Msg 2", "2026-09-24T11:00:00"))
        self.storage.save_message(WhatsAppMessage("Priya", "Priya", "Msg 3", "2026-09-24T12:00:00"))
        stats = self.storage.get_stats()
        self.assertEqual(stats["total_messages"], 3)
        self.assertEqual(stats["distinct_senders"], 3)

    # 6. Latest message query
    def test_latest_message_query(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Old message", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "New message", "2026-09-24T15:18:00"))
        self.storage.save_message(WhatsAppMessage("Arun", "Arun", "Arun's message", "2026-09-24T12:00:00"))

        latest_overall = self.query_engine.get_latest_message()
        self.assertEqual(latest_overall.sender_name, "Rahul")
        self.assertEqual(latest_overall.message_text, "New message")

        latest_arun = self.query_engine.get_latest_message(contact="Arun")
        self.assertEqual(latest_arun.message_text, "Arun's message")

        res_plugin = wmh.run({"action": "latest", "contact": "Rahul"})
        self.assertIn("New message", res_plugin)

    # 7. Contact query
    def test_contact_query(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Bro, are you free?", "2026-09-24T10:12:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Need to discuss project.", "2026-09-24T13:43:00"))
        msgs = self.query_engine.get_messages_by_contact("Rahul")
        self.assertEqual(len(msgs), 2)
        res_plugin = wmh.run({"action": "contact_messages", "contact": "Rahul"})
        self.assertIn("sent 2 messages", res_plugin)

    # 8. Time-range query
    def test_time_range_query(self):
        self.storage.save_message(WhatsAppMessage("User1", "User1", "Morning", "2026-09-24T09:00:00"))
        self.storage.save_message(WhatsAppMessage("User2", "User2", "Afternoon 1", "2026-09-24T13:30:00"))
        self.storage.save_message(WhatsAppMessage("User3", "User3", "Afternoon 2", "2026-09-24T15:45:00"))
        self.storage.save_message(WhatsAppMessage("User4", "User4", "Evening", "2026-09-24T19:00:00"))

        msgs = self.query_engine.get_messages_during_period("2026-09-24T13:00:00", "2026-09-24T16:00:00")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].message_text, "Afternoon 1")
        self.assertEqual(msgs[1].message_text, "Afternoon 2")

    # 9. Count query
    def test_count_query(self):
        today_iso = datetime.now().strftime("%Y-%m-%d")
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M1", f"{today_iso}T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M2", f"{today_iso}T11:00:00"))
        self.storage.save_message(WhatsAppMessage("Arun", "Arun", "M3", f"{today_iso}T12:00:00"))

        count_total = self.query_engine.count_messages()
        count_rahul = self.query_engine.count_messages(contact="Rahul")
        self.assertEqual(count_total, 3)
        self.assertEqual(count_rahul, 2)

        res = wmh.run({"action": "count"})
        self.assertIn("3 WhatsApp messages today", res)

    # 10. Sender query
    def test_sender_query(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M1", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M2", "2026-09-24T11:00:00"))
        self.storage.save_message(WhatsAppMessage("Arun", "Arun", "M3", "2026-09-24T12:00:00"))

        senders = self.query_engine.get_senders()
        self.assertEqual(len(senders), 2)
        self.assertEqual(senders[0]["sender"], "Rahul")
        self.assertEqual(senders[0]["count"], 2)
        self.assertEqual(senders[1]["sender"], "Arun")
        self.assertEqual(senders[1]["count"], 1)

    # 11. Text search
    def test_text_search(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Don't forget the project meeting", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Arun", "Arun", "See you tomorrow for coffee", "2026-09-24T11:00:00"))

        results = self.query_engine.search_messages("project meeting")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].sender_name, "Rahul")

        res_plugin = wmh.run({"action": "search", "query": "coffee"})
        self.assertIn("Found 1 message", res_plugin)

    # 12. Date filtering
    def test_date_filtering(self):
        self.storage.save_message(WhatsAppMessage("User", "User", "Past", "2026-09-20T10:00:00"))
        self.storage.save_message(WhatsAppMessage("User", "User", "Present", "2026-09-24T10:00:00"))

        msgs = self.query_engine.get_messages_during_period("2026-09-24T00:00:00")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].message_text, "Present")

    # 13. Message ordering
    def test_message_ordering(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "First", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Second", "2026-09-24T11:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Third", "2026-09-24T12:00:00"))

        msgs = self.query_engine.get_messages_by_contact("Rahul")
        self.assertEqual([m.message_text for m in msgs], ["First", "Second", "Third"])

    # 14. Retention cleanup
    def test_retention_cleanup(self):
        old_ts = (datetime.now() - timedelta(days=40)).isoformat()
        new_ts = (datetime.now() - timedelta(days=5)).isoformat()

        self.storage.save_message(WhatsAppMessage("User1", "User1", "Old", old_ts))
        self.storage.save_message(WhatsAppMessage("User2", "User2", "New", new_ts))

        self.assertEqual(self.storage.get_stats()["total_messages"], 2)
        deleted = self.storage.enforce_retention(retention_days=30)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.storage.get_stats()["total_messages"], 1)

    # 15. Delete confirmation logic
    def test_delete_confirmation_logic(self):
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M1", "2026-09-24T10:00:00"))
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "M2", "2026-09-24T11:00:00"))

        # Without confirmation
        res_unconfirmed = wmh.run({"action": "delete_contact", "contact": "Rahul", "confirm": False})
        self.assertIn("This will permanently delete 2", res_unconfirmed)
        self.assertEqual(self.storage.get_stats()["total_messages"], 2)

        # With confirmation
        res_confirmed = wmh.run({"action": "delete_contact", "contact": "Rahul", "confirm": True})
        self.assertIn("Successfully deleted 2", res_confirmed)
        self.assertEqual(self.storage.get_stats()["total_messages"], 0)

    # 16. Disabled state
    def test_disabled_state(self):
        wmh.run({"action": "disable"})
        self.assertFalse(wmh._history_active)
        
        # Attempt notification capture while disabled
        stored = wmh.store_notification({"sender": "TestUser", "message": "Ignored message"})
        self.assertFalse(stored)

        # Re-enable
        wmh.run({"action": "enable"})
        self.assertTrue(wmh._history_active)
        stored_after = wmh.store_notification({"sender": "TestUser", "message": "Captured message"})
        self.assertTrue(stored_after)

    # 17. SQL injection protection
    def test_sql_injection_protection(self):
        malicious_input = "' OR '1'='1"
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Safe message", "2026-09-24T10:00:00"))

        results = self.query_engine.search_messages(malicious_input)
        self.assertEqual(len(results), 0)

        contact_results = self.query_engine.get_messages_by_contact(malicious_input)
        self.assertEqual(len(contact_results), 0)

    # 18. Long messages
    def test_long_messages(self):
        long_text = "A" * 5000
        msg = WhatsAppMessage("User", "User", long_text, "2026-09-24T10:00:00")
        inserted, _ = self.storage.save_message(msg)
        self.assertTrue(inserted)
        retrieved = self.query_engine.get_latest_message()
        self.assertEqual(retrieved.message_text, long_text)

    # 19. Special characters
    def test_special_characters(self):
        special_text = "Quotes: \"' ` & < > / \\ % _ ? # $ @"
        msg = WhatsAppMessage("User!@#", "User!@#", special_text, "2026-09-24T10:00:00")
        inserted, _ = self.storage.save_message(msg)
        self.assertTrue(inserted)
        retrieved = self.query_engine.get_latest_message()
        self.assertEqual(retrieved.message_text, special_text)

    # 20. Unicode / Tamil / Hindi / Emoji
    def test_unicode_multilingual_emoji(self):
        tamil_hindi_emoji = "வணக்கம் नमस्ते Hello 🚀🎉🔥"
        msg = WhatsAppMessage("அருண் / राहुल", "அருண் / राहुल", tamil_hindi_emoji, "2026-09-24T10:00:00")
        inserted, _ = self.storage.save_message(msg)
        self.assertTrue(inserted)
        retrieved = self.query_engine.get_latest_message()
        self.assertEqual(retrieved.sender_name, "அருண் / राहुल")
        self.assertEqual(retrieved.message_text, tamil_hindi_emoji)

    # 21. Group message handling
    def test_group_message_handling(self):
        notif = {
            "sender": "Project Group: Rahul",
            "message": "Update on milestone 2",
            "is_group": 1
        }
        stored = wmh.store_notification(notif)
        self.assertTrue(stored)
        latest = self.query_engine.get_latest_message()
        self.assertEqual(latest.is_group, 1)

    # 22. Attachment metadata
    def test_attachment_metadata(self):
        mtype, att = detect_message_type("📷 Photo")
        self.assertEqual(mtype, "image")
        self.assertEqual(att, "image")

        mtype_v, att_v = detect_message_type("🎤 Voice message (0:15)")
        self.assertEqual(mtype_v, "voice")
        self.assertEqual(att_v, "audio")

        mtype_d, att_d = detect_message_type("📄 Project_specs.pdf")
        self.assertEqual(mtype_d, "document")
        self.assertEqual(att_d, "document")

    # 23. Work session / DND tracking
    def test_work_session_tracking(self):
        # Start work session
        res_start = wmh.run({"action": "work_session_start"})
        self.assertIn("Work session started", res_start)

        # Ingest message during work session
        self.storage.save_message(WhatsAppMessage("Rahul", "Rahul", "Work message 1", datetime.now().isoformat()))
        
        # End work session
        res_end = wmh.run({"action": "work_session_end"})
        self.assertIn("Work session ended. You received 1 WhatsApp messages", res_end)

    # 24. UI card formatting and player integration
    def test_ui_card_formatting(self):
        msgs = [
            WhatsAppMessage("Rahul", "Rahul", "Call me when you're free.", "2026-09-24T15:18:00"),
            WhatsAppMessage("Arun", "Arun", "Did you finish the assignment?", "2026-09-24T14:41:00")
        ]
        card = format_card_view(msgs)
        self.assertIn("Rahul", card)
        self.assertIn("Arun", card)
        self.assertIn("Call me when you're free.", card)

        mock_player = MagicMock()
        wmh.run({"action": "latest"}, player=mock_player)
        mock_player.write_log.assert_called()


if __name__ == "__main__":
    unittest.main()
