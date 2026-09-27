import unittest
from unittest.mock import patch, MagicMock
import asyncio
import plugins.whatsapp_monitor as wm


class TestWhatsAppMonitor(unittest.TestCase):
    def setUp(self):
        # Reset state before each test
        wm._monitoring_active = False
        wm._replied_notification_ids.clear()

    def tearDown(self):
        wm._monitoring_active = False

    def test_winrt_or_winsdk_available_on_windows(self):
        """Verify Windows Runtime notification projection is properly detected."""
        self.assertTrue(wm._WINRT_AVAILABLE, "WinRT notification reader should be available.")
        self.assertTrue(wm._WINSDK_AVAILABLE, "Legacy alias _WINSDK_AVAILABLE should also be True.")

    def test_plugin_metadata(self):
        """Verify PLUGIN dict structure conforms to Jarvis plugin spec."""
        self.assertEqual(wm.PLUGIN["name"], "whatsapp_monitor")
        self.assertIn("parameters", wm.PLUGIN)
        self.assertIn("status", wm.PLUGIN["parameters"]["properties"])

    def test_run_invalid_status(self):
        res = wm.run({"status": "invalid_cmd"})
        self.assertIn("Invalid status", res)

    def test_run_toggle_on_off(self):
        res_on = wm.run({"status": "on"})
        self.assertIn("WhatsApp smart auto-reply enabled", res_on)
        self.assertTrue(wm._monitoring_active)

        # Calling 'on' while already running
        res_on_again = wm.run({"status": "on"})
        self.assertIn("already monitoring", res_on_again)

        # Turning 'off'
        res_off = wm.run({"status": "off"})
        self.assertIn("stopped monitoring", res_off)
        self.assertFalse(wm._monitoring_active)

        # Turning 'off' again
        res_off_again = wm.run({"status": "off"})
        self.assertIn("already off", res_off_again)

    def test_generate_dynamic_reply_fallback(self):
        """Ensure fallback response works gracefully without API keys."""
        reply = wm._generate_dynamic_reply("Alice", "Hello")
        self.assertIsInstance(reply, str)
        self.assertTrue(len(reply) > 0)

    @patch.object(wm, "UserNotificationListener")
    def test_get_whatsapp_notifications_mocked(self, mock_listener_cls):
        mock_listener = MagicMock()
        mock_listener_cls.current = mock_listener

        # Mock notif object
        mock_notif = MagicMock()
        mock_notif.id = 101
        mock_notif.app_info.display_info.display_name = "WhatsApp"
        
        mock_binding = MagicMock()
        mock_text1 = MagicMock()
        mock_text1.text = "Bob"
        mock_text2 = MagicMock()
        mock_text2.text = "Hey, are you free?"
        mock_binding.get_text_elements.return_value = [mock_text1, mock_text2]
        mock_notif.notification.visual.bindings = [mock_binding]

        async def fake_request_access():
            return 1

        async def fake_get_notifications(kind):
            return [mock_notif]

        mock_listener.request_access_async.side_effect = fake_request_access
        mock_listener.get_notifications_async.side_effect = fake_get_notifications

        results = asyncio.run(wm._get_whatsapp_notifications())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 101)
        self.assertEqual(results[0]["sender"], "Bob")
        self.assertEqual(results[0]["message"], "Hey, are you free?")


if __name__ == "__main__":
    unittest.main()
