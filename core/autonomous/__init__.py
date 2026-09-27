"""
JARVIS Autonomous Development Engine Package.
"""
from core.autonomous.engine import AutonomousEngine
from core.autonomous.policy import AutonomousConfig, PolicyEngine
from core.autonomous.approval import ApprovalManager, ApprovalRequest, ApprovalStatus
from core.autonomous.checkpoint import CheckpointManager, CheckpointRecord
from core.autonomous.rollback import RollbackManager
from core.autonomous.planner import AutonomousPlanner, AutonomousPlan, RequestCategory
from core.autonomous.task_manager import TaskManager, AutonomousTask, TaskStatus
from core.autonomous.test_runner import AutonomousTestRunner, TestResult
from core.autonomous.diagnostics import DiagnosticsEngine, DiagnosticReport

__all__ = [
    "AutonomousEngine",
    "AutonomousConfig",
    "PolicyEngine",
    "ApprovalManager",
    "ApprovalRequest",
    "ApprovalStatus",
    "CheckpointManager",
    "CheckpointRecord",
    "RollbackManager",
    "AutonomousPlanner",
    "AutonomousPlan",
    "RequestCategory",
    "TaskManager",
    "AutonomousTask",
    "TaskStatus",
    "AutonomousTestRunner",
    "TestResult",
    "DiagnosticsEngine",
    "DiagnosticReport",
]
