"""Tool-layer wrappers for CodeAgent services."""

from codeagent.tools.hitl import (
    ApprovalDecision,
    ApprovalRequest,
    ToolCall,
    ToolHITLInterceptor,
    ToolInterceptionResult,
)
from codeagent.tools.permissions import (
    PermissionDecision,
    ToolCallContext,
    ToolPermissionPolicy,
)
from codeagent.tools.registry import ToolRegistry, ToolSpec, create_default_tool_registry
from codeagent.tools.risk_checker import (
    RepairRiskChecker,
    RepairRiskFinding,
    RepairRiskReport,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "PermissionDecision",
    "ToolCall",
    "ToolCallContext",
    "ToolHITLInterceptor",
    "ToolInterceptionResult",
    "ToolPermissionPolicy",
    "ToolRegistry",
    "ToolSpec",
    "RepairRiskChecker",
    "RepairRiskFinding",
    "RepairRiskReport",
    "create_default_tool_registry",
]
