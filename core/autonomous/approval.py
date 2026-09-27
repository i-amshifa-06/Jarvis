"""
Approval manager and gatekeeper for the JARVIS Autonomous Development Engine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    MODIFY_REQUESTED = "MODIFY_REQUESTED"
    EXPIRED = "EXPIRED"


@dataclass
class ApprovalRequest:
    request_id: str
    task_id: str
    description: str
    approval_level: int
    level_name: str
    reasons: List[str]
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    files_to_delete: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    diff_preview: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    responded_at: Optional[float] = None
    user_response: str = ""
    timeout_seconds: int = 600  # 10 minute approval window

    def is_expired(self) -> bool:
        if self.status != ApprovalStatus.PENDING:
            return False
        return (time.time() - self.created_at) > self.timeout_seconds


class ApprovalManager:
    """Coordinates approval requests, parses user responses, and manages approval gates."""

    def __init__(self):
        self._pending_request: Optional[ApprovalRequest] = None
        self._history: List[ApprovalRequest] = []

    @property
    def pending_request(self) -> Optional[ApprovalRequest]:
        if self._pending_request and self._pending_request.is_expired():
            self._pending_request.status = ApprovalStatus.EXPIRED
            self._history.append(self._pending_request)
            self._pending_request = None
        return self._pending_request

    def create_request(
        self,
        task_id: str,
        description: str,
        approval_level: int,
        level_name: str,
        reasons: List[str],
        files_to_create: Optional[List[str]] = None,
        files_to_modify: Optional[List[str]] = None,
        files_to_delete: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        diff_preview: str = "",
    ) -> ApprovalRequest:
        req_id = f"APPR-{int(time.time())}"
        req = ApprovalRequest(
            request_id=req_id,
            task_id=task_id,
            description=description,
            approval_level=approval_level,
            level_name=level_name,
            reasons=reasons,
            files_to_create=files_to_create or [],
            files_to_modify=files_to_modify or [],
            files_to_delete=files_to_delete or [],
            dependencies=dependencies or [],
            diff_preview=diff_preview,
        )
        self._pending_request = req
        return req

    def process_response(self, response_text: str) -> Tuple[ApprovalStatus, str]:
        """
        Parse user response string against the pending approval request.
        """
        if not self._pending_request:
            return ApprovalStatus.REJECTED, "No pending approval request found."

        if self._pending_request.is_expired():
            self._pending_request.status = ApprovalStatus.EXPIRED
            self._history.append(self._pending_request)
            self._pending_request = None
            return ApprovalStatus.EXPIRED, "The pending approval request has expired."

        cleaned = response_text.strip().lower()

        # Approval matching
        if cleaned in {"approve", "approved", "yes", "y", "confirm", "proceed", "accept", "ok", "go ahead"}:
            self._pending_request.status = ApprovalStatus.APPROVED
            self._pending_request.responded_at = time.time()
            self._pending_request.user_response = response_text
            res_req = self._pending_request
            self._history.append(res_req)
            self._pending_request = None
            return ApprovalStatus.APPROVED, f"Approval granted for task {res_req.task_id} ({res_req.level_name})."

        # Rejection matching
        if cleaned in {"reject", "rejected", "no", "n", "deny", "denied", "disapprove"}:
            self._pending_request.status = ApprovalStatus.REJECTED
            self._pending_request.responded_at = time.time()
            self._pending_request.user_response = response_text
            res_req = self._pending_request
            self._history.append(res_req)
            self._pending_request = None
            return ApprovalStatus.REJECTED, f"Task {res_req.task_id} was rejected by the user."

        # Cancellation matching
        if cleaned in {"cancel", "cancelled", "stop", "abort", "halt"}:
            self._pending_request.status = ApprovalStatus.CANCELLED
            self._pending_request.responded_at = time.time()
            self._pending_request.user_response = response_text
            res_req = self._pending_request
            self._history.append(res_req)
            self._pending_request = None
            return ApprovalStatus.CANCELLED, f"Task {res_req.task_id} was cancelled."

        # Modify plan matching
        if "modify" in cleaned or "change plan" in cleaned or "update plan" in cleaned:
            self._pending_request.status = ApprovalStatus.MODIFY_REQUESTED
            self._pending_request.responded_at = time.time()
            self._pending_request.user_response = response_text
            res_req = self._pending_request
            self._history.append(res_req)
            self._pending_request = None
            return ApprovalStatus.MODIFY_REQUESTED, f"Plan modification requested for task {res_req.task_id}."

        return ApprovalStatus.PENDING, (
            f"Unrecognized response '{response_text}'. Please reply with APPROVE, REJECT, CANCEL, or MODIFY PLAN."
        )

    def cancel_pending(self, reason: str = "User requested cancellation") -> Optional[ApprovalRequest]:
        if self._pending_request:
            self._pending_request.status = ApprovalStatus.CANCELLED
            self._pending_request.user_response = reason
            self._pending_request.responded_at = time.time()
            req = self._pending_request
            self._history.append(req)
            self._pending_request = None
            return req
        return None
