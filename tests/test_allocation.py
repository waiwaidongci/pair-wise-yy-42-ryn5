import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
from src.rules import STATES, TRANSITION_ROLES


class AllocationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item_a = self.service.create_item(
            {"title": "task A", "description": "first task zone", "severity": "high",
             "quantity": 5, "threshold": 10, "external_ref": "ALLOC-A"},
            "creator", "field_commander")
        self.item_b = self.service.create_item(
            {"title": "task B", "description": "second task zone", "severity": "low",
             "quantity": 1, "threshold": 10, "external_ref": "ALLOC-B"},
            "creator", "field_commander")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_resource_cannot_span_active_tasks(self):
        self.service.assign_resource(
            self.item_a["id"], {"resource": "巡线员-张三"}, "cmd", "logistics")
        with self.assertRaises(ConflictError):
            self.service.assign_resource(
                self.item_b["id"], {"resource": "巡线员-张三"}, "cmd", "logistics")
        other = self.service.assign_resource(
            self.item_b["id"], {"resource": "巡线员-李四"}, "cmd", "logistics")
        self.assertEqual(other["status"], "active")
        allocations = self.service.list_allocations(self.item_a["id"], "viewer")
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["resource"], "巡线员-张三")

    def test_release_frees_resource(self):
        allocation = self.service.assign_resource(
            self.item_a["id"], {"resource": "巡线员-张三"}, "cmd", "logistics")
        released = self.service.release_resource(
            self.item_a["id"], allocation["id"], "cmd", "logistics")
        self.assertEqual(released["status"], "released")
        self.assertIsNotNone(released["released_at"])
        again = self.service.assign_resource(
            self.item_b["id"], {"resource": "巡线员-张三"}, "cmd", "logistics")
        self.assertEqual(again["item_id"], self.item_b["id"])
        with self.assertRaises(ConflictError):
            self.service.release_resource(
                self.item_a["id"], allocation["id"], "cmd", "logistics")

    def test_closed_task_rejects_allocation(self):
        current = self.service.get_item(self.item_a["id"], "viewer")
        for target in STATES[1:]:
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer",
                TRANSITION_ROLES[target][0])
        with self.assertRaises(ConflictError):
            self.service.assign_resource(
                self.item_a["id"], {"resource": "巡线员-王五"}, "cmd", "logistics")

    def test_allocation_permission_and_validation(self):
        with self.assertRaises(PermissionDenied):
            self.service.assign_resource(
                self.item_a["id"], {"resource": "巡线员-张三"}, "cmd", "viewer")
        with self.assertRaises(ValidationError):
            self.service.assign_resource(
                self.item_a["id"], {"resource": "  "}, "cmd", "logistics")

    def test_allocation_audit_trail(self):
        allocation = self.service.assign_resource(
            self.item_a["id"], {"resource": "巡线员-张三"}, "cmd", "logistics")
        self.service.release_resource(
            self.item_a["id"], allocation["id"], "cmd", "logistics")
        actions = [e["action"] for e in self.service.audit("viewer", self.item_a["id"])]
        self.assertEqual(actions, ["create", "allocate", "release"])
        self.assertTrue(self.repo.verify_audit_chain())


if __name__ == "__main__":
    unittest.main()
