"""
JARVIS Autonomous Development Engine Plugin.

Provides a safe, approval-gated, and recoverable natural language interface for
JARVIS to inspect, plan, improve, test, diagnose, and expand its own plugins and features.

Rules:
- NEVER silently modifies source code without explicit approval.
- Disabled by default (safe opt-in configuration).
- Creates recoverable snapshot checkpoints before modifying any files.
- Automatically tests and validates changes, rolling back or diagnosing upon failure.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.autonomous.engine import AutonomousEngine

# Singleton engine instance
engine = AutonomousEngine()

# ---------------------------------------------------------------------------
# Plugin Declaration for JARVIS
# ---------------------------------------------------------------------------

PLUGIN = {
    "name": "jarvis_autonomous",
    "description": (
        "JARVIS Autonomous Development Engine. Use this tool when the user asks JARVIS to add new plugins, "
        "add new features, fix bugs, refactor code, improve existing plugins, run tests, diagnose errors, "
        "or manage autonomous development (enable, disable, pause, resume, status, rollback, health_check). "
        "Every change strictly requires user approval before modifying code."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action to perform: 'request' (plan a development task), 'enable' (turn on), 'disable' (turn off), "
                    "'pause', 'resume', 'status', 'approve' (approve pending plan), 'reject' (reject plan), "
                    "'cancel' (cancel task), 'rollback' (restore from checkpoint), 'health_check', 'test_plugin', "
                    "or 'emergency_stop'."
                ),
            },
            "task_request": {
                "type": "STRING",
                "description": "The natural language development request (e.g. 'add a weather plugin', 'fix whatsapp bridge').",
            },
            "task_id": {
                "type": "STRING",
                "description": "Optional task ID for rollback or status query (e.g. 'TASK-20260923-001').",
            },
            "approval_response": {
                "type": "STRING",
                "description": "User response to pending approval gate ('APPROVE', 'REJECT', 'CANCEL', 'MODIFY PLAN').",
            },
            "temporary": {
                "type": "BOOLEAN",
                "description": "Set to True if enabling/disabling only for the current task.",
            },
            "target_plugin": {
                "type": "STRING",
                "description": "Target plugin name or path for self-testing (e.g. 'plugins/weather.py').",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point for JARVIS plugin execution."""
    action = str(parameters.get("action", "status")).strip().lower()
    task_request = str(parameters.get("task_request", "")).strip()
    task_id = str(parameters.get("task_id", "")).strip()
    approval_resp = str(parameters.get("approval_response", "")).strip()
    temporary = bool(parameters.get("temporary", False))
    target_plugin = str(parameters.get("target_plugin", "")).strip()

    result_text = ""

    try:
        if action in ("request", "plan", "develop", "add", "fix", "improve"):
            req_text = task_request or parameters.get("request", "")
            if not req_text:
                result_text = "Please specify what you would like me to add, fix, or improve."
            else:
                result_text = engine.handle_request(req_text)

        elif action == "enable":
            if temporary:
                result_text = engine.policy.set_temporary_mode(True)
            else:
                result_text = engine.policy.set_enabled(True)

        elif action == "disable":
            if temporary:
                result_text = engine.policy.set_temporary_mode(False)
            else:
                result_text = engine.policy.set_enabled(False)

        elif action == "pause":
            result_text = engine.policy.set_paused(True)

        elif action == "resume":
            result_text = engine.policy.set_paused(False)

        elif action in ("status", "show_status"):
            result_text = engine.get_status()

        elif action in ("approve", "reject", "cancel", "modify"):
            resp = approval_resp or action.upper()
            result_text = engine.handle_approval_response(resp)

        elif action in ("rollback", "restore"):
            if not task_id:
                result_text = "Please provide the task ID to rollback (e.g. 'TASK-20260923-001')."
            else:
                result_text = engine.rollback_task(task_id)

        elif action in ("health_check", "health", "check"):
            result_text = engine.health_check()

        elif action in ("emergency_stop", "stop"):
            _, result_text = engine.task_mgr.emergency_stop()

        elif action in ("test_plugin", "test"):
            if not target_plugin:
                test_res = engine.test_runner.run_tests()
                result_text = f"Test execution completed: {test_res.passed_tests}/{test_res.total_tests} passed in {test_res.duration_seconds:.2f}s."
            else:
                self_test = engine.test_runner.self_test_plugin(target_plugin)
                if self_test.get("valid"):
                    result_text = f"Plugin '{target_plugin}' self-test PASSED (import: PASS, schema: PASS, execution: PASS)."
                else:
                    result_text = f"Plugin '{target_plugin}' self-test FAILED: {self_test.get('error', 'Unknown error')}"

        else:
            # Fallback: treat unknown action as request text if provided
            if task_request:
                result_text = engine.handle_request(task_request)
            else:
                result_text = f"Autonomous engine action '{action}' is not recognized. Valid actions: request, enable, disable, pause, resume, status, approve, reject, cancel, rollback, health_check, emergency_stop."

    except Exception as e:
        result_text = f"Sir, autonomous development engine encountered an error: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result_text}")
        except Exception:
            pass

    return result_text
