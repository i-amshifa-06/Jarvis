"""
Task lifecycle, single-task concurrency locking, and persistent task history
for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.planner import AutonomousPlan, RequestCategory
from core.autonomous.policy import get_base_dir


class TaskStatus(str, Enum):
    INITIALIZING = "INITIALIZING"
    INSPECTING = "INSPECTING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    CHECKPOINTING = "CHECKPOINTING"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    DIAGNOSING = "DIAGNOSING"
    RETESTING = "RETESTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class AutonomousTask:
    task_id: str
    request: str
    category: str
    status: TaskStatus = TaskStatus.INITIALIZING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    plan: Optional[Dict[str, Any]] = None
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    failures: List[str] = field(default_factory=list)
    fix_attempts: int = 0
    error_message: str = ""
    checkpoint_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request": self.request,
            "category": self.category,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "plan": self.plan,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "dependencies": self.dependencies,
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "failures": self.failures,
            "fix_attempts": self.fix_attempts,
            "error_message": self.error_message,
            "checkpoint_dir": self.checkpoint_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AutonomousTask:
        return cls(
            task_id=data["task_id"],
            request=data["request"],
            category=data.get("category", "UNKNOWN"),
            status=TaskStatus(data.get("status", TaskStatus.INITIALIZING.value)),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
            plan=data.get("plan"),
            files_created=data.get("files_created", []),
            files_modified=data.get("files_modified", []),
            files_deleted=data.get("files_deleted", []),
            dependencies=data.get("dependencies", []),
            tests_run=data.get("tests_run", 0),
            tests_passed=data.get("tests_passed", 0),
            tests_failed=data.get("tests_failed", 0),
            failures=data.get("failures", []),
            fix_attempts=data.get("fix_attempts", 0),
            error_message=data.get("error_message", ""),
            checkpoint_dir=data.get("checkpoint_dir", ""),
        )


class TaskManager:
    """Manages active task state, task lock, and history persistence."""

    def __init__(self, history_dir: Optional[Path] = None):
        base_dir = get_base_dir()
        self.history_dir = history_dir or (base_dir / ".autonomous" / "history")
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.history_dir / "tasks.json"

        self._lock = threading.Lock()
        self._active_task: Optional[AutonomousTask] = None
        self._task_history: List[AutonomousTask] = self._load_history()

    def _load_history(self) -> List[AutonomousTask]:
        if not self.history_file.exists():
            return []
        try:
            raw = json.loads(self.history_file.read_text(encoding="utf-8"))
            return [AutonomousTask.from_dict(item) for item in raw]
        except Exception as e:
            print(f"[JARVIS-AUTONOMOUS] Failed to load task history: {e}")
            return []

    def _save_history(self) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            data = [t.to_dict() for t in self._task_history]
            self.history_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[JARVIS-AUTONOMOUS] Failed to save task history: {e}")

    @property
    def active_task(self) -> Optional[AutonomousTask]:
        with self._lock:
            return self._active_task

    def create_task(self, request_text: str, category: str = "UNKNOWN") -> Tuple[bool, str, Optional[AutonomousTask]]:
        """
        Create a new autonomous task if no other task is currently running.
        """
        with self._lock:
            if self._active_task is not None and self._active_task.status not in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.REJECTED,
                TaskStatus.ROLLED_BACK,
            ):
                return False, f"JARVIS is currently working on task {self._active_task.task_id}. Would you like to queue this task?", None

            task_date = time.strftime("%Y%m%d")
            task_num = len(self._task_history) + 1
            task_id = f"TASK-{task_date}-{task_num:03d}"

            task = AutonomousTask(
                task_id=task_id,
                request=request_text,
                category=category,
                status=TaskStatus.INITIALIZING,
            )
            self._active_task = task
            return True, f"Created task {task_id}.", task

    def update_task_status(self, status: TaskStatus, error_message: str = "") -> None:
        with self._lock:
            if self._active_task:
                self._active_task.status = status
                if error_message:
                    self._active_task.error_message = error_message
                if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED, TaskStatus.ROLLED_BACK):
                    self._active_task.completed_at = time.time()
                    self._task_history.append(self._active_task)
                    self._save_history()
                    self._active_task = None

    def emergency_stop(self) -> Tuple[bool, str]:
        """Immediately stop and cancel any currently active autonomous task."""
        with self._lock:
            if not self._active_task:
                return True, "No autonomous development task is currently active."

            task_id = self._active_task.task_id
            self._active_task.status = TaskStatus.CANCELLED
            self._active_task.error_message = "Emergency stop triggered by user."
            self._active_task.completed_at = time.time()
            self._task_history.append(self._active_task)
            self._save_history()
            self._active_task = None

            return True, f"Autonomous task {task_id} has been stopped immediately. State and checkpoints preserved."

    def get_task_by_id(self, task_id: str) -> Optional[AutonomousTask]:
        with self._lock:
            if self._active_task and self._active_task.task_id == task_id:
                return self._active_task
            for t in reversed(self._task_history):
                if t.task_id == task_id:
                    return t
        return None

    def get_history_summary(self, limit: int = 5) -> str:
        with self._lock:
            if not self._task_history and not self._active_task:
                return "No autonomous development tasks recorded."

            lines = ["JARVIS Autonomous Task History:"]
            if self._active_task:
                lines.append(f"• [ACTIVE] {self._active_task.task_id} | {self._active_task.status.value} | \"{self._active_task.request}\"")

            for t in list(reversed(self._task_history))[:limit]:
                date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.created_at))
                files_cnt = len(t.files_created) + len(t.files_modified)
                test_str = f"{t.tests_passed}/{t.tests_run} tests" if t.tests_run > 0 else "no tests"
                lines.append(f"• [{t.status.value}] {t.task_id} ({date_str}) | {t.request} | {files_cnt} files | {test_str}")

            return "\n".join(lines)
