"""Offline reliability tests for persistent Mission Control."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import mission_control


class MissionControlReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "missions.json"
        self.path_patch = patch.object(mission_control, "_DATA_PATH", self.store)
        self.path_patch.start()
        mission_control._LOCK.acquire()
        mission_control._LOCK.release()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def plan(self, steps="Open settings\nInspect privacy\nVerify the result"):
        result = mission_control.run({
            "action": "plan",
            "title": "Test mission",
            "goal": "Reach and verify a setting",
            "steps": steps,
        })
        data = mission_control._read()
        self.assertEqual(len(data["missions"]), 1, result)
        return data["missions"][0]["id"]

    def start(self, mission_id):
        return mission_control.run({"action": "start", "mission_id": mission_id})

    def begin(self, mission_id, step_id="S01"):
        return mission_control.run({
            "action": "begin_step", "mission_id": mission_id, "step_id": step_id,
        })

    def complete(self, mission_id, step_id="S01", verification="Verified by tool result"):
        return mission_control.run({
            "action": "complete_step", "mission_id": mission_id,
            "step_id": step_id, "verification": verification,
        })

    def test_plan_is_persisted_without_starting_execution(self):
        mission_id = self.plan()
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["id"], mission_id)
        self.assertEqual(mission["status"], "active")
        self.assertEqual(mission["execution_mode"], "plan")
        self.assertEqual([s["status"] for s in mission["steps"]], ["pending"] * 3)
        self.assertEqual(mission["steps"][0]["status"], "pending")

    def test_start_hands_off_only_the_first_step(self):
        mission_id = self.plan()
        result = self.start(mission_id)
        self.assertIn("[MISSION_CONTINUE]", result)
        self.assertIn("STEP_ID=S01", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["execution_mode"], "autonomous")
        self.assertEqual([s["status"] for s in mission["steps"]], ["pending"] * 3)

    def test_step_must_be_started_before_completion(self):
        mission_id = self.plan()
        self.start(mission_id)
        result = self.complete(mission_id)
        self.assertIn("Call begin_step", result)
        self.assertEqual(mission_control._read()["missions"][0]["steps"][0]["status"], "pending")

    def test_completion_requires_verification_evidence(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.begin(mission_id)
        result = self.complete(mission_id, verification="")
        self.assertIn("verified result", result)
        self.assertEqual(mission_control._read()["missions"][0]["steps"][0]["status"], "running")

    def test_verified_completion_advances_to_next_step(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.begin(mission_id)
        result = self.complete(mission_id)
        self.assertIn("[MISSION_CONTINUE]", result)
        self.assertIn("STEP_ID=S02", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "done")
        self.assertEqual(mission["steps"][0]["verification"], "Verified by tool result")
        self.assertEqual(mission["steps"][1]["status"], "pending")

    def test_interrupted_running_step_is_never_automatically_repeated(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.begin(mission_id)
        recovery = self.start(mission_id)
        self.assertIn("[MISSION_RECOVERY]", recovery)
        self.assertIn("Do NOT repeat side effects", recovery)
        self.assertNotIn("[MISSION_CONTINUE]", recovery)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "running")

    def test_begin_step_is_idempotently_refused_for_running_step(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.begin(mission_id)
        result = self.begin(mission_id)
        self.assertIn("already started", result)
        self.assertEqual(mission_control._read()["missions"][0]["steps"][0]["status"], "running")

    def test_out_of_order_step_cannot_start_or_complete(self):
        mission_id = self.plan()
        self.start(mission_id)
        begin_result = self.begin(mission_id, "S02")
        self.assertIn("Only the next pending step", begin_result)
        complete_result = self.complete(mission_id, "S02")
        self.assertIn("Out-of-order completion refused", complete_result)
        self.assertIn("next pending step is S01", complete_result)
        self.assertEqual(
            [s["status"] for s in mission_control._read()["missions"][0]["steps"]],
            ["pending", "pending", "pending"],
        )

    def test_blocked_step_pauses_mission_and_resume_reopens_it(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.begin(mission_id)
        blocked = mission_control.run({
            "action": "block_step", "mission_id": mission_id,
            "step_id": "S01", "reason": "The target control is unavailable",
        })
        self.assertIn("[MISSION_BLOCKED]", blocked)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["status"], "paused")
        self.assertEqual(mission["steps"][0]["status"], "blocked")
        resumed = mission_control.run({"action": "resume", "mission_id": mission_id})
        self.assertIn("[MISSION_CONTINUE]", resumed)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["status"], "active")
        self.assertEqual(mission["steps"][0]["status"], "pending")

    def test_mission_cannot_be_completed_with_open_steps(self):
        mission_id = self.plan()
        result = mission_control.run({"action": "complete", "mission_id": mission_id})
        self.assertIn("unfinished steps", result)
        self.assertNotEqual(mission_control._read()["missions"][0]["status"], "completed")

    def test_all_steps_verified_marks_mission_complete(self):
        mission_id = self.plan("First\nSecond")
        self.start(mission_id)
        for step_id in ("S01", "S02"):
            self.begin(mission_id, step_id)
            result = self.complete(mission_id, step_id, f"{step_id} verified")
        self.assertIn("[MISSION_COMPLETE]", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["status"], "completed")
        self.assertTrue(all(step["status"] == "done" for step in mission["steps"]))

    def test_corrupt_store_is_not_silently_overwritten(self):
        self.store.write_text("{ definitely not valid json", encoding="utf-8")
        result = mission_control.run({
            "action": "plan", "title": "Must not overwrite",
            "goal": "Preserve existing data", "steps": "One",
        })
        self.assertIn("Mission Control failed", result)
        self.assertEqual(self.store.read_text(encoding="utf-8"), "{ definitely not valid json")

    def test_atomic_store_is_valid_json_after_write(self):
        self.plan()
        parsed = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(parsed["version"], 1)
        self.assertEqual(len(parsed["missions"]), 1)
        self.assertFalse(list(self.store.parent.glob(".missions-*.tmp")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
