"""Self-Play protocol library. SP0 freezes workspace, schemas, and replay."""

from .paths import PROTOCOL_VERSION, Workspace, WorkspaceBoundaryError

__all__ = ["PROTOCOL_VERSION", "Workspace", "WorkspaceBoundaryError"]
