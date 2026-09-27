"""
Test runner and plugin verification system for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.policy import get_base_dir


@dataclass
class TestResult:
    success: bool
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    failure_details: List[str] = field(default_factory=list)


class AutonomousTestRunner:
    """Runs project tests, targeted tests, and plugin runtime self-tests."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or get_base_dir()
        self._python_exe = sys.executable

    def _parse_unittest_output(self, output: str) -> Tuple[int, int, int, int, List[str]]:
        """Extract test counts and failure traces from unittest runner output."""
        total = 0
        failed = 0
        errors = 0
        passed = 0
        failures = []

        # Match "Ran X tests in Ys"
        match_ran = re.search(r"Ran (\d+) tests? in", output)
        if match_ran:
            total = int(match_ran.group(1))

        # Match FAILED (failures=X, errors=Y)
        match_failed = re.search(r"FAILED \((?:failures=(\d+))?,?\s*(?:errors=(\d+))?\)", output)
        if match_failed:
            f_count = int(match_failed.group(1) or 0)
            e_count = int(match_failed.group(2) or 0)
            failed = f_count
            errors = e_count

        passed = max(0, total - failed - errors)

        # Extract FAIL / ERROR blocks
        blocks = re.findall(r"(?:FAIL|ERROR): [^\n]+\n(?:-+|\=+)\n[\s\S]+?(?=\n(?:FAIL|ERROR|\-+|\=+)|$)", output)
        for b in blocks:
            failures.append(b.strip())

        return total, passed, failed, errors, failures

    def run_tests(self, target_pattern: Optional[str] = None, timeout: int = 120) -> TestResult:
        """
        Run test discovery or specific targeted test file.
        """
        start = time.time()
        cmd = [self._python_exe, "-m", "unittest"]

        if target_pattern:
            cmd.append(target_pattern)
        else:
            cmd.extend(["discover", "tests"])

        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            elapsed = time.time() - start
            combined_output = f"{res.stdout}\n{res.stderr}".strip()
            total, passed, failed, errors, failure_list = self._parse_unittest_output(combined_output)

            success = (res.returncode == 0) and (failed == 0) and (errors == 0)

            return TestResult(
                success=success,
                total_tests=total,
                passed_tests=passed,
                failed_tests=failed,
                errors=errors,
                duration_seconds=elapsed,
                stdout=res.stdout,
                stderr=res.stderr,
                failure_details=failure_list,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                success=False,
                duration_seconds=time.time() - start,
                stderr="Test execution timed out.",
                failure_details=["Test runner timed out after 120 seconds."],
            )
        except Exception as e:
            return TestResult(
                success=False,
                duration_seconds=time.time() - start,
                stderr=str(e),
                failure_details=[f"Test runner error: {e}"],
            )

    def self_test_plugin(self, plugin_rel_path: str) -> Dict[str, Any]:
        """
        Verify that a newly generated or modified plugin is valid:
        1. Import check
        2. Schema check (PLUGIN dict, parameters, valid name, description)
        3. Entry point check (callable run())
        4. Synthetic execution check with dummy input
        """
        full_path = self.project_root / plugin_rel_path.replace("\\", "/").lstrip("/")
        if not full_path.exists():
            return {
                "valid": False,
                "error": f"Plugin file '{plugin_rel_path}' does not exist.",
                "stages": {"import": "FAIL", "schema": "SKIP", "run": "SKIP"},
            }

        # Run validation in isolated subprocess to avoid polluting current process space
        check_code = f"""
import sys
import importlib.util
from pathlib import Path

path = Path(r'{full_path}')
spec = importlib.util.spec_from_file_location("test_plugin", path)
if not spec or not spec.loader:
    print("ERR: Spec could not be loaded")
    sys.exit(1)

mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"ERR_IMPORT: {{e}}")
    sys.exit(2)

meta = getattr(mod, "PLUGIN", None)
if not isinstance(meta, dict):
    print("ERR_SCHEMA: PLUGIN dict missing")
    sys.exit(3)

if not meta.get("name") or not isinstance(meta.get("description"), str):
    print("ERR_SCHEMA: Invalid name or description")
    sys.exit(4)

run_fn = getattr(mod, "run", None)
if not callable(run_fn):
    print("ERR_SCHEMA: Missing callable run(parameters) entry point")
    sys.exit(5)

try:
    # Test synthetic call
    out = run_fn({{"action": "status"}})
    print(f"SUCCESS: {{out}}")
except Exception as e:
    print(f"ERR_RUN: {{e}}")
    sys.exit(6)
"""
        try:
            res = subprocess.run(
                [self._python_exe, "-c", check_code],
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="replace",
            )
            combined = f"{res.stdout}\n{res.stderr}".strip()
            if res.returncode == 0 and "SUCCESS:" in combined:
                return {
                    "valid": True,
                    "stages": {"import": "PASS", "schema": "PASS", "run": "PASS"},
                    "output": combined,
                }
            else:
                return {
                    "valid": False,
                    "error": combined,
                    "stages": {
                        "import": "FAIL" if "ERR_IMPORT" in combined else "PASS",
                        "schema": "FAIL" if "ERR_SCHEMA" in combined else "PASS",
                        "run": "FAIL" if "ERR_RUN" in combined else "PASS",
                    },
                }
        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
                "stages": {"import": "ERROR", "schema": "ERROR", "run": "ERROR"},
            }
