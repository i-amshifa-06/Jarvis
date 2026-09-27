"""
Checkpoint and snapshot backup system for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = get_base_dir()
CHECKPOINTS_DIR = BASE_DIR / ".autonomous" / "checkpoints"


@dataclass
class CheckpointFileRecord:
    relative_path: str
    original_sha256: str
    backup_filename: str
    existed_before: bool


@dataclass
class CheckpointRecord:
    task_id: str
    checkpoint_dir: str
    created_at: float
    description: str
    git_commit_hash: Optional[str]
    files: List[CheckpointFileRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "checkpoint_dir": self.checkpoint_dir,
            "created_at": self.created_at,
            "description": self.description,
            "git_commit_hash": self.git_commit_hash,
            "files": [
                {
                    "relative_path": f.relative_path,
                    "original_sha256": f.original_sha256,
                    "backup_filename": f.backup_filename,
                    "existed_before": f.existed_before,
                }
                for f in self.files
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CheckpointRecord:
        files = [
            CheckpointFileRecord(
                relative_path=f["relative_path"],
                original_sha256=f["original_sha256"],
                backup_filename=f["backup_filename"],
                existed_before=f["existed_before"],
            )
            for f in data.get("files", [])
        ]
        return cls(
            task_id=data["task_id"],
            checkpoint_dir=data["checkpoint_dir"],
            created_at=data["created_at"],
            description=data.get("description", ""),
            git_commit_hash=data.get("git_commit_hash"),
            files=files,
        )


class CheckpointManager:
    """Creates and manages recoverable task checkpoints before code modifications."""

    def __init__(self, checkpoints_root: Optional[Path] = None, project_root: Optional[Path] = None):
        self.project_root = project_root or BASE_DIR
        self.checkpoints_root = checkpoints_root or CHECKPOINTS_DIR
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)

    def _get_git_commit(self) -> Optional[str]:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def _calc_sha256(self, filepath: Path) -> str:
        if not filepath.exists():
            return ""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def create_checkpoint(
        self,
        task_id: str,
        affected_files: List[str],
        description: str = "",
    ) -> CheckpointRecord:
        """
        Create a recoverable snapshot checkpoint for the given task and list of affected files.
        """
        task_checkpoint_dir = self.checkpoints_root / task_id
        task_checkpoint_dir.mkdir(parents=True, exist_ok=True)

        backup_files: List[CheckpointFileRecord] = []
        git_hash = self._get_git_commit()

        for rel_path in affected_files:
            clean_rel = rel_path.replace("\\", "/").lstrip("/")
            src_file = self.project_root / clean_rel

            existed = src_file.exists()
            file_hash = self._calc_sha256(src_file) if existed else ""

            # Unique backup filename preserving path structure in safe format
            backup_fname = clean_rel.replace("/", "__").replace("\\", "__") + ".bak"
            backup_dst = task_checkpoint_dir / backup_fname

            if existed:
                shutil.copy2(src_file, backup_dst)

            backup_files.append(
                CheckpointFileRecord(
                    relative_path=clean_rel,
                    original_sha256=file_hash,
                    backup_filename=backup_fname,
                    existed_before=existed,
                )
            )

        record = CheckpointRecord(
            task_id=task_id,
            checkpoint_dir=str(task_checkpoint_dir),
            created_at=time.time(),
            description=description,
            git_commit_hash=git_hash,
            files=backup_files,
        )

        manifest_file = task_checkpoint_dir / "checkpoint.json"
        manifest_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        print(f"[JARVIS-AUTONOMOUS] Checkpoint created for {task_id} at {task_checkpoint_dir}")
        return record

    def get_checkpoint(self, task_id: str) -> Optional[CheckpointRecord]:
        task_checkpoint_dir = self.checkpoints_root / task_id
        manifest_file = task_checkpoint_dir / "checkpoint.json"
        if not manifest_file.exists():
            return None
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            return CheckpointRecord.from_dict(data)
        except Exception as e:
            print(f"[JARVIS-AUTONOMOUS] Failed to load checkpoint for {task_id}: {e}")
            return None

    def list_checkpoints(self) -> List[str]:
        if not self.checkpoints_root.exists():
            return []
        dirs = [d.name for d in self.checkpoints_root.iterdir() if d.is_dir()]
        return sorted(dirs, reverse=True)
