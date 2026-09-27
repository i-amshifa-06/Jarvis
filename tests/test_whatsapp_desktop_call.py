import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Load the plugin
import importlib.util
plugin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'plugins', 'whatsapp_desktop_call.py'))
spec = importlib.util.spec_from_file_location("whatsapp_desktop_call", plugin_path)
wa_plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wa_plugin)

class TestWhatsAppDesktopCall(unittest.TestCase):
    def test_whatsapp_not_running(self):
        with patch.object(wa_plugin.WhatsAppDesktopController, 'is_whatsapp_running', return_value=False):
            res = wa_plugin.run({"intent": "call", "contact_name": "Arun"})
            self.assertIn("not running", res)

    def test_contact_not_found(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.search_contact.return_value = []
            mock_instance.diagnostic_log = ["UIA working"]
            
            res = wa_plugin.run({"intent": "voice_call", "contact_name": "Ghost"})
            self.assertIn("not found", res)
            mock_instance.open_contact.assert_not_called()

    def test_multiple_matching_contacts(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.search_contact.return_value = ["Arun 1", "Arun 2"]
            
            res = wa_plugin.run({"intent": "voice_call", "contact_name": "Arun"})
            self.assertIn("Multiple contacts found", res)
            mock_instance.open_contact.assert_not_called()

    def test_voice_call_intent_exact_match(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.search_contact.return_value = ["arun"]
            mock_instance.open_contact.return_value = True
            mock_instance.start_voice_call.return_value = "SUCCESS_UNVERIFIED"
            mock_instance.diagnostic_log = ["UIA working"]
            
            res = wa_plugin.run({"intent": "voice_call", "contact_name": "arun"})
            self.assertIn("Voice call button click executed", res)
            mock_instance.open_contact.assert_called_once_with("arun", ["arun"])
            mock_instance.start_voice_call.assert_called_once_with("arun")

    def test_end_call_intent(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.end_call.return_value = True
            
            res = wa_plugin.run({"intent": "end_call"})
            self.assertIn("Successfully executed end_call", res)
            mock_instance.end_call.assert_called_once()

    def test_ambiguous_command(self):
        res = wa_plugin.run({"intent": "random garbage intent"})
        self.assertIn("Ambiguous", res)
        
    def test_tanglish_nlp_mapping(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.search_contact.return_value = ["amma"]
            mock_instance.open_contact.return_value = True
            mock_instance.start_voice_call.return_value = "SUCCESS_VERIFIED"
            mock_instance.diagnostic_log = ["UIA working"]
            
            res = wa_plugin.run({"intent": "whatsapp la amma ku call pannu"})
            self.assertIn("Successfully initiated voice_call", res)
            mock_instance.search_contact.assert_called_once_with("amma")

    def test_search_diagnostic_intent(self):
        with patch.object(wa_plugin, 'WhatsAppDesktopController') as mock_class:
            mock_instance = mock_class.return_value
            mock_instance.is_whatsapp_running.return_value = True
            mock_instance.search_contact.return_value = ["My World"]
            mock_instance.open_contact.return_value = True
            mock_instance.diagnostic_log = ["Layer 2 (Keyboard) used."]
            
            res = wa_plugin.run({"intent": "whatsapp_search_test", "contact_name": "My World"})
            self.assertIn("Diagnostic Search Test Completed", res)
            self.assertIn("Layer 2 (Keyboard) used.", res)
            mock_instance.start_voice_call.assert_not_called()

    def test_inspect_whatsapp_ui_intent(self):
        with patch.object(wa_plugin, 'inspect_whatsapp_ui') as mock_inspect:
            mock_inspect.return_value = {
                "whatsapp_window": {"Name": "WhatsApp", "BoundingRectangle": (0, 0, 1000, 800)},
                "display": {"screen_width": 1920, "screen_height": 1080, "scaling_pct": 100},
                "controls": [{"Name": "Voice call", "ControlType": "ButtonControl"}]
            }
            with patch.object(wa_plugin.WhatsAppDesktopController, 'is_whatsapp_running', return_value=True):
                res = wa_plugin.run({"intent": "inspect_whatsapp_ui"})
                self.assertIn("WhatsApp UI Inspection", res)
                self.assertIn("Discovered Controls: 1 items", res)

    @patch('time.sleep', return_value=None)
    def test_dynamic_relative_fallback_math(self, mock_sleep):
        controller = wa_plugin.WhatsAppDesktopController()
        
        mock_window = MagicMock()
        mock_rect = MagicMock()
        mock_rect.left = 100
        mock_rect.top = 50
        mock_rect.right = 1100 # width = 1000
        mock_rect.bottom = 850 # height = 800
        mock_window.BoundingRectangle = mock_rect
        mock_window.Exists.return_value = True
        
        controller.window = mock_window
        
        # Test window_relative_fallback
        with patch('pyautogui.click') as mock_click:
            res_voice = wa_plugin.window_relative_fallback(mock_window, "voice")
            self.assertTrue(res_voice)
            # target_x = 100 + 1000 * 0.88 = 980
            # target_y = 50 + 800 * 0.055 = 94
            mock_click.assert_called_once_with(980, 94)

            mock_click.reset_mock()
            res_video = wa_plugin.window_relative_fallback(mock_window, "video")
            self.assertTrue(res_video)
            # target_x = 100 + 1000 * 0.82 = 920
            # target_y = 50 + 800 * 0.055 = 94
            mock_click.assert_called_once_with(920, 94)

if __name__ == '__main__':
    unittest.main()
