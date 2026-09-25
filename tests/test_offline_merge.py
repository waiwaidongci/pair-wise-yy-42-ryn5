import tempfile
import unittest
from pathlib import Path

from src import rules
from src.domain import ConflictError, NotFoundError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


def batch(*records):
    return {"records": list(records)}


def obs(ref, observed_at, segment="北线-3", summary="火线延长", **extra):
    record = {"ref": ref, "segment": segment, "observed_at": observed_at,
              "risk_summary": summary}
    record.update(extra)
    return record


class OfflineMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "北坡火线", "description": "offline merge scenarios",
             "severity": "moderate", "quantity": 5, "threshold": 10,
             "external_ref": "WF-OFF-1"}, "creator", "field_commander")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def merge(self, payload, actor="patrol-1", role="field_commander"):
        return self.service.merge_offline_records(self.item["id"], payload,
                                                  actor, role)

    def test_merge_applies_latest_and_recalculates(self):
        result = self.merge(batch(
            obs("OBS-1", "2026-09-25T08:00:00Z", severity="high", quantity=12),
            obs("OBS-2", "2026-09-25T09:30:00+00:00", segment="北线-4",
                summary="风向转西北", severity="extreme", quantity=20),
        ))
        self.assertEqual(result["summary"],
                         {"applied": 2, "archived": 0, "replayed": 0})
        item = result["item"]
        self.assertEqual(item["severity"], "extreme")
        self.assertEqual(item["quantity"], 20)
        self.assertEqual(item["last_observed_at"], "2026-09-25T09:30:00+00:00")
        self.assertEqual(item["priority"],
                         rules.priority_score("extreme", 20, 10, 2))
        self.assertEqual(item["deadline_hours"],
                         rules.response_deadline_hours("extreme", 20, 10))
        self.assertEqual(item["open_records"], 2)
        records = self.service.list_records(self.item["id"], "viewer")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["segment"], "北线-3")
        self.assertEqual(records[0]["observed_at"], "2026-09-25T08:00:00+00:00")
        self.assertEqual(records[0]["risk_summary"], "火线延长")
        self.assertEqual(records[0]["merge_outcome"], "applied")
        events = self.service.audit("viewer", self.item["id"])
        self.assertEqual(events[-1]["action"], "offline_merge")
        self.assertEqual(events[-1]["detail"]["summary"]["applied"], 2)
        self.assertTrue(self.repo.verify_audit_chain())

    def test_out_of_order_observation_archived_only(self):
        first = self.merge(batch(
            obs("OBS-NEW", "2026-09-25T10:00:00Z", severity="high", quantity=12)))
        self.assertEqual(first["results"][0]["outcome"], "applied")
        late = self.merge(batch(
            obs("OBS-OLD", "2026-09-25T07:00:00Z", severity="low", quantity=1,
                summary="旧观测补录")))
        self.assertEqual(late["summary"]["archived"], 1)
        self.assertEqual(late["results"][0]["outcome"], "archived")
        item = late["item"]
        self.assertEqual(item["severity"], "high")
        self.assertEqual(item["quantity"], 12)
        self.assertEqual(item["last_observed_at"], "2026-09-25T10:00:00+00:00")
        records = self.service.list_records(self.item["id"], "viewer")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["merge_outcome"], "archived")

    def test_replay_returns_first_acceptance_result(self):
        first = self.merge(batch(
            obs("OBS-1", "2026-09-25T08:00:00Z", severity="high", quantity=12)))
        record_id = first["results"][0]["record_id"]
        replay = self.merge(batch(
            obs("OBS-1", "2026-09-25T08:00:00Z", severity="extreme", quantity=99,
                summary="重放内容不同")))
        entry = replay["results"][0]
        self.assertTrue(entry["replayed"])
        self.assertEqual(entry["outcome"], "applied")
        self.assertEqual(entry["record_id"], record_id)
        self.assertEqual(replay["summary"]["replayed"], 1)
        self.assertEqual(replay["item"]["severity"], "high")
        self.assertEqual(replay["item"]["quantity"], 12)
        self.assertEqual(len(self.service.list_records(self.item["id"], "viewer")), 1)
        archived = self.merge(batch(
            obs("OBS-2", "2026-09-25T06:00:00Z")))
        self.assertEqual(archived["results"][0]["outcome"], "archived")
        archived_replay = self.merge(batch(
            obs("OBS-2", "2026-09-25T06:00:00Z")))
        self.assertEqual(archived_replay["results"][0]["outcome"], "archived")
        self.assertTrue(archived_replay["results"][0]["replayed"])

    def test_duplicate_ref_within_same_batch_replays_first(self):
        result = self.merge(batch(
            obs("OBS-1", "2026-09-25T08:00:00Z", severity="high"),
            obs("OBS-1", "2026-09-25T09:00:00Z", severity="extreme"),
        ))
        self.assertEqual(result["summary"],
                         {"applied": 1, "archived": 0, "replayed": 1})
        first, second = result["results"]
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(result["item"]["severity"], "high")
        self.assertEqual(len(self.service.list_records(self.item["id"], "viewer")), 1)

    def test_open_merged_records_block_closure(self):
        self.merge(batch(obs("OBS-1", "2026-09-25T08:00:00Z",
                             summary="未撤离：三户牧民")))
        current = self.service.get_item(self.item["id"], "viewer")
        for target in STATES[1:-1]:
            current = self.service.transition(
                current["id"], target, current["version"], "commander",
                TRANSITION_ROLES[target][0])
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[-1],
                                    current["version"], "commander",
                                    TRANSITION_ROLES[STATES[-1]][0])
        self.merge(batch(obs("OBS-1", "2026-09-25T08:00:00Z"),
                         obs("OBS-2", "2026-09-25T09:00:00Z",
                             summary="撤离完成", status="closed")))
        self.assertEqual(self.repo.open_record_count(self.item["id"]), 1)
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[-1],
                                    current["version"], "commander",
                                    TRANSITION_ROLES[STATES[-1]][0])

    def test_validation_and_permission_failures(self):
        with self.assertRaises(ValidationError):
            self.merge({"records": []})
        with self.assertRaises(ValidationError):
            self.merge({"records": "not-a-list"})
        with self.assertRaises(ValidationError):
            self.merge(batch({"ref": "OBS-1", "observed_at": "2026-09-25T08:00:00Z",
                              "risk_summary": "缺火线段"}))
        with self.assertRaises(ValidationError):
            self.merge(batch(obs("OBS-1", "not-a-time")))
        with self.assertRaises(ValidationError):
            self.merge(batch(obs("OBS-1", "2026-09-25T08:00:00Z",
                                 severity="not-a-severity")))
        with self.assertRaises(PermissionDenied):
            self.merge(batch(obs("OBS-1", "2026-09-25T08:00:00Z")),
                       role="viewer")
        with self.assertRaises(NotFoundError):
            self.service.merge_offline_records(
                9999, batch(obs("OBS-1", "2026-09-25T08:00:00Z")),
                "patrol-1", "field_commander")


if __name__ == "__main__":
    unittest.main()
