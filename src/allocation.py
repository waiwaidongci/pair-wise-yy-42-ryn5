from __future__ import annotations

from typing import Any

from .domain import ConflictError, require_text
from .rules import TERMINAL_STATES

ALLOCATE_ROLES = set(['field_commander', 'logistics'])
RELEASE_ROLES = set(['field_commander', 'logistics', 'incident_commander'])
ALLOCATION_STATES = ('active', 'released')


def normalize_resource(value: Any) -> str:
    return require_text(value, "resource", 100)


def ensure_allocatable(item_status: str) -> None:
    """已关闭的任务区不能再分配资源。"""
    if item_status in TERMINAL_STATES:
        raise ConflictError("任务已关闭，不能分配资源")
