"""
Unit and integration test suite for JARVIS Legal Media Downloader Plugin.
"""
import http.server
import os
import shutil
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.plugin_loader import discover_plugins
from plugins.media_downloader import (
    PLUGIN,
    DownloadItem,
    MediaDownloaderManager,
    _check_ssrf,
    _format_size,
    _format_time,
    _is_safe_sandbox_path,
    _resolve_and_validate_destination,
    _sanitize_filename,
    manager,
    run,
)


class TestMediaDownloader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mgr = MediaDownloaderManager()

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_01_plugin_metadata(self):
        """Test plugin declaration structure and validity."""
        self.assertEqual(PLUGIN["name"], "media_downloader")
        self.assertIn("description", PLUGIN)
        self.assertEqual(PLUGIN["parameters"]["type"], "OBJECT")
        self.assertIn("action", PLUGIN["parameters"]["properties"])
        self.assertIn("action", PLUGIN["parameters"]["required"])

    def test_02_plugin_discovery(self):
        """Test discover_plugins loads media_downloader successfully in isolation."""
        temp_plugins = self.temp_dir / "test_plugins"
        temp_plugins.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path("plugins") / "media_downloader.py", temp_plugins / "media_downloader.py")
        reg = discover_plugins(temp_plugins, {"open_app", "reminder"})
        self.assertTrue(reg.has("media_downloader"))
        decls = reg.get_tool_declarations()
        dl_decls = [d for d in decls if d["name"] == "media_downloader"]
        self.assertEqual(len(dl_decls), 1)

    def test_03_filename_sanitization(self):
        """Test filename sanitization against path traversal and invalid characters."""
        # Path traversal
        self.assertEqual(_sanitize_filename("../../secret.mp4"), "secret.mp4")
        self.assertEqual(_sanitize_filename("..\\..\\secret.mp4"), "secret.mp4")

        # Invalid Windows characters
        self.assertEqual(_sanitize_filename('video:name*with?invalid"chars<.mp4'), "video_name_with_invalid_chars_.mp4")

        # Executable file rejection & replacement
        sanitized_exe = _sanitize_filename("malware.exe")
        self.assertTrue(sanitized_exe.endswith(".mp4"))
        self.assertNotIn(".exe", sanitized_exe)

        sanitized_bat = _sanitize_filename("script.bat")
        self.assertTrue(sanitized_bat.endswith(".mp4"))
        self.assertNotIn(".bat", sanitized_bat)

    def test_04_url_validation(self):
        """Test URL scheme and host validation."""
        # Valid URLs
        valid, err, _ = self.mgr.validate_url("https://example.com/video.mp4")
        self.assertTrue(valid)

        valid, err, _ = self.mgr.validate_url("https://stream.example.com/live/video.mp4")
        self.assertTrue(valid)

        # Invalid scheme
        valid, err, _ = self.mgr.validate_url("ftp://example.com/video.mp4")
        self.assertFalse(valid)
        self.assertIn("Unsupported protocol", err)

        # Missing host
        valid, err, _ = self.mgr.validate_url("https://")
        self.assertFalse(valid)

    def test_05_ssrf_protection(self):
        """Test that SSRF / private IP targets are blocked."""
        # Localhost
        safe, err = _check_ssrf("localhost", allow_local=False)
        self.assertFalse(safe)

        safe, err = _check_ssrf("127.0.0.1", allow_local=False)
        self.assertFalse(safe)

        # Allowed when allow_local=True for tests
        safe, err = _check_ssrf("127.0.0.1", allow_local=True)
        self.assertTrue(safe)

    def test_06_destination_sandbox(self):
        """Test destination path and drive validation."""
        home_path = Path.home() / "Downloads" / "JARVIS Media"
        self.assertTrue(_is_safe_sandbox_path(home_path))

        # Test shortcuts
        valid, _, p = _resolve_and_validate_destination("desktop")
        self.assertTrue(valid)
        self.assertEqual(p, Path.home() / "Desktop")

        valid, _, p = _resolve_and_validate_destination("downloads")
        self.assertTrue(valid)

        # Critical OS system directory protection
        if os.name == "nt":
            self.assertFalse(_is_safe_sandbox_path(Path("C:\\Windows\\System32")))
            self.assertFalse(_is_safe_sandbox_path(Path("C:\\Program Files")))
        else:
            self.assertFalse(_is_safe_sandbox_path(Path("/etc")))

    def test_07_format_helpers(self):
        """Test human readable size and time formatters."""
        self.assertEqual(_format_size(500), "500.0 B")
        self.assertEqual(_format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(_format_size(1024 * 1024 * 1024 * 2), "2.0 GB")
        self.assertEqual(_format_time(65), "1m 05s")
        self.assertEqual(_format_time(3665), "1h 01m 05s")

    def test_08_run_dispatcher_actions(self):
        """Test run() parameter handling and action routing."""
        # Missing URL
        res = run({"action": "download"})
        self.assertIn("Please provide a media URL", res)

        res = run({"action": "inspect"})
        self.assertIn("Please provide a media URL", res)

        # List with no downloads
        res = run({"action": "list"})
        self.assertTrue(isinstance(res, str))

        # Cancel with invalid ID
        res = run({"action": "cancel", "download_id": 99999})
        self.assertIn("not found", res)

        # Unknown action
        res = run({"action": "invalid_action"})
        self.assertIn("Unknown media downloader action", res)


class TestMediaDownloaderLiveServer(unittest.TestCase):
    """Integration test using a local HTTP server serving synthetic media files."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = Path(tempfile.mkdtemp())
        cls.media_content = b"TEST_MEDIA_DATA_" * 1024  # 16 KB synthetic media
        cls.test_file = cls.test_dir / "sample_video.mp4"
        cls.test_file.write_bytes(cls.media_content)

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(cls.test_dir), **kwargs)

            def log_message(self, format, *args):
                pass  # Keep test output clean

        class QuietServer(socketserver.TCPServer):
            def handle_error(self, request, client_address):
                pass  # Ignore harmless reset during cancellation

        cls.httpd = QuietServer(("127.0.0.1", 0), QuietHandler)
        cls.port = cls.httpd.server_address[1]
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.server_url = f"http://127.0.0.1:{cls.port}/sample_video.mp4"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        try:
            shutil.rmtree(cls.test_dir)
        except Exception:
            pass

    def setUp(self):
        self.download_dest = Path(tempfile.mkdtemp())
        self.mgr = MediaDownloaderManager()

    def tearDown(self):
        try:
            shutil.rmtree(self.download_dest)
        except Exception:
            pass

    def test_09_inspect_media_live(self):
        """Test inspecting remote media file over HTTP."""
        info = self.mgr.inspect_media(self.server_url, allow_local=True)
        self.assertTrue(info["valid"])
        self.assertEqual(info["filename"], "sample_video.mp4")
        self.assertEqual(info["size_bytes"], len(self.media_content))
        self.assertFalse(info["needs_confirmation"])

    def test_10_download_media_live_and_completion(self):
        """Test full downloading lifecycle and file integrity."""
        # Use mocked safe sandbox for custom download_dest
        with patch("plugins.media_downloader._is_safe_sandbox_path", return_value=True):
            success, msg, dl_id = self.mgr.start_download(
                url=self.server_url,
                destination=str(self.download_dest),
                allow_local=True,
            )
            self.assertTrue(success)
            self.assertIsNotNone(dl_id)

            # Wait for download to complete
            for _ in range(50):
                item = self.mgr.get_download(dl_id)
                if item and item.status == "COMPLETED":
                    break
                time.sleep(0.1)

            item = self.mgr.get_download(dl_id)
            self.assertIsNotNone(item)
            self.assertEqual(item.status, "COMPLETED")
            self.assertEqual(item.downloaded_bytes, len(self.media_content))
            self.assertTrue(item.final_filepath.exists())
            self.assertEqual(item.final_filepath.read_bytes(), self.media_content)

    def test_11_cancellation(self):
        """Test cancelling a download and verifying partial file cleanup."""
        # Create a slow or larger dummy endpoint
        big_file = self.test_dir / "big_video.mp4"
        big_file.write_bytes(b"A" * (1024 * 1024 * 5))  # 5 MB
        big_url = f"http://127.0.0.1:{self.port}/big_video.mp4"

        with patch("plugins.media_downloader._is_safe_sandbox_path", return_value=True):
            success, msg, dl_id = self.mgr.start_download(
                url=big_url,
                destination=str(self.download_dest),
                allow_local=True,
            )
            self.assertTrue(success)
            self.assertIsNotNone(dl_id)

            # Cancel immediately
            cancel_success, cancel_msg = self.mgr.cancel_download(dl_id)
            self.assertTrue(cancel_success)

            # Wait for worker to handle cancellation
            time.sleep(0.5)

            item = self.mgr.get_download(dl_id)
            self.assertIn(item.status, ("CANCELLED", "COMPLETED"))
            if item.status == "CANCELLED":
                # Ensure .jarvis_part was cleaned up
                self.assertFalse(item.part_filepath.exists())

    def test_12_duplicate_filename_handling(self):
        """Test that duplicate downloads get renamed safely."""
        with patch("plugins.media_downloader._is_safe_sandbox_path", return_value=True):
            # Create pre-existing file in destination
            existing_file = self.download_dest / "sample_video.mp4"
            existing_file.write_bytes(b"EXISTING")

            success, msg, dl_id = self.mgr.start_download(
                url=self.server_url,
                destination=str(self.download_dest),
                allow_local=True,
            )
            self.assertTrue(success)

            for _ in range(50):
                item = self.mgr.get_download(dl_id)
                if item and item.status == "COMPLETED":
                    break
                time.sleep(0.1)

            item = self.mgr.get_download(dl_id)
            self.assertEqual(item.status, "COMPLETED")
            self.assertEqual(item.filename, "sample_video (1).mp4")
            self.assertTrue((self.download_dest / "sample_video (1).mp4").exists())
            self.assertEqual(existing_file.read_bytes(), b"EXISTING")


if __name__ == "__main__":
    unittest.main()
