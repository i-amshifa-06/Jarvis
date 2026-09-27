"""
Rollback engine for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.checkpoint import CheckpointManager, CheckpointRecord


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = get_base_dir()


class RollbackManager:
    """Manages restoration of project files from checkpoints."""

    def __init__(self, checkpoint_mgr: Optional[CheckpointManager] = None, project_root: Optional[Path] = None):
        self.project_root = project_root or BASE_DIR
        self.checkpoint_mgr = checkpoint_mgr or CheckpointManager(project_root=self.project_root)

    def preview_rollback(self, task_id: str) -> Tuple[bool, str, List[str], List[str]]:
        """
        Preview what changes will be restored or removed upon rolling back task_id.
        Returns (valid, msg, files_to_restore, files_to_delete).
        """
        checkpoint = self.checkpoint_mgr.get_checkpoint(task_id)
        if not checkpoint:
            return False, f"Checkpoint for task '{task_id}' was not found.", [], []

        to_restore = []
        to_delete = []

        for f in checkpoint.files:
            if f.existed_before:
                to_restore.append(f.relative_path)
            else:
                to_delete.append(f.relative_path)

        preview_msg = (
            f"Rollback Preview for {task_id}:\n"
            f"• Files to restore: {len(to_restore)} ({', '.join(to_restore) if to_restore else 'None'})\n"
            f"• Files to remove (created during task): {len(to_delete)} ({', '.join(to_delete) if to_delete else 'None'})"
        )
        return True, preview_msg, to_restore, to_delete

    def execute_rollback(self, task_id: str) -> Tuple[bool, str]:
        """
        Restore project state to the specified checkpoint.
        """
        checkpoint = self.checkpoint_mgr.get_checkpoint(task_id)
        if not checkpoint:
            return False, f"Cannot rollback: Checkpoint for task '{task_id}' not found."

        checkpoint_dir = Path(checkpoint.checkpoint_dir)
        restored_count = 0
        deleted_count = 0
        errors = []

        for f in checkpoint.files:
            target_path = self.project_root / f.relative_path
            backup_path = checkpoint_dir / f.backup_filename

            try:
                if f.existed_before:
                    if backup_path.exists():
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_path, target_path)
                        restored_count += 1
                    else:
                        errors.append(f"Backup file missing for {f.relative_path}")
                else:
                    # File was newly created during the task, remove it
                    if target_path.exists():
                        if target_path.is_file():
                            target_path.unlink()
                        elif target_path.is_dir():
                            shutil.rmtree(target_path)
                        deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to restore {f.relative_path}: {e}")

        if errors:
            err_summary = "; ".join(errors)
            return False, f"Rollback for {task_id} completed with errors: {err_summary}"

        msg = f"Rollback for {task_id} successful. Restored {restored_count} file(s), removed {deleted_count} new file(s)."
        print(f"[JARVIS-AUTONOMOUS] {msg}")
        return True, msg
