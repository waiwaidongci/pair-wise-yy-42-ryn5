from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .domain import ValidationError, normalize_severity, require_number, require_text

MAX_BATCH_SIZE = 500
OUTCOME_APPLIED = "applied"
OUTCOME_ARCHIVED = "archived"
OUTCOME_RECORDED = "recorded"


def parse_observed_at(value: Any, field: str = "observed_at") -> str:
    """把观测时刻归一化为UTC ISO字符串，保证字符串比较即时间比较。"""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field}不能为空")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{field}必须是ISO-8601格式时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def validate_observation(payload: Any, index: int = 0) -> Dict[str, Any]:
    """校验并归一化一条离线补录观测：编号、火线段、观测时刻、风险摘要。"""
    if not isinstance(payload, dict):
        raise ValidationError(f"records[{index}]必须是对象")
    ref = payload.get("ref")
    if ref is None:
        ref = payload.get("external_ref")
    ref = require_text(ref, f"records[{index}].ref", 100)
    segment = require_text(payload.get("segment"), f"records[{index}].segment", 200)
    observed_at = parse_observed_at(payload.get("observed_at"),
                                    f"records[{index}].observed_at")
    risk_summary = payload.get("risk_summary")
    if risk_summary is None:
        risk_summary = payload.get("detail")
    risk_summary = require_text(risk_summary, f"records[{index}].risk_summary")
    kind = payload.get("kind")
    kind = require_text(kind, f"records[{index}].kind", 100) if kind is not None \
        else "field_observation"
    status = payload.get("status", "open")
    if status not in ("open", "closed"):
        raise ValidationError(f"records[{index}].status必须是open或closed")
    severity = payload.get("severity")
    severity = normalize_severity(severity) if severity is not None else None
    quantity = payload.get("quantity")
    quantity = require_number(quantity, f"records[{index}].quantity") \
        if quantity is not None else None
    return {
        "ref": ref, "segment": segment, "observed_at": observed_at,
        "risk_summary": risk_summary, "kind": kind, "status": status,
        "severity": severity, "quantity": quantity,
    }


def validate_batch(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是JSON对象")
    raw = payload.get("records")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("records必须是非空数组")
    if len(raw) > MAX_BATCH_SIZE:
        raise ValidationError(f"records一次不能超过{MAX_BATCH_SIZE}条")
    return [validate_observation(entry, index) for index, entry in enumerate(raw)]


def plan_merge(item: Dict[str, Any], observations: List[Dict[str, Any]],
               existing_records: List[Dict[str, Any]]
               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """纯函数：决定每条观测的受理结果和合并后的现值。

    - 同编号重放：返回第一次受理结果（沿用已存记录的outcome），不重复计入。
    - 观测时刻不晚于当前最新观测的旧观测：只归档，不推进现值。
    - 其余观测：受理为最新现值，推进severity/quantity/last_observed_at。
    """
    existing_by_ref = {row["external_ref"]: row for row in existing_records}
    state = {
        "severity": item["severity"],
        "quantity": item["quantity"],
        "last_observed_at": item.get("last_observed_at"),
    }
    results: List[Dict[str, Any]] = []
    planned_by_ref: Dict[str, Dict[str, Any]] = {}
    for obs in observations:
        ref = obs["ref"]
        stored = existing_by_ref.get(ref)
        if stored is not None:
            results.append({
                "ref": ref, "record_id": stored["id"],
                "outcome": stored.get("merge_outcome") or OUTCOME_RECORDED,
                "replayed": True, "segment": stored.get("segment"),
                "observed_at": stored.get("observed_at"),
            })
            continue
        first = planned_by_ref.get(ref)
        if first is not None:
            results.append({
                "ref": ref, "record_id": None,
                "outcome": first["outcome"], "replayed": True,
                "segment": first["record"]["segment"],
                "observed_at": first["record"]["observed_at"],
            })
            continue
        last = state["last_observed_at"]
        if last is None or obs["observed_at"] >= last:
            outcome = OUTCOME_APPLIED
            state["last_observed_at"] = obs["observed_at"]
            if obs["severity"] is not None:
                state["severity"] = obs["severity"]
            if obs["quantity"] is not None:
                state["quantity"] = obs["quantity"]
        else:
            outcome = OUTCOME_ARCHIVED
        entry = {
            "ref": ref, "record_id": None, "outcome": outcome,
            "replayed": False, "record": obs,
            "segment": obs["segment"], "observed_at": obs["observed_at"],
        }
        planned_by_ref[ref] = entry
        results.append(entry)
    return results, state
