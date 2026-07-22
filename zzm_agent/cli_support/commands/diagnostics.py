"""Runtime inspection and diagnostic slash-command handlers."""

from zzm_agent.cli_support.commands.router import (
    _handle_artifacts,
    _handle_permissions,
    _handle_placeholder_registry,
    _handle_plan,
    _handle_review,
    _handle_status,
)

__all__ = [
    "_handle_artifacts",
    "_handle_permissions",
    "_handle_placeholder_registry",
    "_handle_plan",
    "_handle_review",
    "_handle_status",
]
