"""
Failure diagnostics, root cause classification, and fix proposal generator
for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.policy import get_base_dir


@dataclass
class DiagnosticReport:
    error_type: str
    likely_cause: str
    affected_file: Optional[str]
    line_number: Optional[int]
    stack_trace: str
    proposed_fix: str
    fix_changes_required: List[str] = field(default_factory=list)
    is_low_risk: bool = True


class DiagnosticsEngine:
    """Diagnoses test failures and proposes minimal targeted fixes."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or get_base_dir()

    def classify_error(self, output: str) -> str:
        low = output.lower()
        if any(x in low for x in ("modulenotfounderror", "no module named")):
            return "dependency_error"
        if "syntaxerror" in low or "invalid syntax" in low:
            return "syntax_error"
        if "importerror" in low or "cannot import name" in low:
            return "import_error"
        if "assertionerror" in low:
            return "assertion_error"
        if "permissionerror" in low or "access is denied" in low:
            return "permission_error"
        if "timeout" in low or "timed out" in low:
            return "timeout_error"
        if any(x in low for x in ("typeerror", "valueerror", "attributeerror", "keyerror", "indexerror")):
            return "runtime_error"
        return "general_error"

    def parse_traceback_location(self, output: str) -> Tuple[Optional[str], Optional[int]]:
        matches = re.findall(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', output, re.IGNORECASE)
        for raw_path, line_str in reversed(matches):
            try:
                p = Path(raw_path)
                # Check if file belongs to workspace
                if self.project_root in p.parents or not p.is_absolute():
                    rel = p.relative_to(self.project_root) if p.is_absolute() else p
                    return str(rel).replace("\\", "/"), int(line_str)
            except Exception:
                pass
        if matches:
            last_path, last_line = matches[-1]
            return Path(last_path).name, int(last_line)
        return None, None

    def diagnose_failure(self, failure_text: str, context_files: Optional[List[str]] = None) -> DiagnosticReport:
        """Analyze test failure and construct a structured diagnostic report."""
        error_type = self.classify_error(failure_text)
        file_path, line_no = self.parse_traceback_location(failure_text)

        if not file_path and context_files:
            file_path = context_files[0]

        likely_cause = "Unknown runtime failure"
        proposed_fix = "Review stack trace and correct invalid logic."
        changes = []
        is_low_risk = True

        if error_type == "dependency_error":
            mod_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", failure_text, re.IGNORECASE)
            pkg = mod_match.group(1) if mod_match else "required_package"
            likely_cause = f"Missing dependency package '{pkg}'."
            proposed_fix = f"Install dependency '{pkg}' (requires explicit approval) or provide pure-Python fallback."
            changes = [f"Add fallback import or install {pkg}"]
            is_low_risk = False  # Dependency changes require separate approval

        elif error_type == "syntax_error":
            likely_cause = f"Syntax error at {file_path or 'code'}{f':{line_no}' if line_no else ''}."
            proposed_fix = f"Correct invalid Python syntax in '{file_path}'."
            changes = [f"Fix syntax on/around line {line_no or 'unknown'} in {file_path}"]

        elif error_type == "import_error":
            likely_cause = f"Import failure in {file_path or 'module'}."
            proposed_fix = f"Ensure imported symbol exists and resolve circular imports in '{file_path}'."
            changes = [f"Adjust import statement in {file_path}"]

        elif error_type == "assertion_error":
            likely_cause = f"Unit test assertion failed in test suite."
            proposed_fix = f"Align implementation output format with expected test assertions in '{file_path}'."
            changes = [f"Update function return value or fix logic in {file_path}"]

        elif error_type == "permission_error":
            likely_cause = "Filesystem permission restriction encountered."
            proposed_fix = "Use user-approved directory paths or appropriate permissions."
            changes = ["Adjust target path permissions"]
            is_low_risk = False

        else:
            likely_cause = f"Runtime exception occurred during execution in {file_path}."
            proposed_fix = f"Add null checks, exception handling, and input validation in '{file_path}'."
            changes = [f"Patch exception handler in {file_path}"]

        return DiagnosticReport(
            error_type=error_type,
            likely_cause=likely_cause,
            affected_file=file_path,
            line_number=line_no,
            stack_trace=failure_text,
            proposed_fix=proposed_fix,
            fix_changes_required=changes,
            is_low_risk=is_low_risk,
        )
