"""
Security policy, permission levels, and configuration management for the
JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = get_base_dir()
AUTONOMOUS_DATA_DIR = BASE_DIR / ".autonomous"
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"
POLICY_STATE_FILE = AUTONOMOUS_DATA_DIR / "policy_state.json"

# Approval Levels 0 to 7
LEVEL_0_INSPECT = 0        # Read-only inspection
LEVEL_1_MODIFY_FILES = 1   # Create/modify source files
LEVEL_2_DEPENDENCIES = 2   # Install dependencies
LEVEL_3_NETWORK = 3        # Network/API access
LEVEL_4_EXECUTE_CODE = 4   # Execute generated code
LEVEL_5_SECURITY = 5       # Modify security-sensitive code
LEVEL_6_CORE_ARCH = 6      # Modify JARVIS core architecture
LEVEL_7_GIT_OPERATIONS = 7 # Git commit / push

LEVEL_NAMES = {
    0: "LEVEL 0 (Read-only inspection)",
    1: "LEVEL 1 (Create/modify source files)",
    2: "LEVEL 2 (Install dependencies)",
    3: "LEVEL 3 (Network/API access)",
    4: "LEVEL 4 (Execute generated code)",
    5: "LEVEL 5 (Modify security-sensitive code)",
    6: "LEVEL 6 (Modify JARVIS core architecture)",
    7: "LEVEL 7 (Git commit/push)",
}

# Protected security-critical and core files
PROTECTED_FILES = {
    "config/api_keys.json",
    "config/api_keys.json.example",
    ".env",
    "core/autonomous/policy.py",
    "core/autonomous/approval.py",
    "core/autonomous/engine.py",
}

# Core architecture directories
CORE_ARCH_PATHS = {
    "core/",
    "main.py",
    "ui.py",
    "core/plugin_loader.py",
}

SECRET_PATTERNS = [
    re.compile(r'(api[_-]?key\s*[:=]\s*["\'])([^"\']{6,})(["\'])', re.IGNORECASE),
    re.compile(r'(token\s*[:=]\s*["\'])([^"\']{6,})(["\'])', re.IGNORECASE),
    re.compile(r'(password\s*[:=]\s*["\'])([^"\']{4,})(["\'])', re.IGNORECASE),
    re.compile(r'(secret\s*[:=]\s*["\'])([^"\']{6,})(["\'])', re.IGNORECASE),
    re.compile(r'(bearer\s+)([a-zA-Z0-9_\-\.]{10,})', re.IGNORECASE),
    re.compile(r'(AIza[0-9A-Za-z\-_]{35})'),
]


@dataclass
class AutonomousConfig:
    enabled: bool = False
    require_approval: bool = True
    allow_code_changes: bool = True
    allow_test_execution: bool = True
    allow_dependency_installation: bool = False
    allow_git_operations: bool = False
    allow_network_access: bool = False
    max_auto_fix_attempts: int = 3
    auto_fix: bool = False
    paused: bool = False
    # Temporary overrides for single-task runs
    temporary_override: Optional[bool] = None
    previous_state: Optional[bool] = None

    def is_active(self) -> bool:
        if self.paused:
            return False
        if self.temporary_override is not None:
            return self.temporary_override
        return self.enabled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "require_approval": self.require_approval,
            "allow_code_changes": self.allow_code_changes,
            "allow_test_execution": self.allow_test_execution,
            "allow_dependency_installation": self.allow_dependency_installation,
            "allow_git_operations": self.allow_git_operations,
            "allow_network_access": self.allow_network_access,
            "max_auto_fix_attempts": self.max_auto_fix_attempts,
            "auto_fix": self.auto_fix,
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AutonomousConfig:
        return cls(
            enabled=bool(data.get("enabled", False)),
            require_approval=bool(data.get("require_approval", True)),
            allow_code_changes=bool(data.get("allow_code_changes", True)),
            allow_test_execution=bool(data.get("allow_test_execution", True)),
            allow_dependency_installation=bool(data.get("allow_dependency_installation", False)),
            allow_git_operations=bool(data.get("allow_git_operations", False)),
            allow_network_access=bool(data.get("allow_network_access", False)),
            max_auto_fix_attempts=int(data.get("max_auto_fix_attempts", 3)),
            auto_fix=bool(data.get("auto_fix", False)),
            paused=bool(data.get("paused", False)),
        )


class PolicyEngine:
    """Manages autonomous development permissions, security gates, and config state."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or AUTONOMOUS_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "policy_state.json"
        self._config = self._load_config()

    def _load_config(self) -> AutonomousConfig:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return AutonomousConfig.from_dict(data)
            except Exception:
                pass

        # Check in api_keys.json as fallback
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                auto_cfg = data.get("autonomous_development", {})
                if isinstance(auto_cfg, dict) and auto_cfg:
                    return AutonomousConfig.from_dict(auto_cfg)
            except Exception:
                pass

        # Safe defaults: enabled = False, require_approval = True
        return AutonomousConfig()

    def save_config(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self._config.to_dict(), indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[JARVIS-AUTONOMOUS] Failed to save policy config: {e}")

    @property
    def config(self) -> AutonomousConfig:
        return self._config

    def set_enabled(self, enabled: bool) -> str:
        self._config.enabled = enabled
        self._config.temporary_override = None
        self._config.paused = False
        self.save_config()
        state = "ENABLED" if enabled else "DISABLED"
        return f"Autonomous development is now {state}."

    def set_paused(self, paused: bool) -> str:
        self._config.paused = paused
        self.save_config()
        state = "PAUSED" if paused else "RESUMED"
        return f"Autonomous development is now {state}."

    def set_temporary_mode(self, enabled: bool) -> str:
        self._config.previous_state = self._config.enabled
        self._config.temporary_override = enabled
        state = "ENABLED" if enabled else "DISABLED"
        return f"Autonomous development is temporarily {state} for this task only."

    def revert_temporary_mode(self) -> None:
        if self._config.temporary_override is not None:
            self._config.temporary_override = None
            self._config.previous_state = None

    def assess_approval_level(
        self,
        files_to_create: list[str],
        files_to_modify: list[str],
        dependencies: list[str],
        requires_network: bool = False,
        requires_git: bool = False,
    ) -> Tuple[int, list[str]]:
        """
        Determine the maximum required approval level and specific elevation reasons.
        """
        level = LEVEL_0_INSPECT
        reasons = []

        all_files = [f.replace("\\", "/") for f in (files_to_create + files_to_modify)]

        if all_files:
            level = max(level, LEVEL_1_MODIFY_FILES)
            reasons.append(f"Modifies source files: {', '.join(all_files)}")

        if dependencies:
            level = max(level, LEVEL_2_DEPENDENCIES)
            reasons.append(f"Requires dependency installation: {', '.join(dependencies)}")

        if requires_network:
            level = max(level, LEVEL_3_NETWORK)
            reasons.append("Requires external network or API access")

        # Check for core architecture files
        for f in all_files:
            if any(f.startswith(arch) or f == arch.rstrip("/") for arch in CORE_ARCH_PATHS):
                level = max(level, LEVEL_6_CORE_ARCH)
                reasons.append(f"Modifies core JARVIS architecture component '{f}'")
                break

        # Check for security-sensitive protected files
        for f in all_files:
            if any(f == prot or f.endswith("/" + prot) for prot in PROTECTED_FILES):
                level = max(level, LEVEL_5_SECURITY)
                reasons.append(f"Modifies security-critical file '{f}'")
                break

        if requires_git:
            level = max(level, LEVEL_7_GIT_OPERATIONS)
            reasons.append("Performs Git commit/push operations")

        return level, reasons

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Sanitize secrets, API keys, tokens, and credentials from text and diffs."""
        if not text:
            return ""
        result = text
        for pat in SECRET_PATTERNS:
            def _repl(match):
                if len(match.groups()) == 3:
                    prefix, secret, suffix = match.group(1), match.group(2), match.group(3)
                    masked = secret[:2] + "*******" + secret[-2:] if len(secret) > 4 else "*******"
                    return f"{prefix}{masked}{suffix}"
                elif len(match.groups()) == 2:
                    prefix, secret = match.group(1), match.group(2)
                    masked = secret[:2] + "*******" + secret[-2:] if len(secret) > 4 else "*******"
                    return f"{prefix}{masked}"
                else:
                    secret = match.group(0)
                    return secret[:2] + "*******" + secret[-2:] if len(secret) > 4 else "*******"
            result = pat.sub(_repl, result)
        return result
