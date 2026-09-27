"""
Unit and integration test suite for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.autonomous.approval import ApprovalManager, ApprovalRequest, ApprovalStatus
from core.autonomous.checkpoint import CheckpointManager
from core.autonomous.diagnostics import DiagnosticsEngine
from core.autonomous.engine import AutonomousEngine
from core.autonomous.planner import AutonomousPlan, AutonomousPlanner, RequestCategory
from core.autonomous.policy import AutonomousConfig, PolicyEngine, LEVEL_0_INSPECT, LEVEL_1_MODIFY_FILES, LEVEL_5_SECURITY
from core.autonomous.rollback import RollbackManager
from core.autonomous.task_manager import TaskManager, TaskStatus
from core.autonomous.test_runner import AutonomousTestRunner, TestResult
from core.plugin_loader import discover_plugins
from plugins.jarvis_autonomous import PLUGIN, run


class TestJarvisAutonomousEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.policy = PolicyEngine(data_dir=self.temp_dir / ".autonomous")
        self.approval = ApprovalManager()
        self.checkpoints = CheckpointManager(
            checkpoints_root=self.temp_dir / "checkpoints",
            project_root=self.temp_dir,
        )
        self.rollback = RollbackManager(
            checkpoint_mgr=self.checkpoints,
            project_root=self.temp_dir,
        )
        self.task_mgr = TaskManager(history_dir=self.temp_dir / "history")
        self.planner = AutonomousPlanner(
            project_root=self.temp_dir,
            policy_engine=self.policy,
        )
        self.diagnostics = DiagnosticsEngine(project_root=self.temp_dir)
        self.test_runner = AutonomousTestRunner(project_root=self.temp_dir)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_01_plugin_discovery_and_schema(self):
        """Test plugin declaration structure, schema, and loader discovery."""
        self.assertEqual(PLUGIN["name"], "jarvis_autonomous")
        self.assertIn("description", PLUGIN)
        self.assertEqual(PLUGIN["parameters"]["type"], "OBJECT")
        self.assertIn("action", PLUGIN["parameters"]["properties"])
        self.assertIn("action", PLUGIN["parameters"]["required"])

        # Test discovery in isolated plugin folder
        temp_plugins = self.temp_dir / "test_plugins"
        temp_plugins.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path("plugins") / "jarvis_autonomous.py", temp_plugins / "jarvis_autonomous.py")

        reg = discover_plugins(temp_plugins, {"open_app", "reminder"})
        self.assertTrue(reg.has("jarvis_autonomous"))
        decls = reg.get_tool_declarations()
        auto_decls = [d for d in decls if d["name"] == "jarvis_autonomous"]
        self.assertEqual(len(auto_decls), 1)

    def test_02_policy_default_safe_state(self):
        """Verify safe default settings (disabled by default, require approval)."""
        cfg = self.policy.config
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.require_approval)
        self.assertFalse(cfg.allow_dependency_installation)
        self.assertFalse(cfg.allow_git_operations)
        self.assertFalse(cfg.allow_network_access)
        self.assertEqual(cfg.max_auto_fix_attempts, 3)
        self.assertFalse(cfg.is_active())

    def test_03_enable_disable_toggle(self):
        """Test master enable, disable, pause, and resume."""
        res = self.policy.set_enabled(True)
        self.assertIn("ENABLED", res)
        self.assertTrue(self.policy.config.is_active())

        res = self.policy.set_paused(True)
        self.assertIn("PAUSED", res)
        self.assertFalse(self.policy.config.is_active())

        res = self.policy.set_paused(False)
        self.assertIn("RESUMED", res)
        self.assertTrue(self.policy.config.is_active())

        res = self.policy.set_enabled(False)
        self.assertIn("DISABLED", res)
        self.assertFalse(self.policy.config.is_active())

    def test_04_temporary_task_mode(self):
        """Test temporary enable mode and auto-reverting."""
        self.assertFalse(self.policy.config.enabled)
        self.policy.set_temporary_mode(True)
        self.assertTrue(self.policy.config.is_active())

        self.policy.revert_temporary_mode()
        self.assertFalse(self.policy.config.is_active())

    def test_05_secret_redaction(self):
        """Test that secrets, tokens, and API keys are redacted from text/diffs."""
        sample_text = (
            'gemini_api_key = "AIzaSyD1234567890abcdefghijklmnopqrst"\n'
            'token: "ghp_abcdef1234567890"\n'
            'password = "SuperSecretPassword123"\n'
        )
        redacted = PolicyEngine.redact_secrets(sample_text)
        self.assertNotIn("AIzaSyD1234567890abcdefghijklmnopqrst", redacted)
        self.assertNotIn("ghp_abcdef1234567890", redacted)
        self.assertNotIn("SuperSecretPassword123", redacted)
        self.assertIn("*******", redacted)

    def test_06_request_classification(self):
        """Test classification of various natural language developer requests."""
        cat1 = self.planner.classify_request("JARVIS, add a weather plugin to yourself.")
        self.assertEqual(cat1, RequestCategory.NEW_PLUGIN)

        cat2 = self.planner.classify_request("JARVIS, add a feature that lets you summarize PDFs.")
        self.assertEqual(cat2, RequestCategory.NEW_FEATURE)

        cat3 = self.planner.classify_request("JARVIS, fix the error in the WhatsApp voice bridge.")
        self.assertEqual(cat3, RequestCategory.BUG_FIX)

        cat4 = self.planner.classify_request("JARVIS, improve your media downloader.")
        self.assertEqual(cat4, RequestCategory.REFACTOR)

        cat5 = self.planner.classify_request("JARVIS, test whether the new plugin actually works.")
        self.assertEqual(cat5, RequestCategory.TESTING)

    def test_07_planner_inspection_and_plan(self):
        """Test planner creation of structured execution plan."""
        plan = self.planner.create_plan("TASK-20260923-001", "JARVIS, add a Telegram plugin.")
        self.assertEqual(plan.category, RequestCategory.NEW_PLUGIN)
        self.assertTrue(len(plan.steps) >= 3)
        self.assertTrue(any("plugins/telegram.py" in f for f in plan.files_to_create))
        self.assertIn("AUTONOMOUS TASK PLAN", plan.to_formatted_text())

    def test_08_approval_manager_flows(self):
        """Test multi-level approval flow: creation, approval, rejection, cancellation, and modify plan."""
        # 1. Approval
        req = self.approval.create_request(
            task_id="TASK-001",
            description="Add Telegram Plugin",
            approval_level=1,
            level_name="LEVEL 1 (Create files)",
            reasons=["Creates new files"],
            files_to_create=["plugins/telegram.py"],
        )
        self.assertIsNotNone(self.approval.pending_request)

        status, msg = self.approval.process_response("APPROVE")
        self.assertEqual(status, ApprovalStatus.APPROVED)
        self.assertIsNone(self.approval.pending_request)

        # 2. Rejection
        self.approval.create_request(
            task_id="TASK-002",
            description="Modify Core",
            approval_level=5,
            level_name="LEVEL 5 (Security)",
            reasons=["Security edit"],
        )
        status, msg = self.approval.process_response("NO")
        self.assertEqual(status, ApprovalStatus.REJECTED)

        # 3. Cancellation
        self.approval.create_request(
            task_id="TASK-003",
            description="Another Task",
            approval_level=1,
            level_name="LEVEL 1",
            reasons=["Edits"],
        )
        status, msg = self.approval.process_response("CANCEL")
        self.assertEqual(status, ApprovalStatus.CANCELLED)

        # 4. Modify Plan
        self.approval.create_request(
            task_id="TASK-004",
            description="Task 4",
            approval_level=1,
            level_name="LEVEL 1",
            reasons=["Edits"],
        )
        status, msg = self.approval.process_response("MODIFY PLAN")
        self.assertEqual(status, ApprovalStatus.MODIFY_REQUESTED)

    def test_09_checkpoint_creation_and_restoration(self):
        """Test that checkpoints capture pre-existing files and rollback restores them exactly."""
        # Create a sample file
        sample_file = self.temp_dir / "sample.py"
        sample_file.write_text("print('original version')", encoding="utf-8")

        new_file = self.temp_dir / "new_module.py"

        # Create checkpoint
        chk = self.checkpoints.create_checkpoint(
            task_id="TASK-TEST-001",
            affected_files=["sample.py", "new_module.py"],
            description="Test Checkpoint",
        )
        self.assertTrue(Path(chk.checkpoint_dir).exists())

        # Modify original and create new file
        sample_file.write_text("print('modified bad version')", encoding="utf-8")
        new_file.write_text("print('new file')", encoding="utf-8")

        # Rollback
        success, msg = self.rollback.execute_rollback("TASK-TEST-001")
        self.assertTrue(success)

        # Verify restored state
        self.assertEqual(sample_file.read_text(encoding="utf-8"), "print('original version')")
        self.assertFalse(new_file.exists())

    def test_10_task_manager_concurrency_lock(self):
        """Test that single-task concurrency locking prevents multiple simultaneous tasks."""
        created1, msg1, t1 = self.task_mgr.create_task("First task")
        self.assertTrue(created1)
        self.assertIsNotNone(t1)

        # Second task attempt while first is active
        created2, msg2, t2 = self.task_mgr.create_task("Second task")
        self.assertFalse(created2)
        self.assertIsNone(t2)
        self.assertIn("currently working on task", msg2)

        # Complete first task
        self.task_mgr.update_task_status(TaskStatus.COMPLETED)

        # Now second task can be created
        created3, msg3, t3 = self.task_mgr.create_task("Third task")
        self.assertTrue(created3)
        self.assertIsNotNone(t3)

    def test_11_diagnostics_classification(self):
        """Test diagnosis of test failure stack traces."""
        diag_syntax = self.diagnostics.diagnose_failure("SyntaxError: invalid syntax on line 12 in plugins/weather.py")
        self.assertEqual(diag_syntax.error_type, "syntax_error")

        diag_import = self.diagnostics.diagnose_failure("ModuleNotFoundError: No module named 'requests'")
        self.assertEqual(diag_import.error_type, "dependency_error")
        self.assertFalse(diag_import.is_low_risk)

        diag_assert = self.diagnostics.diagnose_failure("AssertionError: 4 != 5 in test_math.py")
        self.assertEqual(diag_assert.error_type, "assertion_error")

    def test_12_plugin_self_test_runner(self):
        """Test isolated self-test runner for plugin validation."""
        valid_plugin = self.temp_dir / "valid_plugin.py"
        valid_plugin.write_text(
            'PLUGIN = {"name": "valid_plugin", "description": "test", "parameters": {"type": "OBJECT"}}\n'
            'def run(params): return "OK"\n',
            encoding="utf-8"
        )
        res_valid = self.test_runner.self_test_plugin(str(valid_plugin))
        self.assertTrue(res_valid["valid"])

        invalid_plugin = self.temp_dir / "invalid_plugin.py"
        invalid_plugin.write_text('bad code syntax {{{', encoding="utf-8")
        res_invalid = self.test_runner.self_test_plugin(str(invalid_plugin))
        self.assertFalse(res_invalid["valid"])

    def test_13_emergency_stop_and_health_check(self):
        """Test emergency stop and health check status generation."""
        engine_inst = AutonomousEngine(project_root=self.temp_dir)
        health_out = engine_inst.health_check()
        self.assertIn("HEALTH:", health_out)

        # Test emergency stop
        engine_inst.policy.set_enabled(True)
        engine_inst.task_mgr.create_task("Emergency test task")
        self.assertIsNotNone(engine_inst.task_mgr.active_task)

        stopped, stop_msg = engine_inst.task_mgr.emergency_stop()
        self.assertTrue(stopped)
        self.assertIsNone(engine_inst.task_mgr.active_task)

    def test_14_run_dispatcher_actions(self):
        """Test plugin run() dispatcher actions."""
        # Status
        res = run({"action": "status"})
        self.assertIn("AUTONOMOUS DEVELOPMENT STATUS", res)

        # Health
        res = run({"action": "health_check"})
        self.assertIn("HEALTH", res)

        # Disabled request check
        res = run({"action": "request", "task_request": "add weather plugin"})
        self.assertTrue(isinstance(res, str))


if __name__ == "__main__":
    unittest.main()
