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
        from core.autonomy_policy import new_turn_authorization
        self.scope = new_turn_authorization("Run the test mission")

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
        return mission_control.run({
            "action": "start", "mission_id": mission_id,
        }, session_memory=self.scope)

    def begin(self, mission_id, step_id="S01"):
        return mission_control.run({
            "action": "begin_step", "mission_id": mission_id, "step_id": step_id,
        }, session_memory=self.scope)

    def record_verified_tool(self, tool_name="verified_test_action"):
        result = f"status=done action={tool_name} verified=true"
        self.assertTrue(mission_control.record_tool_result(
            self.scope, tool_name, result,
        ))
        return result

    def complete(self, mission_id, step_id="S01", verification="Model-supplied text is not evidence"):
        return mission_control.run({
            "action": "complete_step", "mission_id": mission_id,
            "step_id": step_id, "verification": verification,
        }, session_memory=self.scope)

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
        self.assertIn("do NOT call begin_step", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["execution_mode"], "autonomous")
        self.assertEqual([s["status"] for s in mission["steps"]],
                         ["running", "pending", "pending"])
        self.assertEqual(self.scope["_mission_execution"]["step_id"], "S01")

    def test_step_must_be_claimed_before_completion(self):
        mission_id = self.plan()
        result = self.complete(mission_id)
        self.assertIn("currently running step", result)
        self.assertEqual(mission_control._read()["missions"][0]["steps"][0]["status"], "pending")

    def test_completion_requires_dispatcher_evidence_not_model_text(self):
        mission_id = self.plan()
        self.start(mission_id)
        result = self.complete(mission_id, verification="I personally verified this.")
        self.assertIn("No matching verified tool result", result)
        self.assertEqual(mission_control._read()["missions"][0]["steps"][0]["status"], "running")

    def test_verified_completion_auto_claims_next_step(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.record_verified_tool()
        result = self.complete(mission_id)
        self.assertIn("[MISSION_CONTINUE]", result)
        self.assertIn("STEP_ID=S02", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "done")
        self.assertIn("Application verified verified_test_action", mission["steps"][0]["verification"])
        self.assertEqual(mission["steps"][1]["status"], "running")
        self.assertEqual(self.scope["_mission_execution"]["step_id"], "S02")

    def test_interrupted_running_step_is_never_automatically_repeated(self):
        mission_id = self.plan()
        self.start(mission_id)
        self.scope = {"user_request": "Continue the mission"}
        recovery = self.start(mission_id)
        self.assertIn("[MISSION_RECOVERY_REQUIRED]", recovery)
        self.assertIn("Do NOT repeat", recovery)
        self.assertNotIn("[MISSION_CONTINUE]", recovery)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "blocked")

    def test_begin_step_is_idempotently_refused_for_running_step(self):
        mission_id = self.plan()
        self.start(mission_id)
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
        self.assertEqual(
            [s["status"] for s in mission_control._read()["missions"][0]["steps"]],
            ["running", "pending", "pending"],
        )

    def test_blocked_step_requires_actual_read_only_inspection_before_resolution(self):
        mission_id = self.plan("Do one task")
        self.start(mission_id)
        blocked = mission_control.run({
            "action": "block_step", "mission_id": mission_id,
            "step_id": "S01", "reason": "Execution outcome is uncertain",
        }, session_memory=self.scope)
        self.assertIn("[MISSION_BLOCKED]", blocked)
        resumed = mission_control.run({"action": "resume", "mission_id": mission_id},
                                      session_memory=self.scope)
        self.assertIn("[MISSION_RECOVERY_REQUIRED]", resumed)
        refused = mission_control.run({
            "action": "resolve_step", "mission_id": mission_id, "step_id": "S01",
            "resolution": "retry_safe", "verification": "I checked that nothing happened.",
        }, session_memory=self.scope)
        self.assertIn("actual read-only inspection", refused)
        mission_control.record_tool_result(
            self.scope, "inspect_active_window", "- Button: Save", {},
        )
        recovered = mission_control.run({
            "action": "resolve_step", "mission_id": mission_id, "step_id": "S01",
            "resolution": "retry_safe", "verification": "The expected state is not visible.",
        }, session_memory=self.scope)
        self.assertIn("[MISSION_CONTINUE]", recovered)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["status"], "active")
        self.assertEqual(mission["steps"][0]["status"], "running")
        self.assertEqual(mission["steps"][0]["attempts"], 2)

    def test_recovery_can_complete_only_after_a_read_only_inspection(self):
        mission_id = self.plan("Confirm an existing state")
        self.start(mission_id)
        mission_control.run({
            "action": "block_step", "mission_id": mission_id,
            "step_id": "S01", "reason": "The previous result was lost",
        }, session_memory=self.scope)
        mission_control.run({"action": "resume", "mission_id": mission_id},
                            session_memory=self.scope)
        mission_control.record_tool_result(
            self.scope, "file_controller", "report.txt contains requested line",
            {"action": "read", "path": "downloads", "name": "report.txt"},
        )
        resolved = mission_control.run({
            "action": "resolve_step", "mission_id": mission_id, "step_id": "S01",
            "resolution": "completed", "verification": "The file contains the requested line.",
        }, session_memory=self.scope)
        self.assertIn("[MISSION_COMPLETE]", resolved)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "done")
        self.assertIn("file_controller/read", mission["steps"][0]["verification"])

    def test_mission_cannot_be_completed_with_open_steps(self):
        mission_id = self.plan()
        result = mission_control.run({"action": "complete", "mission_id": mission_id})
        self.assertIn("unfinished steps", result)
        self.assertNotEqual(mission_control._read()["missions"][0]["status"], "completed")

    def test_all_steps_verified_marks_mission_complete(self):
        mission_id = self.plan("First\nSecond")
        self.start(mission_id)
        for _step_id in ("S01", "S02"):
            self.record_verified_tool()
            result = self.complete(mission_id, _step_id, "ignored model summary")
        self.assertIn("[MISSION_COMPLETE]", result)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["status"], "completed")
        self.assertTrue(all(step["status"] == "done" for step in mission["steps"]))
        self.assertNotIn("_mission_execution", self.scope)

    def test_verified_receipt_survives_new_turn_without_repeating_action(self):
        mission_id = self.plan("Do a step once")
        self.start(mission_id)
        self.record_verified_tool()
        saved = mission_control._read()["missions"][0]["steps"][0]["tool_evidence"]
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["tool_name"], "verified_test_action")

        # A later user turn gets a fresh scope; the mission reattaches only
        # the app-persisted receipt belonging to its currently running step.
        self.scope = {"user_request": "Continue the mission"}
        recovery = self.start(mission_id)
        self.assertIn("[MISSION_RECOVERY]", recovery)
        self.assertIn("persisted verified receipt", recovery)
        self.assertIn("_mission_execution", self.scope)
        completed = self.complete(mission_id)
        self.assertIn("[MISSION_COMPLETE]", completed)
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "done")
        self.assertEqual(mission["status"], "completed")

    def test_recovery_rejects_and_clears_legacy_receipt_without_verified_true(self):
        mission_id = self.plan("Do a step once")
        self.start(mission_id)
        data = mission_control._read()
        step = data["missions"][0]["steps"][0]
        step["tool_evidence"] = {
            "status": "done",
            "tool_name": "verified_test_action",
            "action": "verified_test_action",
            "result_sha256": "0" * 64,
        }
        mission_control._write(data)

        self.scope = {"user_request": "Continue the mission"}
        recovery = self.start(mission_id)
        self.assertIn("[MISSION_RECOVERY_REQUIRED]", recovery)
        self.assertIn("Do NOT repeat", recovery)
        self.assertNotIn("persisted receipt", recovery)
        self.assertNotIn(
            "tool_evidence",
            mission_control._read()["missions"][0]["steps"][0],
        )
        self.assertIsNone(
            self.scope["_mission_execution"]["last_result"],
        )

    def test_failed_dispatcher_result_removes_previously_persisted_receipt(self):
        mission_id = self.plan("Do a step once")
        self.start(mission_id)
        self.record_verified_tool()
        self.assertIn("tool_evidence", mission_control._read()["missions"][0]["steps"][0])
        cleared = mission_control.record_tool_result(
            self.scope, "verified_test_action",
            "status=unverified action=verified_test_action reason=timeout",
        )
        self.assertFalse(cleared)
        self.assertNotIn("tool_evidence",
                         mission_control._read()["missions"][0]["steps"][0])

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


    def _running_mission(self, steps="Open Privacy tab"):
        mission_id = self.plan(steps)
        self.start(mission_id)
        return mission_id

    def test_ui_plan_accepts_the_step_claimed_by_start(self):
        mission_id = self.plan("Open Privacy tab")
        self.start(mission_id)
        with patch("actions.ui_interaction._snapshot", return_value=[
            {"role": "TabItem", "name": "Privacy", "visible": True, "enabled": True},
        ]):
            result = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            }, session_memory=self.scope)
        self.assertIn("[MISSION_UI_PLAN]", result)

    def test_ui_plan_uses_fresh_snapshot_and_persists_one_action_without_executing(self):
        mission_id = self._running_mission()
        snapshot = [{
            "role": "TabItem", "name": "Privacy",
            "visible": True, "enabled": True,
        }]
        with patch("actions.ui_interaction._snapshot", return_value=snapshot) as inspect_ui:
            result = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
        inspect_ui.assert_called_once_with()
        payload = json.loads(result.split("[MISSION_UI_PLAN] ", 1)[1])
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["execution"], "not_started")
        self.assertEqual(len(payload["steps"]), 1)
        self.assertEqual(payload["steps"][0]["tool"], "select_tab_item")
        self.assertEqual(payload["steps"][0]["parameters"], {"tab_label": "Privacy"})
        mission = mission_control._read()["missions"][0]
        self.assertEqual(mission["steps"][0]["status"], "running")
        self.assertEqual(mission["steps"][0]["ui_action"]["tool"], "select_tab_item")
        self.assertTrue(mission["steps"][0]["ui_action_planned_at"])

    def test_ui_plan_clarification_does_not_persist_or_execute_an_action(self):
        mission_id = self._running_mission("Open Privacy")
        snapshot = [
            {"role": "TabItem", "name": "Privacy", "visible": True, "enabled": True,
             "parent_name": "Account"},
            {"role": "TabItem", "name": "Privacy", "visible": True, "enabled": True,
             "parent_name": "Network"},
        ]
        with patch("actions.ui_interaction._snapshot", return_value=snapshot):
            result = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
        payload = json.loads(result.split("[MISSION_UI_PLAN] ", 1)[1])
        self.assertEqual(payload["status"], "needs_clarification")
        self.assertEqual(payload["steps"], [])
        self.assertNotIn("ui_action", mission_control._read()["missions"][0]["steps"][0])

    def test_persisted_ui_plan_is_never_reissued_after_interruption(self):
        mission_id = self._running_mission()
        snapshot = [{
            "role": "TabItem", "name": "Privacy",
            "visible": True, "enabled": True,
        }]
        with patch("actions.ui_interaction._snapshot", return_value=snapshot) as inspect_ui:
            first = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
            second = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
        self.assertIn("[MISSION_UI_PLAN]", first)
        self.assertIn("[MISSION_UI_RECOVERY]", second)
        self.assertEqual(inspect_ui.call_count, 1)
        self.assertNotIn('"tool"', second)

    def test_ui_plan_rejects_unknown_tool_before_persisting_it(self):
        mission_id = self._running_mission()
        fake_plan = {
            "status": "planned",
            "steps": [{
                "tool": "delete_everything", "parameters": {},
                "risk": "low", "requires_confirmation": False,
            }],
        }
        with patch("actions.ui_interaction._snapshot", return_value=[]), \
             patch("core.ui_reasoning.plan_ui_task", return_value=fake_plan):
            result = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
        payload = json.loads(result.split("[MISSION_UI_PLAN] ", 1)[1])
        self.assertEqual(payload["status"], "blocked")
        self.assertNotIn("ui_action", mission_control._read()["missions"][0]["steps"][0])



    def test_long_ui_plan_keeps_its_machine_readable_handoff(self):
        mission_id = self._running_mission("Click Save and verify a new confirmation label")
        fake_plan = {
            "status": "planned",
            "steps": [{
                "tool": "click_and_verify",
                "parameters": {
                    "button_label": "S" * 160,
                    "expected_result": "C" * 160,
                },
                "risk": "low",
                "requires_confirmation": False,
                "reason": "R" * 240,
                "expected_result": "E" * 320,
            }],
        }
        with patch("actions.ui_interaction._snapshot", return_value=[]), \
             patch("core.ui_reasoning.plan_ui_task", return_value=fake_plan):
            result = mission_control.run({
                "action": "plan_ui_step", "mission_id": mission_id, "step_id": "S01",
            })
        self.assertIn("[MISSION_UI_PLAN]", result)
        payload = json.loads(result.split("[MISSION_UI_PLAN] ", 1)[1])
        self.assertEqual(payload["steps"][0]["tool"], "click_and_verify")
        self.assertEqual(payload["steps"][0]["expected_result"], "E" * 320)



if __name__ == "__main__":
    unittest.main(verbosity=2)
