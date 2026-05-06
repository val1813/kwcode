"""
Pydantic models for kwcode server API.
"""

from typing import Optional
from pydantic import BaseModel, Field

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("kwcode")
except Exception:
    _VERSION = "1.5.1"


class TaskRequest(BaseModel):
    """Request to submit a task."""
    input: str = Field(..., description="Task description")
    project_root: str = Field(".", description="Project root directory")
    no_search: bool = Field(False, description="Disable search augmentation")
    image_paths: list[str] = Field(default_factory=list, description="Image paths for vision tasks")


class TaskResponse(BaseModel):
    """Response after submitting a task."""
    task_id: str
    status: str = "accepted"


class TaskResult(BaseModel):
    """Final result of a completed task."""
    task_id: str
    success: bool
    error: Optional[str] = None
    elapsed: float = 0.0
    files_modified: list[str] = Field(default_factory=list)
    summary: str = ""


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = _VERSION
    model: str = ""
    project_root: str = ""


class StatusResponse(BaseModel):
    """Server status response."""
    model: str = ""
    project_root: str = ""
    experts_loaded: int = 0
    search_enabled: bool = False
    uptime_seconds: float = 0.0


class FileTreeItem(BaseModel):
    """A file or directory in the file tree."""
    name: str
    path: str
    is_dir: bool = False
    children: list["FileTreeItem"] = Field(default_factory=list)


class FileContent(BaseModel):
    """File content response."""
    path: str
    content: str
    language: str = ""
    lines: int = 0


class ManifestResponse(BaseModel):
    """UpstreamManifest state response."""
    signatures: dict[str, dict[str, str]] = Field(default_factory=dict)
    constants: dict[str, dict[str, str]] = Field(default_factory=dict)
    file_count: int = 0
