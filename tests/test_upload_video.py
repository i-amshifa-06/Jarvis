import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plugins.upload_video as uv
from plugins.upload_video import PLUGIN, run

class TestUploadVideo(unittest.TestCase):
    def setUp(self):
        uv._last_post_at = 0.0
        uv._upload_running = False

    def test_plugin_metadata(self):
        self.assertEqual(PLUGIN["name"], "upload_video")
        self.assertIn("description", PLUGIN)
        self.assertEqual(PLUGIN["parameters"]["type"], "OBJECT")
        self.assertIn("description", PLUGIN["parameters"]["properties"])

    def test_empty_brief_asks_for_caption(self):
        res = run({"description": ""})
        self.assertIn("I need a caption idea", res)

    def test_duplicate_call_cooldown(self):
        uv._last_post_at = time.monotonic()
        res = run({"description": "Test caption"})
        self.assertIn("ALREADY posted successfully", res)

    def test_already_running_upload(self):
        uv._upload_running = True
        res = run({"description": "Test caption"})
        self.assertIn("STILL RUNNING", res)

if __name__ == "__main__":
    unittest.main()
