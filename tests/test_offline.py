import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES, priority_score, response_deadline_hours


def entry(record_no, observed_at, **extra):
    base = {"record_no": record_no, "segment": "北坡-3", "observed_at": observed_at,
            "risk_summary": "风险摘要" + record_no, "status": "closed"}
    base.update(extra)
    return base


class OfflineMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "merge item", "description": "offline merge scenarios",
             "severity": "moderate", "quantity": 4, "threshold": 8,
             "external_ref": "MERGE-1"}, "creator", "field_commander")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def merge(self, records, item_id=None):
        return self.service.merge_offline_records(
            item_id or self.item["id"], {"records": records}, "recorder", "field_commander")

    def test_latest_observation_wins_and_recomputes(self):
        result = self.merge([
            entry("OBS-2", "2026-09-25T10:00:00Z", quantity=12, severity="high",
                  risk_summary="火线延长至12公里"),
            entry("OBS-1", "2026-09-25T08:00:00Z", quantity=6, severity="low",
                  risk_summary="早先观测火线6公里"),
        ])
        outcomes = {r["record_no"]: r["outcome"] for r in result["results"]}
        self.assertEqual(outcomes, {"OBS-2": "applied", "OBS-1": "archived"})
        item = result["item"]
        self.assertEqual(item["quantity"], 12)
        self.assertEqual(item["severity"], "high")
        self.assertEqual(item["last_observed_at"], "2026-09-25T10:00:00+00:00")
        self.assertEqual(item["priority"], priority_score("high", 12, 8, 0))
        self.assertEqual(item["deadline_hours"], response_deadline_hours("high", 12, 8))
        records = self.service.list_records(self.item["id"], "viewer")
        self.assertEqual(len(records), 2)
        archived = [r for r in records if r["external_ref"] == "OBS-1"][0]
        self.assertEqual(archived["applied"], 0)
        self.assertEqual(archived["segment"], "北坡-3")
        self.assertEqual(archived["risk_summary"], "早先观测火线6公里")

    def test_late_old_observation_does_not_overwrite(self):
        self.merge([entry("OBS-1", "2026-09-25T10:00:00Z", quantity=12)])
        result = self.merge([entry("OBS-2", "2026-09-25T07:00:00Z", quantity=30,
                                   risk_summary="迟到的旧观测")])
        self.assertEqual(result["results"][0]["outcome"], "archived")
        self.assertEqual(result["item"]["quantity"], 12)
        self.assertEqual(result["item"]["last_observed_at"], "2026-09-25T10:00:00+00:00")

    def test_replay_returns_first_acceptance_without_double_count(self):
        first = self.merge([entry("OBS-9", "2026-09-25T09:00:00Z", status="open",
                                  risk_summary="未撤离牧民2人")])
        self.assertEqual(first["results"][0]["outcome"], "applied")
        second = self.merge([entry("OBS-9", "2026-09-25T09:00:00Z", status="open",
                                   risk_summary="未撤离牧民2人")])
        replay = second["results"][0]
        self.assertEqual(replay["outcome"], "replayed")
        self.assertEqual(replay["first_outcome"], "applied")
        self.assertEqual(replay["record"]["id"], first["results"][0]["record"]["id"])
        self.assertEqual(self.repo.open_record_count(self.item["id"]), 1)
        self.assertEqual(len(self.service.list_records(self.item["id"], "viewer")), 1)
        self.assertEqual(first["item"]["priority"], second["item"]["priority"])
        self.assertEqual(second["summary"],
                         {"received": 1, "applied": 0, "archived": 0, "replayed": 1})

    def test_open_merged_records_block_close(self):
        self.merge([entry("OBS-5", "2026-09-25T09:30:00+08:00", status="open",
                          risk_summary="东沟尚有未撤离人员")])
        current = self.service.get_item(self.item["id"], "viewer")
        for target in STATES[1:-1]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        with self.assertRaises(ConflictError):
            self.service.transition(current["id"], STATES[-1], current["version"],
                                    "reviewer", TRANSITION_ROLES[STATES[-1]][0])

    def test_merge_validation_and_closed_item(self):
        with self.assertRaises(ValidationError):
            self.merge([entry("OBS-X", "not-a-time")])
        with self.assertRaises(ValidationError):
            self.merge([{"record_no": "OBS-X", "observed_at": "2026-09-25T10:00:00Z",
                         "risk_summary": "缺火线段"}])
        with self.assertRaises(ValidationError):
            self.service.merge_offline_records(
                self.item["id"], {"records": []}, "recorder", "field_commander")
        with self.assertRaises(PermissionDenied):
            self.service.merge_offline_records(
                self.item["id"], {"records": [entry("OBS-P", "2026-09-25T10:00:00Z")]},
                "recorder", "viewer")
        current = self.service.get_item(self.item["id"], "viewer")
        for target in STATES[1:]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        with self.assertRaises(ConflictError):
            self.merge([entry("OBS-Z", "2026-09-25T11:00:00Z")])

    def test_merge_audit_trail(self):
        self.merge([entry("OBS-1", "2026-09-25T10:00:00Z", status="open")])
        self.merge([entry("OBS-1", "2026-09-25T10:00:00Z", status="open")])
        events = [e for e in self.service.audit("viewer", self.item["id"])
                  if e["action"] == "offline_merge"]
        self.assertEqual([e["detail"]["outcome"] for e in events], ["applied", "replayed"])
        self.assertEqual(events[0]["detail"]["record_no"], "OBS-1")
        self.assertEqual(events[0]["detail"]["segment"], "北坡-3")
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
