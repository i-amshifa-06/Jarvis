import unittest
from unittest.mock import patch, MagicMock
import os
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plugins.pushup_counter import PLUGIN, run, _RepCounter, _record_session

class TestPushupCounter(unittest.TestCase):
    def test_plugin_metadata(self):
        self.assertEqual(PLUGIN["name"], "pushup_counter")
        self.assertIn("description", PLUGIN)
        self.assertEqual(PLUGIN["parameters"]["type"], "OBJECT")
        self.assertIn("query", PLUGIN["parameters"]["properties"])

    def test_rep_counter_state_machine(self):
        counter = _RepCounter(min_rel_amplitude=0.1)
        # Feed synthetic up-down-up signals
        # Calibrate initial range
        counter.update(100.0) # lo=100, hi=100
        counter.update(0.0)   # lo=0, hi=100 -> rng=100
        
        # Move down (pos > 0.70)
        down_detected = counter.update(85.0)
        self.assertFalse(down_detected)
        self.assertEqual(counter.state, "down")

        # Move back up (pos < 0.30)
        rep_completed = counter.update(15.0)
        self.assertTrue(rep_completed)
        self.assertEqual(counter.state, "up")
        self.assertEqual(counter.reps, 1)

    @patch("plugins.pushup_counter._open_camera")
    def test_camera_unavailable(self, mock_camera):
        mock_camera.return_value = None
        res = run({"query": "count my pushups"})
        self.assertIn("couldn't access the camera", res)

if __name__ == "__main__":
    unittest.main()
