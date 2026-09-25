from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import ensure_role, normalize_severity, require_number, require_text
from .offline_merge import validate_batch
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES, TITLE,
                    VIEW_ROLES, completion_blockers, escalation_required,
                    priority_score, recalculate_response, role_for_transition,
                    validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "priority": priority_score(severity, quantity, threshold),
        })
        return self.enrich(item)

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def merge_offline_records(self, item_id: int, payload: Dict[str, Any],
                              actor: str, role: str) -> Dict[str, Any]:
        """离线补录合并：同编号重放返回首次受理结果，旧观测只归档不改现值。"""
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        observations = validate_batch(payload)
        results, item = self.repository.merge_offline_records(
            item_id, observations, actor)
        open_records = self.repository.open_record_count(item_id)
        recalc = recalculate_response(item["severity"], item["quantity"],
                                      item["threshold"], open_records)
        summary = {
            "applied": sum(1 for r in results if r["outcome"] == "applied"
                           and not r["replayed"]),
            "archived": sum(1 for r in results if r["outcome"] == "archived"
                            and not r["replayed"]),
            "replayed": sum(1 for r in results if r["replayed"]),
        }
        outcome_view = [{
            "ref": r["ref"], "record_id": r["record_id"], "outcome": r["outcome"],
            "replayed": r["replayed"], "segment": r.get("segment"),
            "observed_at": r.get("observed_at"),
        } for r in results]
        self.repository.append_audit("offline_merge", ENTITY, item_id, actor, {
            "results": outcome_view, "summary": summary,
            "last_observed_at": item.get("last_observed_at"),
            "priority": recalc["priority"],
            "deadline_hours": recalc["deadline_hours"],
        })
        return {"item": self.enrich(item), "results": outcome_view,
                "summary": summary}

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        if blockers:
            from .domain import ConflictError
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        })
        return self.enrich(updated)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        return [self.enrich(item) for item in self.repository.list_items(status)]

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        open_records = self.repository.open_record_count(item["id"])
        result.update(recalculate_response(
            item["severity"], item["quantity"], item["threshold"], open_records))
        result["open_records"] = open_records
        return result
