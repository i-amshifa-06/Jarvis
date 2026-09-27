import unittest
from unittest.mock import patch, MagicMock
import os
import sys

import importlib.util
plugin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'plugins', 'whatsapp_desktop_call.py'))
spec = importlib.util.spec_from_file_location("whatsapp_desktop_call", plugin_path)
wa_plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wa_plugin)

class TestWhatsAppLogic(unittest.TestCase):
    def setUp(self):
        self.sleep_patcher = patch('time.sleep', return_value=None)
        self.sleep_patcher.start()
        self.mock_window = MagicMock()
        self.mock_rect = MagicMock(left=0, top=0, right=1200, bottom=800)
        self.mock_window.BoundingRectangle = self.mock_rect
        self.mock_window.Exists.return_value = True
        self.mock_window.GetChildren.return_value = []
        
        self.win_patcher = patch.object(wa_plugin, 'find_whatsapp_window', return_value=self.mock_window)
        self.win_patcher.start()
        
        self.walk_patcher = patch('uiautomation.WalkControl', return_value=[])
        self.walk_patcher.start()
        
        self.controller = wa_plugin.WhatsAppDesktopController()
        self.controller.window = self.mock_window
        self.controller.find_or_focus_whatsapp = MagicMock(return_value=True)

    def tearDown(self):
        self.walk_patcher.stop()
        self.win_patcher.stop()
        self.sleep_patcher.stop()

    def test_get_element_center_valid(self):
        elem = MagicMock()
        rect = MagicMock(left=100, top=200, right=200, bottom=300)
        elem.BoundingRectangle = rect
        center = wa_plugin.get_element_center(elem)
        self.assertEqual(center, (150, 250))

    def test_get_element_center_invalid(self):
        elem = MagicMock()
        elem.BoundingRectangle = None
        self.assertIsNone(wa_plugin.get_element_center(elem))
        
        # Zero area rect
        elem.BoundingRectangle = MagicMock(left=10, top=10, right=10, bottom=10)
        self.assertIsNone(wa_plugin.get_element_center(elem))

    # 5. invalid element validation
    def test_invalid_element_validation(self):
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        # None element
        self.assertFalse(wa_plugin.validate_element_for_action(None, win_rect))
        
        # Element does not exist
        non_existent = MagicMock()
        non_existent.Exists.return_value = False
        self.assertFalse(wa_plugin.validate_element_for_action(non_existent, win_rect))
        
        # Tiny/invalid rect
        tiny_elem = MagicMock()
        tiny_elem.Exists.return_value = True
        tiny_elem.IsEnabled = True
        tiny_elem.IsOffscreen = False
        tiny_elem.BoundingRectangle = MagicMock(left=10, top=10, right=11, bottom=11)
        self.assertFalse(wa_plugin.validate_element_for_action(tiny_elem, win_rect))

    # 6. disabled element validation
    def test_disabled_element_validation(self):
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        disabled_elem = MagicMock()
        disabled_elem.Exists.return_value = True
        disabled_elem.IsEnabled = False
        disabled_elem.IsOffscreen = False
        disabled_elem.BoundingRectangle = MagicMock(left=100, top=50, right=150, bottom=90)
        self.assertFalse(wa_plugin.validate_element_for_action(disabled_elem, win_rect))

    # 7. offscreen element validation
    def test_offscreen_element_validation(self):
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        offscreen_elem = MagicMock()
        offscreen_elem.Exists.return_value = True
        offscreen_elem.IsEnabled = True
        offscreen_elem.IsOffscreen = True
        offscreen_elem.BoundingRectangle = MagicMock(left=100, top=50, right=150, bottom=90)
        self.assertFalse(wa_plugin.validate_element_for_action(offscreen_elem, win_rect))

    def test_valid_element_action(self):
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        valid_elem = MagicMock()
        valid_elem.Exists.return_value = True
        valid_elem.IsEnabled = True
        valid_elem.IsOffscreen = False
        valid_elem.BoundingRectangle = MagicMock(left=100, top=50, right=150, bottom=90)
        self.assertTrue(wa_plugin.validate_element_for_action(valid_elem, win_rect))

    # 1. InvokePattern preferred
    def test_invoke_pattern_preferred(self):
        elem = MagicMock()
        elem.Exists.return_value = True
        inv = MagicMock()
        elem.GetInvokePattern.return_value = inv
        elem.Click = MagicMock()
        
        self.assertTrue(wa_plugin.invoke_ui_element(elem, is_call_action=True))
        inv.Invoke.assert_called_once()
        elem.Click.assert_not_called()

    # 2. TogglePattern not used for normal call buttons
    def test_toggle_pattern_not_used_for_call_buttons(self):
        elem = MagicMock()
        elem.Exists.return_value = True
        elem.GetInvokePattern.return_value = None
        elem.Click.side_effect = Exception("Click unavailable")
        elem.BoundingRectangle = None
        tog = MagicMock()
        elem.GetTogglePattern.return_value = tog
        
        # When is_call_action=True, TogglePattern must NOT be executed
        res = wa_plugin.invoke_ui_element(elem, is_call_action=True)
        self.assertFalse(res)
        tog.Toggle.assert_not_called()

    # 3. UIA Click fallback
    def test_uia_click_fallback(self):
        elem = MagicMock()
        elem.Exists.return_value = True
        elem.GetInvokePattern.return_value = None
        elem.Click = MagicMock()
        
        self.assertTrue(wa_plugin.invoke_ui_element(elem, is_call_action=True))
        elem.Click.assert_called_once()

    # 4. Dynamic center click fallback
    @patch('pyautogui.click')
    def test_dynamic_center_click_fallback(self, mock_pyautogui_click):
        elem = MagicMock()
        elem.Exists.return_value = True
        elem.GetInvokePattern.return_value = None
        elem.Click.side_effect = Exception("Native click failed")
        elem.BoundingRectangle = MagicMock(left=100, top=200, right=200, bottom=300)
        
        self.assertTrue(wa_plugin.invoke_ui_element(elem, is_call_action=True))
        mock_pyautogui_click.assert_called_once_with(150, 250)

    # 8. generic "call" false-positive rejection
    def test_generic_call_false_positive_rejection(self):
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        # False positive: Call history / missed call text item
        history_item = MagicMock()
        history_item.Exists.return_value = True
        history_item.IsEnabled = True
        history_item.IsOffscreen = False
        history_item.Name = "Missed voice call at 10:30 PM"
        history_item.ControlType = 50020 # Text / List item
        history_item.BoundingRectangle = MagicMock(left=200, top=100, right=400, bottom=140)
        self.assertFalse(wa_plugin.is_valid_call_candidate(history_item, "voice", win_rect))

        # False positive: Item outside header region (e.g. In message history at y=500, win height=800)
        outside_header = MagicMock()
        outside_header.Exists.return_value = True
        outside_header.IsEnabled = True
        outside_header.IsOffscreen = False
        outside_header.Name = "Voice call"
        outside_header.ControlType = 50000 # Button
        outside_header.BoundingRectangle = MagicMock(left=200, top=500, right=250, bottom=550)
        self.assertFalse(wa_plugin.is_valid_call_candidate(outside_header, "voice", win_rect))

        # False positive: Search Edit control with generic name
        edit_control = MagicMock()
        edit_control.Exists.return_value = True
        edit_control.IsEnabled = True
        edit_control.IsOffscreen = False
        edit_control.Name = "Search or start new call"
        edit_control.ControlType = 50004 # EditControl
        edit_control.BoundingRectangle = MagicMock(left=50, top=50, right=200, bottom=80)
        self.assertFalse(wa_plugin.is_valid_call_candidate(edit_control, "voice", win_rect))

        # True positive: Valid header voice call button
        valid_btn = MagicMock()
        valid_btn.Exists.return_value = True
        valid_btn.IsEnabled = True
        valid_btn.IsOffscreen = False
        valid_btn.Name = "Voice call"
        valid_btn.ControlType = 50000 # Button
        valid_btn.BoundingRectangle = MagicMock(left=800, top=30, right=850, bottom=70)
        self.assertTrue(wa_plugin.is_valid_call_candidate(valid_btn, "voice", win_rect))

    # 9. call verification timeout
    def test_call_verification_timeout(self):
        win = MagicMock()
        win.Exists.return_value = True
        win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        win.ButtonControl.return_value.Exists.return_value = False
        
        with patch('uiautomation.WalkControl', return_value=[]):
            with patch('time.time', side_effect=[100.0, 100.1, 106.0]):
                state = wa_plugin.verify_call_started(win, timeout=5.0)
                self.assertEqual(state, "SUCCESS_UNVERIFIED")

    # 10. successful post-action state
    def test_successful_post_action_state(self):
        win = MagicMock()
        win.Exists.return_value = True
        win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        # Positive indicator: "End call" button appears
        end_btn = MagicMock()
        end_btn.Exists.return_value = True
        end_btn.IsEnabled = True
        end_btn.IsOffscreen = False
        end_btn.BoundingRectangle = MagicMock(left=800, top=30, right=850, bottom=70)
        
        win.ButtonControl.side_effect = lambda searchDepth, Name: end_btn if Name == "End call" else MagicMock(Exists=lambda *a: False)
        
        with patch('uiautomation.WalkControl', return_value=[]):
            state = wa_plugin.verify_call_started(win, timeout=1.0)
            self.assertEqual(state, "SUCCESS_VERIFIED")

    # 11. unsuccessful post-action state
    def test_unsuccessful_post_action_state(self):
        btn = MagicMock()
        btn.Exists.return_value = True
        btn.IsEnabled = True
        btn.IsOffscreen = False
        btn.BoundingRectangle = MagicMock(left=800, top=30, right=850, bottom=70)
        btn.Name = "Voice call"
        btn.ControlType = 50000
        btn.GetInvokePattern.return_value = MagicMock()
        
        self.controller.window.ButtonControl.return_value = btn
        
        # When verify_call_started returns unverified, the method should handle retries / fallbacks
        with patch.object(wa_plugin, 'find_call_button', return_value=btn):
            with patch.object(wa_plugin, 'verify_call_started', return_value="SUCCESS_UNVERIFIED"):
                with patch.object(wa_plugin, 'visual_call_button_fallback', return_value=False):
                    with patch.object(wa_plugin, 'window_relative_fallback', return_value=False):
                        with patch.object(wa_plugin, 'keyboard_call_fallback', return_value=False):
                            self.controller.active_contact = "myworld"
                            result = self.controller.start_voice_call("My World")
                            self.assertFalse(result)
                            self.assertIn("All automation levels failed", "".join(self.controller.diagnostic_log))

    # Window Acquisition Scenario 1: UIA window already available
    def test_acquire_window_uia_already_available(self):
        valid_win = MagicMock()
        valid_win.Exists.return_value = True
        valid_win.IsOffscreen = False
        valid_win.BoundingRectangle = MagicMock(left=100, top=100, right=1100, bottom=900)
        
        with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
            with patch.object(wa_plugin, 'find_whatsapp_window', return_value=valid_win):
                win, diag = wa_plugin.acquire_whatsapp_window(timeout=1.0)
                self.assertEqual(win, valid_win)
                self.assertTrue(diag["uia_window_found"])
                self.assertTrue(diag["visible"])
                self.assertEqual(diag["bounds"], (100, 100, 1100, 900))

    # Window Acquisition Scenario 2: Window exists but minimized
    def test_acquire_window_minimized_restored(self):
        self.win_patcher.stop()
        try:
            hwnd_info = {
                "hwnd": 12345,
                "pid": 9804,
                "title": "WhatsApp",
                "class": "ApplicationFrameWindow",
                "visible": False,
                "iconic": True
            }
            mock_uia_win = MagicMock()
            mock_uia_win.Exists.return_value = True
            mock_uia_win.IsOffscreen = False
            mock_uia_win.BoundingRectangle = MagicMock(left=100, top=100, right=1000, bottom=800)
            
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=hwnd_info):
                with patch.object(wa_plugin, 'activate_native_hwnd', return_value=mock_uia_win) as mock_activate:
                    win, diag = wa_plugin.acquire_whatsapp_window(timeout=1.0)
                    self.assertEqual(win, mock_uia_win)
                    self.assertTrue(diag["hwnd_found"])
                    self.assertTrue(diag["minimized"])
                    mock_activate.assert_called_once_with(hwnd_info)
        finally:
            self.win_patcher.start()

    # Window Acquisition Scenario 3: Native HWND exists and UIA re-queries successfully
    def test_acquire_window_native_hwnd_conversion(self):
        hwnd_info = {"hwnd": 54321, "title": "WhatsApp", "visible": True, "iconic": False}
        mock_ctrl = MagicMock()
        mock_ctrl.Exists.return_value = True
        mock_ctrl.BoundingRectangle = MagicMock(left=50, top=50, right=1050, bottom=850)
        
        with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=hwnd_info):
            with patch.object(wa_plugin, 'activate_native_hwnd', return_value=mock_ctrl):
                win, diag = wa_plugin.acquire_whatsapp_window(timeout=1.0)
                self.assertIsNotNone(win)
                self.assertTrue(diag["hwnd_found"])

    # Window Acquisition Scenario 4: WhatsApp.Root exists but UI window initially unavailable
    def test_acquire_window_whatsapp_root_background_process(self):
        p_info = {"process_found": True, "pid": 9804, "name": "WhatsApp.Root", "is_background_root": True}
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'get_whatsapp_process_info', return_value=p_info):
                    with patch.object(wa_plugin, 'safe_launch_whatsapp', return_value=True) as mock_launch:
                        win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                        self.assertIsNone(win)
                        self.assertTrue(diag["process_found"])
                        self.assertTrue(diag["launch_attempted"])
                        mock_launch.assert_called_once()

    # Window Acquisition Scenario 5: Launch/restore succeeds and UI appears
    def test_acquire_window_launch_succeeds_and_ui_appears(self):
        valid_win = MagicMock()
        valid_win.Exists.return_value = True
        valid_win.BoundingRectangle = MagicMock(left=0, top=0, right=1200, bottom=800)
        
        # 1st call None, 2nd call None (pre-poll), 3rd call inside polling loop returns valid window
        with patch.object(wa_plugin, 'find_whatsapp_window', side_effect=[None, None, valid_win]):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'safe_launch_whatsapp', return_value=True):
                    win, diag = wa_plugin.acquire_whatsapp_window(timeout=2.0, poll_interval=0.1)
                    self.assertEqual(win, valid_win)
                    self.assertTrue(diag["launch_attempted"])
                    self.assertTrue(diag["uia_window_found"])

    # Window Acquisition Scenario 6: Acquisition times out
    def test_acquire_window_timeout(self):
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'safe_launch_whatsapp', return_value=False):
                    win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                    self.assertIsNone(win)
                    self.assertEqual(diag["failure_reason"], "WHATSAPP_WINDOW_NOT_AVAILABLE")

    # Window Acquisition Scenario 7: Invalid/zero-size window
    def test_acquire_window_invalid_zero_size(self):
        tiny_win = MagicMock()
        tiny_win.Exists.return_value = True
        tiny_win.BoundingRectangle = MagicMock(left=0, top=0, right=10, bottom=10) # Area too small
        
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=tiny_win):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'safe_launch_whatsapp', return_value=False):
                    win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                    self.assertIsNone(win)
                    self.assertEqual(diag["failure_reason"], "WHATSAPP_WINDOW_NOT_AVAILABLE")

    # Window Acquisition Scenario 8: No WhatsApp process or window exists
    def test_acquire_window_no_process_no_window(self):
        p_info = {"process_found": False, "pid": None, "name": "", "is_background_root": False}
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'get_whatsapp_process_info', return_value=p_info):
                    with patch.object(wa_plugin, 'safe_launch_whatsapp', return_value=False):
                        win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                        self.assertIsNone(win)
                        self.assertFalse(diag["process_found"])
                        self.assertEqual(diag["failure_reason"], "WHATSAPP_WINDOW_NOT_AVAILABLE")

    # --- Phase 8: 10 Diagnostic & Activation Unit Tests ---
    
    # 1. successful whatsapp URI activation
    def test_activation_successful_whatsapp_uri(self):
        with patch('ctypes.windll.shell32.ShellExecuteW', return_value=42):
            with patch('ctypes.GetLastError', return_value=0):
                with patch('subprocess.Popen', return_value=MagicMock(pid=9999)):
                    res = wa_plugin.instrumented_activate_whatsapp()
                    self.assertEqual(res["whatsapp_uri"]["return"], 42)
                    self.assertEqual(res["whatsapp_uri"]["error"], 0)
                    self.assertIsNone(res["whatsapp_uri"]["exception"])

    # 2. failed whatsapp URI activation
    def test_activation_failed_whatsapp_uri(self):
        with patch('ctypes.windll.shell32.ShellExecuteW', return_value=2): # SE_ERR_FNF = 2 (<=32 is error)
            with patch('ctypes.GetLastError', return_value=2):
                with patch('subprocess.Popen', return_value=MagicMock(pid=9999)):
                    res = wa_plugin.instrumented_activate_whatsapp()
                    self.assertEqual(res["whatsapp_uri"]["return"], 2)
                    self.assertEqual(res["whatsapp_uri"]["error"], 2)

    # 3. successful AppsFolder activation
    def test_activation_successful_appsfolder(self):
        mock_proc = MagicMock(pid=7788)
        with patch('ctypes.windll.shell32.ShellExecuteW', return_value=42):
            with patch('subprocess.Popen', return_value=mock_proc):
                res = wa_plugin.instrumented_activate_whatsapp()
                self.assertEqual(res["apps_folder"]["return"], 7788)
                self.assertEqual(res["apps_folder"]["error"], 0)

    # 4. failed AppsFolder activation
    def test_activation_failed_appsfolder(self):
        with patch('ctypes.windll.shell32.ShellExecuteW', return_value=42):
            with patch('subprocess.Popen', side_effect=FileNotFoundError("explorer.exe not found")):
                res = wa_plugin.instrumented_activate_whatsapp()
                self.assertIn("explorer.exe not found", res["apps_folder"]["exception"])

    # 5. process appears after activation
    def test_activation_process_appears_after_activation(self):
        p_before = {"process_found": False, "pids": []}
        p_after = {"process_found": True, "pids": [9804], "pid": 9804, "name": "WhatsApp.Root"}
        with patch.object(wa_plugin, 'get_whatsapp_process_info', side_effect=[p_before, p_after]):
            with patch('ctypes.windll.shell32.ShellExecuteW', return_value=42):
                with patch('subprocess.Popen', return_value=MagicMock(pid=1000)):
                    res = wa_plugin.instrumented_activate_whatsapp()
                    self.assertEqual(res["process_count_before"], 0)
                    self.assertEqual(res["process_count_after"], 1)
                    self.assertEqual(res["pids_after"], [9804])

    # 6. HWND appears after activation
    def test_activation_hwnd_appears_after_activation(self):
        h_after = [{"hwnd": 66714, "pid": 9804, "is_whatsapp_candidate": True}]
        with patch.object(wa_plugin, 'enumerate_all_desktop_windows', side_effect=[[], h_after]):
            with patch('ctypes.windll.shell32.ShellExecuteW', return_value=42):
                with patch('subprocess.Popen', return_value=MagicMock(pid=1000)):
                    res = wa_plugin.instrumented_activate_whatsapp()
                    self.assertEqual(res["hwnd_count_before"], 0)
                    self.assertEqual(res["hwnd_count_after"], 1)

    # 7. HWND never appears
    def test_activation_hwnd_never_appears(self):
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'instrumented_activate_whatsapp', return_value={}):
                    win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                    self.assertIsNone(win)
                    self.assertFalse(diag["hwnd_found"])
                    self.assertEqual(diag["failure_reason"], "WHATSAPP_WINDOW_NOT_AVAILABLE")

    # 8. package identity mismatch
    def test_activation_package_identity_mismatch(self):
        mismatch_pkg = {
            "family": "DifferentFamily_1234",
            "full_name": "Different_1234",
            "aumid": "DifferentFamily_1234!App",
            "path": "C:\\WindowsApps\\Different",
            "current_activation_string": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            "mismatch": True
        }
        with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'get_package_identity', return_value=mismatch_pkg):
                    with patch.object(wa_plugin, 'instrumented_activate_whatsapp', return_value={}):
                        win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                        self.assertEqual(diag["final_diagnosis"], "F. Package/AppUserModelID mismatch.")

    # 9. different session detected
    def test_activation_different_session_detected(self):
        sess_diag = {
            "jarvis_session": 1,
            "whatsapp_sessions": [{"pid": 9804, "name": "WhatsApp.Root", "session_id": 2}],
            "interactive_session": 2,
            "desktop_window_station": "WinSta0\\Default",
            "thread_desktop": "Default",
            "window_station": "WinSta0",
            "default_desktop_accessible": True
        }
        with patch.object(wa_plugin, 'get_session_diagnostics', return_value=sess_diag):
            diag = wa_plugin.get_session_diagnostics()
            self.assertEqual(diag["jarvis_session"], 1)
            self.assertEqual(diag["interactive_session"], 2)
            self.assertEqual(diag["whatsapp_sessions"][0]["session_id"], 2)

    # 10. delayed window appearance
    def test_activation_delayed_window_appearance(self):
        valid_win = MagicMock()
        valid_win.Exists.return_value = True
        valid_win.BoundingRectangle = MagicMock(left=50, top=50, right=1000, bottom=800)
        
        # Returns None on tick 0.0s, None on 0.1s, then valid window on 0.2s
        with patch.object(wa_plugin, 'find_whatsapp_window', side_effect=[None, None, valid_win]):
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                with patch.object(wa_plugin, 'instrumented_activate_whatsapp', return_value={}):
                    with patch.object(wa_plugin, 'get_package_identity', return_value={}):
                        with patch.object(wa_plugin, 'get_session_diagnostics', return_value={}):
                            win, diag = wa_plugin.acquire_whatsapp_window(timeout=5.0, poll_interval=0.1)
                            self.assertEqual(win, valid_win)
                            self.assertTrue(diag["uia_window_found"])
                            self.assertGreater(len(diag["timeline"]), 1)

    # --- Phase 7: HWND Discovery & Bridge Unit Tests ---

    # 1. Default desktop HWND found
    def test_default_desktop_hwnd_found(self):
        hwnd_info = {
            "hwnd": 66714,
            "pid": 9804,
            "proc": "WhatsApp.Root.exe",
            "title": "WhatsApp",
            "class": "WinUIDesktopWin32WindowClass",
            "visible": True,
            "iconic": False,
            "width": 978,
            "height": 1089,
            "bounds": (951, 0, 1929, 1089)
        }
        with patch.object(wa_plugin, 'find_whatsapp_hwnd_default_desktop', return_value=(66714, hwnd_info)):
            hwnd, info = wa_plugin.find_whatsapp_hwnd_default_desktop()
            self.assertEqual(hwnd, 66714)
            self.assertEqual(info["proc"], "WhatsApp.Root.exe")

    # 2. Non-WhatsApp HWND rejected
    def test_non_whatsapp_hwnd_rejected(self):
        non_wa_info = {
            "hwnd": 12345,
            "pid": 5000,
            "proc": "notepad.exe",
            "title": "Untitled - Notepad",
            "class": "Notepad",
            "visible": True,
            "iconic": False,
            "width": 800,
            "height": 600
        }
        self.assertFalse(wa_plugin.validate_whatsapp_hwnd(non_wa_info))

    # 3. Wrong PID rejected
    def test_wrong_pid_rejected(self):
        wrong_pid_info = {
            "hwnd": 99999,
            "pid": 1111,
            "proc": "chrome.exe",
            "title": "WhatsApp - Google Chrome",
            "class": "Chrome_WidgetWin_1",
            "visible": True,
            "iconic": False,
            "width": 1000,
            "height": 800
        }
        self.assertFalse(wa_plugin.validate_whatsapp_hwnd(wrong_pid_info))

    # 4. Hidden window rejected
    def test_hidden_window_rejected(self):
        hidden_info = {
            "hwnd": 66714,
            "pid": 9804,
            "proc": "WhatsApp.Root.exe",
            "title": "WhatsApp",
            "class": "WinUIDesktopWin32WindowClass",
            "visible": False,
            "iconic": False,
            "width": 978,
            "height": 1089
        }
        with patch('ctypes.windll.user32.IsWindow', return_value=True):
            self.assertFalse(wa_plugin.validate_whatsapp_hwnd(hidden_info))

    # 5. Minimized window restored
    def test_minimized_window_restored_phase7(self):
        minimized_info = {
            "hwnd": 66714,
            "pid": 9804,
            "proc": "WhatsApp.Root.exe",
            "title": "WhatsApp",
            "class": "WinUIDesktopWin32WindowClass",
            "visible": False,
            "iconic": True,
            "width": 978,
            "height": 1089
        }
        mock_ctrl = MagicMock()
        mock_ctrl.Exists.return_value = True
        mock_ctrl.BoundingRectangle = MagicMock(left=951, top=0, right=1929, bottom=1089)
        
        with patch('ctypes.windll.user32.ShowWindow') as mock_show:
            with patch('ctypes.windll.user32.SetForegroundWindow'):
                with patch.object(wa_plugin, 'bridge_hwnd_to_uia', return_value=mock_ctrl):
                    ctrl = wa_plugin.activate_native_hwnd(minimized_info)
                    self.assertEqual(ctrl, mock_ctrl)
                    mock_show.assert_called_once_with(66714, 9) # SW_RESTORE

    # 6. Invalid bounds rejected
    def test_invalid_bounds_rejected(self):
        tiny_info = {
            "hwnd": 66714,
            "pid": 9804,
            "proc": "WhatsApp.Root.exe",
            "title": "WhatsApp",
            "class": "WinUIDesktopWin32WindowClass",
            "visible": True,
            "iconic": False,
            "width": 50,
            "height": 50
        }
        with patch('ctypes.windll.user32.IsWindow', return_value=True):
            self.assertFalse(wa_plugin.validate_whatsapp_hwnd(tiny_info))

    # 7. Correct WhatsApp HWND bridged through ControlFromHandle
    def test_correct_whatsapp_hwnd_bridged_through_controlfromhandle(self):
        mock_ctrl = MagicMock()
        mock_ctrl.Exists.return_value = True
        mock_ctrl.Name = "WhatsApp"
        mock_ctrl.BoundingRectangle = MagicMock(left=951, top=0, right=1929, bottom=1089)
        
        with patch('uiautomation.ControlFromHandle', return_value=mock_ctrl):
            ctrl = wa_plugin.bridge_hwnd_to_uia(66714)
            self.assertEqual(ctrl, mock_ctrl)
            self.assertEqual(ctrl.Name, "WhatsApp")

    # 8. ControlFromHandle failure returns structured failure
    def test_controlfromhandle_failure_returns_structured_failure(self):
        with patch.object(wa_plugin, 'find_whatsapp_hwnd_default_desktop', return_value=(None, None)):
            with patch.object(wa_plugin, 'find_whatsapp_window', return_value=None):
                with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=None):
                    with patch.object(wa_plugin, 'instrumented_activate_whatsapp', return_value={}):
                        win, diag = wa_plugin.acquire_whatsapp_window(timeout=0.2, poll_interval=0.1)
                        self.assertIsNone(win)
                        self.assertEqual(diag["failure_reason"], "WHATSAPP_WINDOW_NOT_AVAILABLE")

    # 9. Existing UIA root fallback is NOT used when Default HWND is available
    def test_existing_uia_root_fallback_not_used(self):
        self.win_patcher.stop()
        try:
            hwnd_info = {
                "hwnd": 66714,
                "pid": 9804,
                "proc": "WhatsApp.Root.exe",
                "title": "WhatsApp",
                "class": "WinUIDesktopWin32WindowClass",
                "visible": True,
                "iconic": False,
                "width": 978,
                "height": 1089
            }
            mock_ctrl = MagicMock()
            mock_ctrl.Exists.return_value = True
            mock_ctrl.BoundingRectangle = MagicMock(left=951, top=0, right=1929, bottom=1089)
            
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=hwnd_info):
                with patch.object(wa_plugin, 'activate_native_hwnd', return_value=mock_ctrl):
                    with patch('uiautomation.GetRootControl') as mock_root:
                        ctrl = wa_plugin.find_whatsapp_window()
                        self.assertEqual(ctrl, mock_ctrl)
                        mock_root.assert_not_called()
        finally:
            self.win_patcher.start()

    # 10. Existing valid HWND is reused without relaunching WhatsApp
    def test_existing_valid_hwnd_reused_without_relaunching(self):
        self.win_patcher.stop()
        try:
            hwnd_info = {
                "hwnd": 66714,
                "pid": 9804,
                "proc": "WhatsApp.Root.exe",
                "title": "WhatsApp",
                "class": "WinUIDesktopWin32WindowClass",
                "visible": True,
                "iconic": False,
                "width": 978,
                "height": 1089
            }
            mock_ctrl = MagicMock()
            mock_ctrl.Exists.return_value = True
            mock_ctrl.BoundingRectangle = MagicMock(left=951, top=0, right=1929, bottom=1089)
            
            with patch.object(wa_plugin, 'find_native_whatsapp_hwnd', return_value=hwnd_info):
                with patch.object(wa_plugin, 'activate_native_hwnd', return_value=mock_ctrl):
                    with patch.object(wa_plugin, 'safe_launch_whatsapp') as mock_launch:
                        win, diag = wa_plugin.acquire_whatsapp_window(timeout=1.0)
                        self.assertEqual(win, mock_ctrl)
                        self.assertTrue(diag["hwnd_found"])
                        mock_launch.assert_not_called()
        finally:
            self.win_patcher.start()

    # --- Phase 7: UI Control Discovery & Candidate Scoring Unit Tests ---

    # 1. Voice call ButtonControl in header
    def test_score_voice_call_button_in_header(self):
        btn_info = {
            "ControlType": "ButtonControl",
            "Name": "Voice call",
            "AutomationId": "",
            "ClassName": "html-button",
            "BoundingRectangle": (707, 76, 767, 137),
            "IsEnabled": True,
            "IsOffscreen": False
        }
        header_bounds = (98, 58, 959, 155)
        res = wa_plugin.score_call_candidate(btn_info, call_type="voice", header_bounds=header_bounds)
        self.assertGreaterEqual(res["Score"], 80)
        self.assertIn("Exact Name match 'Voice call'", res["PositiveSignals"])
        self.assertIn("ControlType is Button", res["PositiveSignals"])
        self.assertIn("Located inside detected chat header container", res["PositiveSignals"])
        self.assertEqual(len(res["NegativeSignals"]), 0)

    # 2. Video call ButtonControl in header
    def test_score_video_call_button_in_header(self):
        btn_info = {
            "ControlType": "ButtonControl",
            "Name": "Video call",
            "AutomationId": "",
            "ClassName": "html-button",
            "BoundingRectangle": (623, 76, 683, 137),
            "IsEnabled": True,
            "IsOffscreen": False
        }
        header_bounds = (98, 58, 959, 155)
        res = wa_plugin.score_call_candidate(btn_info, call_type="video", header_bounds=header_bounds)
        self.assertGreaterEqual(res["Score"], 80)
        self.assertIn("Exact Name match 'Video call'", res["PositiveSignals"])
        self.assertIn("ControlType is Button", res["PositiveSignals"])
        self.assertIn("Located inside detected chat header container", res["PositiveSignals"])
        self.assertEqual(len(res["NegativeSignals"]), 0)

    # 3. Fake "Call history" control
    def test_score_fake_call_history_control(self):
        history_info = {
            "ControlType": "ButtonControl",
            "Name": "Voice call Click to call back 12:16 pm",
            "AutomationId": "",
            "ClassName": "msg-bubble",
            "BoundingRectangle": (191, -471, 695, -313),
            "IsEnabled": True,
            "IsOffscreen": True
        }
        res = wa_plugin.score_call_candidate(history_info, call_type="voice")
        self.assertLess(res["Score"], 0)
        self.assertTrue(any("history" in s.lower() or "call-back" in s.lower() for s in res["NegativeSignals"]))
        self.assertTrue(any("negative" in s.lower() or "offscreen" in s.lower() for s in res["NegativeSignals"]))

    # 4. Fake "Incoming call" item
    def test_score_fake_incoming_call_item(self):
        incoming_info = {
            "ControlType": "ListItemControl",
            "Name": "Incoming voice call from Friend",
            "AutomationId": "",
            "ClassName": "list-item",
            "BoundingRectangle": (100, 300, 500, 350),
            "IsEnabled": True,
            "IsOffscreen": False
        }
        res = wa_plugin.score_call_candidate(incoming_info, call_type="voice")
        self.assertLess(res["Score"], 20)
        self.assertTrue(any("history" in s.lower() or "missed" in s.lower() or "call-back" in s.lower() for s in res["NegativeSignals"]))
        self.assertTrue(any("non-button" in s.lower() for s in res["NegativeSignals"]))

    # 5. Search box containing "call"
    def test_score_search_box_containing_call(self):
        search_info = {
            "ControlType": "EditControl",
            "Name": "Search or start new call",
            "AutomationId": "search-input",
            "ClassName": "search-box",
            "BoundingRectangle": (50, 70, 300, 110),
            "IsEnabled": True,
            "IsOffscreen": False
        }
        res = wa_plugin.score_call_candidate(search_info, call_type="voice")
        self.assertLess(res["Score"], 20)
        self.assertTrue(any("search" in s.lower() for s in res["NegativeSignals"]))
        self.assertTrue(any("non-button" in s.lower() for s in res["NegativeSignals"]))

    # 6. Button outside header
    def test_score_button_outside_header(self):
        sidebar_info = {
            "ControlType": "ButtonControl",
            "Name": "Calls",
            "AutomationId": "",
            "ClassName": "tab-button",
            "BoundingRectangle": (20, 139, 80, 200),
            "IsEnabled": True,
            "IsOffscreen": False
        }
        header_bounds = (98, 58, 959, 155)
        res = wa_plugin.score_call_candidate(sidebar_info, call_type="voice", header_bounds=header_bounds)
        self.assertLess(res["Score"], 30)
        self.assertTrue(any("calls" in s.lower() or "sidebar" in s.lower() for s in res["NegativeSignals"]))

    # 7. Disabled call-like button
    def test_score_disabled_call_like_button(self):
        disabled_info = {
            "ControlType": "ButtonControl",
            "Name": "Voice call",
            "AutomationId": "",
            "ClassName": "html-button",
            "BoundingRectangle": (707, 76, 767, 137),
            "IsEnabled": False,
            "IsOffscreen": False
        }
        res = wa_plugin.score_call_candidate(disabled_info, call_type="voice")
        self.assertTrue(any("disabled" in s.lower() for s in res["NegativeSignals"]))
        self.assertLess(res["Score"], 70)

    # 8. Offscreen call-like button
    def test_score_offscreen_call_like_button(self):
        offscreen_info = {
            "ControlType": "ButtonControl",
            "Name": "Voice call",
            "AutomationId": "",
            "ClassName": "html-button",
            "BoundingRectangle": (707, 76, 767, 137),
            "IsEnabled": True,
            "IsOffscreen": True
        }
        res = wa_plugin.score_call_candidate(offscreen_info, call_type="voice")
        self.assertTrue(any("offscreen" in s.lower() for s in res["NegativeSignals"]))
        self.assertLess(res["Score"], 70)

    # 9. Multiple call-like candidates ranking
    def test_score_multiple_call_like_candidates_ranking(self):
        header_bounds = (98, 58, 959, 155)
        candidates = [
            {
                "ControlType": "ButtonControl",
                "Name": "Voice call",
                "BoundingRectangle": (707, 76, 767, 137),
                "IsEnabled": True,
                "IsOffscreen": False
            },
            {
                "ControlType": "ButtonControl",
                "Name": "Calls",
                "BoundingRectangle": (20, 139, 80, 200),
                "IsEnabled": True,
                "IsOffscreen": False
            },
            {
                "ControlType": "ButtonControl",
                "Name": "Voice call Click to call back",
                "BoundingRectangle": (191, -471, 695, -313),
                "IsEnabled": True,
                "IsOffscreen": True
            },
            {
                "ControlType": "EditControl",
                "Name": "Search call",
                "BoundingRectangle": (50, 70, 300, 110),
                "IsEnabled": True,
                "IsOffscreen": False
            }
        ]
        scored = [wa_plugin.score_call_candidate(c, call_type="voice", header_bounds=header_bounds) for c in candidates]
        scored.sort(key=lambda x: x["Score"], reverse=True)
        
        # Candidate 1 (exact Voice call button in header) must rank #1
        self.assertEqual(scored[0]["Name"], "Voice call")
        self.assertGreaterEqual(scored[0]["Score"], 80)
        
        # Trailing items must have much lower or negative scores
        self.assertLess(scored[1]["Score"], 50)
        self.assertLess(scored[-1]["Score"], 0)

    # --- Phase 11: Adaptive Voice Call Action & Verification Unit Tests ---

    # 1. Voice call button found inside header
    def test_voice_call_button_found_inside_header(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        mock_header = MagicMock()
        mock_header.ControlType = 50026
        mock_header.BoundingRectangle = MagicMock(left=100, top=50, right=900, bottom=150)
        
        btn_voice = MagicMock(ControlType=50000, Name="Voice call", IsEnabled=True, IsOffscreen=False)
        btn_voice.BoundingRectangle = MagicMock(left=700, top=70, right=760, bottom=130)
        btn_voice.Exists.return_value = True
        btn_voice.GetParentControl.return_value = mock_header
        
        mock_header.ButtonControl.return_value = btn_voice
        mock_win.ButtonControl.return_value = btn_voice
        
        with patch.object(wa_plugin, 'discover_chat_header', return_value={"found": True, "header_container": {"BoundingRectangle": (100, 50, 900, 150)}}):
            btn = wa_plugin.find_call_button(mock_win, "voice")
            self.assertEqual(btn, btn_voice)

    # 2. Exact "Voice call" candidate wins over sidebar "Calls"
    def test_exact_voice_call_candidate_wins_over_sidebar_calls(self):
        btn_sidebar = MagicMock(ControlType=50000, Name="Calls", IsEnabled=True, IsOffscreen=False)
        btn_sidebar.BoundingRectangle = MagicMock(left=20, top=140, right=80, bottom=200)
        btn_sidebar.Exists.return_value = True
        
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        self.assertFalse(wa_plugin.is_valid_call_candidate(btn_sidebar, "voice", win_rect))

    # 3. Video call does not get selected for voice
    def test_video_call_does_not_get_selected_for_voice(self):
        btn_video = MagicMock(ControlType=50000, Name="Video call", IsEnabled=True, IsOffscreen=False)
        btn_video.BoundingRectangle = MagicMock(left=620, top=70, right=680, bottom=130)
        btn_video.Exists.return_value = True
        
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        self.assertFalse(wa_plugin.is_valid_call_candidate(btn_video, "voice", win_rect))

    # 4. Disabled voice button rejected
    def test_disabled_voice_button_rejected(self):
        btn_disabled = MagicMock(ControlType=50000, Name="Voice call", IsEnabled=False, IsOffscreen=False)
        btn_disabled.BoundingRectangle = MagicMock(left=700, top=70, right=760, bottom=130)
        btn_disabled.Exists.return_value = True
        
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        self.assertFalse(wa_plugin.is_valid_call_candidate(btn_disabled, "voice", win_rect))

    # 5. Offscreen voice button rejected
    def test_offscreen_voice_button_rejected(self):
        btn_offscreen = MagicMock(ControlType=50000, Name="Voice call", IsEnabled=True, IsOffscreen=True)
        btn_offscreen.BoundingRectangle = MagicMock(left=700, top=70, right=760, bottom=130)
        btn_offscreen.Exists.return_value = True
        
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        self.assertFalse(wa_plugin.is_valid_call_candidate(btn_offscreen, "voice", win_rect))

    # 6. Invalid bounds rejected
    def test_invalid_bounds_rejected_phase11(self):
        btn_tiny = MagicMock(ControlType=50000, Name="Voice call", IsEnabled=True, IsOffscreen=False)
        btn_tiny.BoundingRectangle = MagicMock(left=700, top=70, right=701, bottom=71)
        btn_tiny.Exists.return_value = True
        
        win_rect = MagicMock(left=0, top=0, right=1000, bottom=800)
        self.assertFalse(wa_plugin.is_valid_call_candidate(btn_tiny, "voice", win_rect))

    # 7. InvokePattern succeeds but call state does NOT change
    def test_invokepattern_succeeds_but_call_state_does_not_change(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        btn_voice = MagicMock(ControlType=50000, Name="Voice call", IsEnabled=True, IsOffscreen=False)
        btn_voice.BoundingRectangle = MagicMock(left=700, top=70, right=760, bottom=130)
        btn_voice.Exists.return_value = True
        btn_invoke = MagicMock()
        btn_voice.GetInvokePattern.return_value = btn_invoke
        
        # Invocation dispatches
        dispatched = wa_plugin.invoke_ui_element(btn_voice, is_call_action=True)
        self.assertTrue(dispatched)
        btn_invoke.Invoke.assert_called_once()
        
        # Verification fails when no call indicators appear
        with patch('uiautomation.WalkControl', return_value=[]):
            with patch.object(mock_win, 'ButtonControl', return_value=MagicMock(Exists=lambda *a: False)):
                ver = wa_plugin.verify_call_started(mock_win, timeout=0.1)
                self.assertEqual(ver, "SUCCESS_UNVERIFIED")

    # 8. InvokePattern succeeds and Calling state appears
    def test_invokepattern_succeeds_and_calling_state_appears(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        calling_ctrl = MagicMock(ControlType=50020, Name="Calling...", IsEnabled=True, IsOffscreen=False)
        with patch('uiautomation.WalkControl', return_value=[(calling_ctrl, 1)]):
            ver = wa_plugin.verify_call_started(mock_win, timeout=0.2)
            self.assertEqual(ver, "SUCCESS_VERIFIED")

    # 9. Ringing state appears
    def test_ringing_state_appears(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        ringing_ctrl = MagicMock(ControlType=50020, Name="Ringing...", IsEnabled=True, IsOffscreen=False)
        with patch('uiautomation.WalkControl', return_value=[(ringing_ctrl, 1)]):
            ver = wa_plugin.verify_call_started(mock_win, timeout=0.2)
            self.assertEqual(ver, "SUCCESS_VERIFIED")

    # 10. Connected state appears
    def test_connected_state_appears(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        timer_ctrl = MagicMock(ControlType=50020, Name="00:15", IsEnabled=True, IsOffscreen=False)
        with patch('uiautomation.WalkControl', return_value=[(timer_ctrl, 1)]):
            ver = wa_plugin.verify_call_started(mock_win, timeout=0.2)
            self.assertEqual(ver, "SUCCESS_VERIFIED")

    # 11. End Call appears
    def test_end_call_appears(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        end_btn = MagicMock(ControlType=50000, Name="End call", IsEnabled=True, IsOffscreen=False)
        end_btn.BoundingRectangle = MagicMock(left=700, top=70, right=760, bottom=130)
        end_btn.Exists.return_value = True
        
        mock_win.ButtonControl.side_effect = lambda **kwargs: end_btn if kwargs.get("Name") == "End call" else MagicMock(Exists=lambda *a: False)
        ver = wa_plugin.verify_call_started(mock_win, timeout=0.2)
        self.assertEqual(ver, "SUCCESS_VERIFIED")

    # 12. Standalone Mute does NOT count as successful verification
    def test_standalone_mute_does_not_count_as_successful_verification(self):
        mock_win = MagicMock()
        mock_win.Exists.return_value = True
        mock_win.BoundingRectangle = MagicMock(left=0, top=0, right=1000, bottom=800)
        
        mute_ctrl = MagicMock(ControlType=50000, Name="Mute microphone", IsEnabled=True, IsOffscreen=False)
        with patch('uiautomation.WalkControl', return_value=[(mute_ctrl, 1)]):
            with patch.object(mock_win, 'ButtonControl', return_value=MagicMock(Exists=lambda *a: False)):
                ver = wa_plugin.verify_call_started(mock_win, timeout=0.1)
                self.assertEqual(ver, "SUCCESS_UNVERIFIED")

    # 13. First attempt fails and second fresh UIA lookup succeeds
    def test_first_attempt_fails_and_second_fresh_lookup_succeeds(self):
        controller = wa_plugin.WhatsAppDesktopController()
        controller.window = MagicMock()
        controller.active_contact = "friend"
        
        fresh_btn = MagicMock(ControlType=50000, Name="Voice call")
        fresh_btn.Exists.return_value = True
        
        with patch.object(wa_plugin, 'find_call_button', side_effect=[None, fresh_btn]):
            with patch.object(wa_plugin, 'invoke_ui_element', return_value=True):
                with patch.object(wa_plugin, 'verify_call_started', return_value="SUCCESS_VERIFIED"):
                    with patch.object(controller, 'find_or_focus_whatsapp', return_value=True):
                        res = controller.start_voice_call("Friend")
                        self.assertEqual(res, "SUCCESS_VERIFIED")
                        self.assertEqual(controller.find_or_focus_whatsapp.call_count, 1)

    # 14. Both attempts fail safely
    def test_both_attempts_fail_safely(self):
        controller = wa_plugin.WhatsAppDesktopController()
        controller.window = MagicMock()
        controller.active_contact = "friend"
        
        with patch.object(wa_plugin, 'find_call_button', return_value=None):
            with patch.object(controller, 'find_or_focus_whatsapp', return_value=True):
                with patch.object(wa_plugin, 'visual_call_button_fallback', return_value=False):
                    with patch.object(wa_plugin, 'window_relative_fallback', return_value=False):
                        with patch.object(wa_plugin, 'keyboard_call_fallback', return_value=False):
                            res = controller.start_voice_call("Friend")
                            self.assertFalse(res)

    # 15. TogglePattern is NOT used for call buttons
    def test_togglepattern_not_used_for_call_buttons_phase11(self):
        btn = MagicMock(ControlType=50000)
        btn.Exists.return_value = True
        btn.GetInvokePattern.return_value = None
        btn.Click.side_effect = Exception("Click failed")
        btn.BoundingRectangle = None
        
        toggle_pat = MagicMock()
        btn.GetTogglePattern.return_value = toggle_pat
        
        # When is_call_action=True, TogglePattern must NOT be invoked
        res = wa_plugin.invoke_ui_element(btn, is_call_action=True)
        self.assertFalse(res)
        toggle_pat.Toggle.assert_not_called()

    # 16. Stale UIA element is not reused after retry
    def test_stale_uia_element_not_reused_after_retry(self):
        controller = wa_plugin.WhatsAppDesktopController()
        controller.window = MagicMock()
        controller.active_contact = "friend"
        
        stale_btn = MagicMock(Name="Voice call (Stale)")
        fresh_btn = MagicMock(Name="Voice call (Fresh)")
        
        call_count = [0]
        def mock_find(win, call_type):
            if call_type == "voice":
                call_count[0] += 1
                return stale_btn if call_count[0] <= 2 else fresh_btn
            return None
            
        with patch.object(wa_plugin, 'find_call_button', side_effect=mock_find):
            with patch.object(wa_plugin, 'invoke_ui_element', return_value=True) as mock_invoke:
                with patch.object(wa_plugin, 'verify_call_started', side_effect=["SUCCESS_UNVERIFIED", "SUCCESS_VERIFIED"]):
                    with patch.object(controller, 'find_or_focus_whatsapp', return_value=True):
                        res = controller.start_voice_call("Friend")
                        self.assertEqual(res, "SUCCESS_VERIFIED")
                        # Attempt 1 invoked stale_btn, Attempt 2 invoked fresh_btn
                        self.assertEqual(mock_invoke.call_count, 2)
                        self.assertEqual(mock_invoke.call_args_list[0][0][0], stale_btn)
                        self.assertEqual(mock_invoke.call_args_list[1][0][0], fresh_btn)

    # 17. Video call uses Video call control
    def test_video_call_uses_video_call_control(self):
        controller = wa_plugin.WhatsAppDesktopController()
        controller.window = MagicMock()
        controller.active_contact = "friend"
        
        vid_btn = MagicMock(ControlType=50000, Name="Video call")
        with patch.object(wa_plugin, 'find_call_button', return_value=vid_btn) as mock_find:
            with patch.object(wa_plugin, 'invoke_ui_element', return_value=True):
                with patch.object(wa_plugin, 'verify_call_started', return_value="SUCCESS_VERIFIED"):
                    res = controller.start_video_call("Friend")
                    self.assertEqual(res, "SUCCESS_VERIFIED")
                    mock_find.assert_called_with(controller.window, "video")

    # 18. Existing fallbacks remain available
    def test_existing_fallbacks_remain_available(self):
        controller = wa_plugin.WhatsAppDesktopController()
        controller.window = MagicMock()
        controller.active_contact = "friend"
        
        with patch.object(wa_plugin, 'find_call_button', return_value=None):
            with patch.object(controller, 'find_or_focus_whatsapp', return_value=True):
                with patch.object(wa_plugin, 'visual_call_button_fallback', return_value=True):
                    with patch.object(wa_plugin, 'verify_call_started', return_value="SUCCESS_VERIFIED"):
                        res = controller.start_voice_call("Friend")
                        self.assertEqual(res, "SUCCESS_VERIFIED")


if __name__ == '__main__':
    unittest.main()




