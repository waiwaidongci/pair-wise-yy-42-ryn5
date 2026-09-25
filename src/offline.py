from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .domain import (ConflictError, ValidationError, normalize_severity,
                     require_number, require_text)
from .rules import TERMINAL_STATES

APPLIED = "applied"
ARCHIVED = "archived"
REPLAYED = "replayed"
OUTCOMES = (APPLIED, ARCHIVED, REPLAYED)
MAX_BATCH_SIZE = 500


def parse_observed_at(value: Any) -> str:
    """观测时刻必须是ISO-8601时间，归一化到UTC秒级，便于按字典序比较先后。"""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("observed_at不能为空")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError("observed_at必须是ISO-8601时间") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def decide_outcome(last_observed_at: str | None, observed_at: str) -> str:
    """旧观测只归档不改现值：只有严格更新的观测才受理并刷新现值。"""
    if last_observed_at is None:
        return APPLIED
    return APPLIED if observed_at > last_observed_at else ARCHIVED


def ensure_mergeable(item_status: str) -> None:
    if item_status in TERMINAL_STATES:
        raise ConflictError("事件已关闭，无法补录")


def normalize_entry(entry: Any) -> Dict[str, Any]:
    """每条补录记录必须带编号、火线段、观测时刻和风险摘要。"""
    if not isinstance(entry, dict):
        raise ValidationError("补录记录必须是JSON对象")
    record_no = require_text(entry.get("record_no"), "record_no", 100)
    segment = require_text(entry.get("segment"), "segment", 200)
    observed_at = parse_observed_at(entry.get("observed_at"))
    risk_summary = require_text(entry.get("risk_summary"), "risk_summary")
    kind = require_text(entry.get("kind", "observation"), "kind", 100)
    status = entry.get("status", "open")
    if status not in ("open", "closed"):
        raise ValidationError("status必须是open或closed")
    severity = entry.get("severity")
    if severity is not None:
        severity = normalize_severity(severity)
    quantity = entry.get("quantity")
    if quantity is not None:
        quantity = require_number(quantity, "quantity")
    return {
        "record_no": record_no, "segment": segment, "observed_at": observed_at,
        "risk_summary": risk_summary, "kind": kind, "status": status,
        "severity": severity, "quantity": quantity,
    }


def normalize_batch(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = payload.get("records")
    if not isinstance(entries, list) or not entries:
        raise ValidationError("records必须是非空数组")
    if len(entries) > MAX_BATCH_SIZE:
        raise ValidationError(f"单次补录不能超过{MAX_BATCH_SIZE}条")
    return [normalize_entry(entry) for entry in entries]


def summarize_outcomes(results: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {"received": len(results), APPLIED: 0, ARCHIVED: 0, REPLAYED: 0}
    for result in results:
        summary[result["outcome"]] += 1
    return summary
