"""
Request classification, codebase inspection, and plan generation for the
JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.policy import PolicyEngine, LEVEL_NAMES, get_base_dir


class RequestCategory(str, Enum):
    NEW_PLUGIN = "NEW_PLUGIN"
    NEW_FEATURE = "NEW_FEATURE"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"
    PERFORMANCE = "PERFORMANCE"
    SECURITY_FIX = "SECURITY_FIX"
    TESTING = "TESTING"
    DEPENDENCY_CHANGE = "DEPENDENCY_CHANGE"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    DOCUMENTATION = "DOCUMENTATION"
    UNKNOWN = "UNKNOWN"


@dataclass
class CodebaseContext:
    existing_plugins: List[str]
    existing_actions: List[str]
    installed_packages: List[str]
    config_keys: List[str]
    test_files: List[str]


@dataclass
class AutonomousPlan:
    task_id: str
    request: str
    category: RequestCategory
    summary: str
    steps: List[str]
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    files_to_delete: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    risk_level: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL
    risk_reasons: List[str] = field(default_factory=list)
    approval_level: int = 1
    level_name: str = ""
    diff_preview: str = ""

    def to_formatted_text(self) -> str:
        lines = [
            "==================================================",
            "AUTONOMOUS TASK PLAN",
            "==================================================",
            f"Task ID: {self.task_id}",
            f"Request: \"{self.request}\"",
            f"Classification: {self.category.value}",
            f"Approval Level: {self.level_name}",
            f"Risk Level: {self.risk_level}",
            "",
            "Plan Steps:",
        ]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"  {i}. {step}")

        lines.extend([
            "",
            f"Estimated Changes:",
            f"  • Files to create ({len(self.files_to_create)}): {', '.join(self.files_to_create) if self.files_to_create else 'None'}",
            f"  • Files to modify ({len(self.files_to_modify)}): {', '.join(self.files_to_modify) if self.files_to_modify else 'None'}",
            f"  • Files to delete ({len(self.files_to_delete)}): {', '.join(self.files_to_delete) if self.files_to_delete else 'None'}",
            f"  • Dependencies to install ({len(self.dependencies)}): {', '.join(self.dependencies) if self.dependencies else 'None'}",
        ])

        if self.risk_reasons:
            lines.append("")
            lines.append("Risk Analysis:")
            for r in self.risk_reasons:
                lines.append(f"  • {r}")

        if self.diff_preview:
            lines.append("")
            lines.append("Planned Diff Preview:")
            lines.append(self.diff_preview)

        lines.append("==================================================")
        return "\n".join(lines)


class AutonomousPlanner:
    """Classifies requests, inspects project structure, and drafts task plans."""

    def __init__(self, project_root: Optional[Path] = None, policy_engine: Optional[PolicyEngine] = None):
        self.project_root = project_root or get_base_dir()
        self.policy_engine = policy_engine or PolicyEngine()

    def classify_request(self, request_text: str) -> RequestCategory:
        """Classify user natural language request."""
        low = request_text.lower().strip()

        # Testing
        if any(p in low for p in ["test ", "testing", "run test", "verify plugin", "check if it works", "test whether", "test the"]):
            return RequestCategory.TESTING

        # Plugin creation: e.g. "add a weather plugin", "create telegram plugin", "new plugin for X"
        if re.search(r"\b(add|create|new|build|make)\s+(?:a\s+|an\s+)?(?:[a-zA-Z0-9_\-]+\s+)?plugin\b", low) or (
            "plugin" in low and any(w in low for w in ("add", "create", "build", "make", "new"))
        ):
            return RequestCategory.NEW_PLUGIN

        # Bug fixing / troubleshooting
        if any(p in low for p in ["fix ", "solve error", "debug ", "investigate why", "resolve issue", "broken", "repair", "error in", "bug in", "failing"]):
            return RequestCategory.BUG_FIX

        # Performance & Optimization
        if any(p in low for p in ["optimize", "improve speed", "performance", "faster", "reduce memory"]):
            return RequestCategory.PERFORMANCE

        # Refactoring & improvement
        if any(p in low for p in ["refactor", "improve ", "clean up", "restructure", "enhance "]):
            return RequestCategory.REFACTOR

        # Security
        if any(p in low for p in ["security", "vulnerability", "sanitize", "patch exploit", "harden"]):
            return RequestCategory.SECURITY_FIX

        # Dependency
        if any(p in low for p in ["install package", "install dependency", "pip install", "add dependency"]):
            return RequestCategory.DEPENDENCY_CHANGE

        # Configuration
        if any(p in low for p in ["configure", "change config", "update settings", "change voice", "set api key"]):
            return RequestCategory.CONFIGURATION_CHANGE

        # Documentation
        if any(p in low for p in ["document", "readme", "write docs", "add docstring"]):
            return RequestCategory.DOCUMENTATION

        # New feature
        if re.search(r"\b(add|create|new|build)\s+(?:a\s+|an\s+)?(?:[a-zA-Z0-9_\-]+\s+)?feature\b", low) or any(
            p in low for p in ["add a feature", "add feature", "create feature", "support ", "enable "]
        ):
            return RequestCategory.NEW_FEATURE

        return RequestCategory.UNKNOWN

    def inspect_codebase(self) -> CodebaseContext:
        """Gather current JARVIS architectural context."""
        plugins_dir = self.project_root / "plugins"
        actions_dir = self.project_root / "actions"
        tests_dir = self.project_root / "tests"
        req_file = self.project_root / "requirements.txt"
        config_file = self.project_root / "config" / "api_keys.json"

        plugins = []
        if plugins_dir.exists():
            plugins = [p.stem for p in plugins_dir.glob("*.py") if not p.name.startswith("_")]

        actions = []
        if actions_dir.exists():
            actions = [a.stem for a in actions_dir.glob("*.py") if not a.name.startswith("_")]

        tests = []
        if tests_dir.exists():
            tests = [t.name for t in tests_dir.glob("test_*.py")]

        packages = []
        if req_file.exists():
            try:
                for line in req_file.read_text(encoding="utf-8").splitlines():
                    clean = line.strip()
                    if clean and not clean.startswith("#"):
                        packages.append(clean.split("==")[0].split(">=")[0].strip())
            except Exception:
                pass

        config_keys = []
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                config_keys = list(data.keys())
            except Exception:
                pass

        return CodebaseContext(
            existing_plugins=plugins,
            existing_actions=actions,
            installed_packages=packages,
            config_keys=config_keys,
            test_files=tests,
        )

    def create_plan(self, task_id: str, request_text: str) -> AutonomousPlan:
        """
        Synthesize inspection and build a complete execution plan.
        """
        category = self.classify_request(request_text)
        context = self.inspect_codebase()

        files_to_create: List[str] = []
        files_to_modify: List[str] = []
        files_to_delete: List[str] = []
        dependencies: List[str] = []
        steps: List[str] = []
        risk_reasons: List[str] = []
        risk_level = "LOW"

        # Derive subject/target name
        low_req = request_text.lower()
        target_name = ""

        # Pattern 1: "add a [telegram] plugin" / "create [weather] plugin"
        m1 = re.search(r"\b(?:add|create|new|build|make)\s+(?:a\s+|an\s+)?([a-zA-Z0-9_]+)\s+plugin\b", low_req)
        if m1:
            target_name = m1.group(1).strip()

        # Pattern 2: "plugin for [telegram]" / "plugin named [telegram]"
        if not target_name:
            m2 = re.search(r"\bplugin\s+(?:for\s+|named\s+|called\s+)?([a-zA-Z0-9_]+)", low_req)
            if m2:
                target_name = m2.group(1).strip()

        # Pattern 3: "feature for [pdf_summarizer]" / "fix [whatsapp_voice_bridge]"
        if not target_name:
            m3 = re.search(r"(?:feature|bridge|component|downloader|module)\s+(?:for\s+|named\s+|called\s+)?([a-zA-Z0-9_]+)", low_req)
            if m3:
                target_name = m3.group(1).strip()

        # Fallback: non-stopwords
        if not target_name:
            stopwords = {"jarvis", "please", "could", "would", "plugin", "feature", "yourself", "create", "about", "this", "that", "error"}
            words = [w for w in re.sub(r"[^a-zA-Z0-9_ ]", "", low_req).split() if len(w) > 2 and w not in stopwords]
            target_name = words[-1] if words else "custom_module"

        target_slug = re.sub(r"[^a-zA-Z0-9_]", "_", target_name.lower())

        if category == RequestCategory.NEW_PLUGIN:
            plugin_file = f"plugins/{target_slug}.py"
            test_file = f"tests/test_{target_slug}.py"
            files_to_create = [plugin_file, test_file]
            steps = [
                f"Inspect existing plugin architecture and plugin loader interfaces.",
                f"Create plugin '{plugin_file}' implementing PLUGIN declaration and callable run() entry point.",
                f"Create unit test '{test_file}' verifying schema, isolation, and functionality.",
                f"Execute project tests and verify plugin discovery.",
                f"Run self-test on '{plugin_file}' and report result.",
            ]
            risk_level = "MEDIUM"
            risk_reasons.append("Creates new runtime plugin entry point in plugins/ directory.")

        elif category == RequestCategory.BUG_FIX:
            # Check if target matches existing plugin or action
            matched_file = ""
            for p in context.existing_plugins:
                if p in low_req or target_slug in p:
                    matched_file = f"plugins/{p}.py"
                    break
            if not matched_file:
                for a in context.existing_actions:
                    if a in low_req or target_slug in a:
                        matched_file = f"actions/{a}.py"
                        break
            if not matched_file:
                matched_file = f"plugins/{target_slug}.py"

            files_to_modify = [matched_file]
            steps = [
                f"Inspect '{matched_file}' to isolate root cause of reported issue.",
                f"Draft minimal targeted patch preserving existing functionality.",
                f"Apply bug fix to '{matched_file}'.",
                f"Run relevant test suites to verify fix and check for regressions.",
                f"Report verification results.",
            ]
            risk_level = "MEDIUM"
            risk_reasons.append(f"Modifies existing code in '{matched_file}'.")

        elif category == RequestCategory.TESTING:
            steps = [
                f"Discover test files for '{target_slug}'.",
                f"Execute targeted unit tests and runtime self-tests.",
                f"Evaluate test outcomes, error logs, and coverage.",
                f"Report comprehensive pass/fail summary.",
            ]
            risk_level = "LOW"

        else:
            # General feature / refactor
            target_file = f"plugins/{target_slug}.py"
            files_to_modify = [target_file]
            steps = [
                f"Inspect codebase context for request '{request_text}'.",
                f"Prepare implementation changes for '{target_file}'.",
                f"Apply minimal verified edits.",
                f"Execute test suite to prevent regressions.",
                f"Report final outcome.",
            ]
            risk_level = "MEDIUM"

        level, reasons = self.policy_engine.assess_approval_level(
            files_to_create=files_to_create,
            files_to_modify=files_to_modify,
            dependencies=dependencies,
        )

        diff_summary = ""
        if files_to_create or files_to_modify:
            diff_lines = []
            for f in files_to_create:
                diff_lines.append(f"+ [NEW FILE] {f}")
            for f in files_to_modify:
                diff_lines.append(f"~ [MODIFY]   {f}")
            diff_summary = "\n".join(diff_lines)

        plan = AutonomousPlan(
            task_id=task_id,
            request=request_text,
            category=category,
            summary=f"{category.value} for '{target_slug}'",
            steps=steps,
            files_to_create=files_to_create,
            files_to_modify=files_to_modify,
            files_to_delete=files_to_delete,
            dependencies=dependencies,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            approval_level=level,
            level_name=LEVEL_NAMES.get(level, f"LEVEL {level}"),
            diff_preview=diff_summary,
        )
        return plan
