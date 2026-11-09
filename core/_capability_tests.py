"""Offline capability-pack tests. No API calls, audio devices or paid services."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import undo


class ConversationHistoryTests(unittest.TestCase):
    def test_history_is_opt_in_and_searchable(self):
        from memory import conversation_history as history
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            with patch.object(history, "_HISTORY_PATH", path), \
                 patch.object(history, "get_plugin_config", return_value={"enabled": False}):
                self.assertFalse(history.append_message("user", "private prompt"))
                self.assertFalse(path.exists())

            with patch.object(history, "_HISTORY_PATH", path), \
                 patch.object(history, "get_plugin_config",
                              return_value={"enabled": True, "retention_days": "30"}):
                self.assertTrue(history.append_message("user", "We planned a JARVIS mission."))
                self.assertTrue(history.append_message("assistant", "Mission saved."))
                self.assertEqual(len(history.recent_messages(10)), 2)
                found = history.search_messages("JARVIS")
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["role"], "user")

    def test_history_physically_purges_expired_rows(self):
        from memory import conversation_history as history
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            old = (datetime.now().astimezone() - timedelta(days=100)).isoformat()
            fresh = datetime.now().astimezone().isoformat()
            path.write_text(
                json.dumps({"ts": old, "role": "user", "text": "old"}) + "\n"
                + json.dumps({"ts": fresh, "role": "assistant", "text": "new"}) + "\n",
                encoding="utf-8",
            )
            with patch.object(history, "_HISTORY_PATH", path), \
                 patch.object(history, "get_plugin_config",
                              return_value={"enabled": True, "retention_days": "30"}):
                rows = history.recent_messages()
                stored = path.read_text(encoding="utf-8")
            self.assertEqual([row["text"] for row in rows], ["new"])
            self.assertNotIn('"text": "old"', stored)

    def test_history_purge_never_follows_predictable_temp_symlink(self):
        from memory import conversation_history as history
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            sentinel = Path(tmp) / "outside.txt"
            sentinel.write_text("leave this file untouched", encoding="utf-8")
            path.with_suffix(".jsonl.tmp").symlink_to(sentinel)
            old = (datetime.now().astimezone() - timedelta(days=100)).isoformat()
            path.write_text(
                json.dumps({"ts": old, "role": "user", "text": "expired"}) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(history, "_HISTORY_PATH", path),
                patch.object(history, "get_plugin_config",
                             return_value={"enabled": True, "retention_days": "30"}),
            ):
                self.assertEqual(history.recent_messages(), [])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "leave this file untouched")
            self.assertTrue(path.with_suffix(".jsonl.tmp").is_symlink())

    def test_history_rotation_never_follows_predictable_temp_symlink(self):
        from memory import conversation_history as history
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            sentinel = Path(tmp) / "outside.txt"
            sentinel.write_text("leave this file untouched", encoding="utf-8")
            path.with_suffix(".jsonl.tmp").symlink_to(sentinel)
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            with (
                patch.object(history, "_HISTORY_PATH", path),
                patch.object(history, "_MAX_FILE_BYTES", 1),
                patch.object(history, "_MAX_KEEP_LINES", 1),
            ):
                history._rotate_if_needed()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "leave this file untouched")
            self.assertEqual(path.read_text(encoding="utf-8"), "three\n")
            self.assertTrue(path.with_suffix(".jsonl.tmp").is_symlink())

    def test_history_clear_removes_local_archive(self):
        from memory import conversation_history as history
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text('{"role":"user","text":"test"}\n', encoding="utf-8")
            with patch.object(history, "_HISTORY_PATH", path):
                self.assertTrue(history.clear_history())
                self.assertFalse(path.exists())

    def test_history_redacts_common_api_secrets(self):
        from memory import conversation_history as history
        text = "token=sk-1234567890abcdef API_KEY=AIza12345678901234567890"
        clean = history._clean_text(text)
        self.assertNotIn("sk-1234567890abcdef", clean)
        self.assertNotIn("AIza12345678901234567890", clean)
        self.assertIn("[REDACTED]", clean)


class ConversationHistoryPluginTests(unittest.TestCase):
    def test_clear_requires_hud_confirmation_and_works_when_history_off(self):
        from memory import conversation_history as store
        from plugins import conversation_history as plugin
        from core import confirm
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text('{"role":"user","text":"private"}\n', encoding="utf-8")
            captured = {}

            def fake_request(key, title, detail, run):
                captured["run"] = run
                return "[CONFIRMATION_PENDING]"

            with patch.object(store, "_HISTORY_PATH", path), \
                 patch.object(store, "settings", return_value={"enabled": False, "retention_days": 30}), \
                 patch.object(confirm, "pending_title", return_value=""), \
                 patch.object(confirm, "request", side_effect=fake_request):
                result = plugin.run({"action": "clear"})
                self.assertIn("CONFIRMATION_PENDING", result)
                self.assertTrue(path.exists(), "data must remain until the user confirms")
                self.assertIn("cleared", captured["run"]().lower())
                self.assertFalse(path.exists())


class MissionControlTests(unittest.TestCase):
    def _new_scope(self, request="Run the test mission"):
        from core.autonomy_policy import new_turn_authorization
        return new_turn_authorization(request)

    def _record_done(self, mission, scope, tool_name="test_tool"):
        result = f"status=done action={tool_name} verified=true"
        self.assertTrue(mission.record_tool_result(scope, tool_name, result))
        return result

    def test_plan_progress_pause_and_resume_persist(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                created = mission.run({
                    "action": "plan", "title": "Build the assistant",
                    "goal": "Create a safe multi-step assistant",
                    "steps": "Audit capabilities\nAdd persistent task tracking",
                })
                self.assertIn("MISSION M-", created)
                mission_id = created.split("MISSION ", 1)[1].split(":", 1)[0]
                scope = self._new_scope()

                next_step = mission.run({"action": "next"})
                self.assertIn("S01", next_step)
                started = mission.run(
                    {"action": "start", "mission_id": mission_id},
                    session_memory=scope,
                )
                self.assertIn("[MISSION_CONTINUE]", started)
                self._record_done(mission, scope, "audit_action")
                done = mission.run({
                    "action": "complete_step", "mission_id": mission_id,
                    "step_id": "S01", "verification": "Audit completed.",
                }, session_memory=scope)
                self.assertIn("[MISSION_CONTINUE]", done)
                paused = mission.run({"action": "pause", "mission_id": mission_id})
                self.assertIn("Status: paused", paused)
                resumed = mission.run(
                    {"action": "resume", "mission_id": mission_id},
                    session_memory=scope,
                )
                self.assertIn("[MISSION_RECOVERY_REQUIRED]", resumed)
                mission.record_tool_result(
                    scope, "inspect_active_window", "- Text: current state", {},
                )
                recovered = mission.run({
                    "action": "resolve_step", "mission_id": mission_id, "step_id": "S02",
                    "resolution": "retry_safe", "verification": "No effect was found.",
                }, session_memory=scope)
                self.assertIn("[MISSION_CONTINUE]", recovered)

                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["missions"][0]["steps"][0]["status"], "done")
                self.assertIn("verified", stored["missions"][0]["steps"][0]["verification"].lower())
                self.assertEqual(stored["missions"][0]["status"], "active")

    def test_autonomous_mission_continues_until_verified_completion(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                planned = mission.run({
                    "action": "plan", "title": "Create a project",
                    "goal": "Create and verify two project files",
                    "steps": "Create the first file\nCreate the second file",
                })
                mission_id = planned.split("MISSION ", 1)[1].split(":", 1)[0]
                self.assertNotIn("[MISSION_CONTINUE]", planned)
                scope = self._new_scope("Create both project files")

                started = mission.run(
                    {"action": "start", "mission_id": mission_id},
                    session_memory=scope,
                )
                self.assertIn("[MISSION_CONTINUE]", started)
                self.assertIn("STEP_ID=S01", started)
                self._record_done(mission, scope, "create_file")
                first = mission.run({
                    "action": "complete_step", "mission_id": mission_id,
                    "step_id": "S01", "verification": "First file exists.",
                }, session_memory=scope)
                self.assertIn("[MISSION_CONTINUE]", first)
                self.assertIn("STEP_ID=S02", first)

                self._record_done(mission, scope, "create_file")
                final = mission.run({
                    "action": "complete_step", "mission_id": mission_id,
                    "step_id": "S02", "verification": "Second file exists.",
                }, session_memory=scope)
                self.assertIn("[MISSION_COMPLETE]", final)
                self.assertIn("Status: completed", final)
                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["missions"][0]["execution_mode"], "autonomous")
                self.assertEqual(
                    [step["status"] for step in stored["missions"][0]["steps"]],
                    ["done", "done"],
                )

    def test_autonomous_mission_rejects_out_of_order_and_requires_recovery_inspection(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                planned = mission.run({
                    "action": "plan", "title": "Ordered job",
                    "goal": "Perform steps in order",
                    "steps": "First step\\nSecond step",
                })
                mission_id = planned.split("MISSION ", 1)[1].split(":", 1)[0]
                scope = self._new_scope("Run ordered job")
                mission.run({"action": "start", "mission_id": mission_id},
                            session_memory=scope)
                out_of_order = mission.run({
                    "action": "complete_step", "mission_id": mission_id,
                    "step_id": "S02", "verification": "Attempted second step",
                }, session_memory=scope)
                self.assertIn("Out-of-order", out_of_order)
                blocked = mission.run({
                    "action": "block_step", "mission_id": mission_id,
                    "step_id": "S01", "reason": "A required permission is missing.",
                }, session_memory=scope)
                self.assertIn("[MISSION_BLOCKED]", blocked)
                resumed = mission.run(
                    {"action": "resume", "mission_id": mission_id}, session_memory=scope,
                )
                self.assertIn("[MISSION_RECOVERY_REQUIRED]", resumed)
                refused = mission.run({
                    "action": "resolve_step", "mission_id": mission_id,
                    "step_id": "S01", "resolution": "retry_safe",
                    "verification": "No side effect occurred.",
                }, session_memory=scope)
                self.assertIn("actual read-only inspection", refused)
                mission.record_tool_result(
                    scope, "inspect_active_window", "- Text: unchanged", {},
                )
                resolved = mission.run({
                    "action": "resolve_step", "mission_id": mission_id,
                    "step_id": "S01", "resolution": "retry_safe",
                    "verification": "Read-only inspection proves no side effect.",
                }, session_memory=scope)
                self.assertIn("[MISSION_CONTINUE]", resolved)

    def test_paused_mission_requires_real_read_only_recovery_evidence(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                planned = mission.run({
                    "action": "plan", "title": "Paused job",
                    "goal": "Do one task safely", "steps": "Do the task",
                })
                mission_id = planned.split("MISSION ", 1)[1].split(":", 1)[0]
                scope = self._new_scope("Resume paused task")
                mission.run({"action": "start", "mission_id": mission_id}, session_memory=scope)
                mission.run({
                    "action": "block_step", "mission_id": mission_id,
                    "step_id": "S01", "reason": "Need inspection.",
                }, session_memory=scope)
                resumed = mission.run(
                    {"action": "resume", "mission_id": mission_id}, session_memory=scope,
                )
                self.assertIn("[MISSION_RECOVERY_REQUIRED]", resumed)
                refused = mission.run({
                    "action": "resolve_step", "mission_id": mission_id, "step_id": "S01",
                    "resolution": "completed", "verification": "I verified it.",
                }, session_memory=scope)
                self.assertIn("actual read-only inspection", refused)
                mission.record_tool_result(
                    scope, "inspect_active_window", "- Text: Completed", {},
                )
                recovered = mission.run({
                    "action": "resolve_step", "mission_id": mission_id, "step_id": "S01",
                    "resolution": "completed", "verification": "The completed state is visible.",
                }, session_memory=scope)
                self.assertIn("[MISSION_COMPLETE]", recovered)

    def test_corrupt_mission_store_is_not_overwritten(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(mission, "_DATA_PATH", path):
                result = mission.run({"action": "list"})
            self.assertIn("unreadable", result.lower())
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_interrupted_running_step_is_blocked_until_read_only_recovery(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                planned = mission.run({
                    "action": "plan", "title": "Safe interrupted task",
                    "goal": "Perform one side effect exactly once",
                    "steps": "Create a marker file",
                })
                mission_id = planned.split("MISSION ", 1)[1].split(":", 1)[0]
                scope = self._new_scope("Create one marker")
                started = mission.run(
                    {"action": "start", "mission_id": mission_id},
                    session_memory=scope,
                )
                self.assertIn("[MISSION_CONTINUE]", started)
                next_scope = self._new_scope("Continue marker")
                resumed = mission.run(
                    {"action": "resume", "mission_id": mission_id},
                    session_memory=next_scope,
                )
                self.assertIn("[MISSION_RECOVERY_REQUIRED]", resumed)
                state = mission._read()["missions"][0]
                self.assertEqual(state["status"], "paused")
                self.assertEqual(state["steps"][0]["status"], "blocked")

    def test_step_completion_requires_a_started_step_and_tool_evidence(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                planned = mission.run({
                    "action": "plan", "title": "Guard test",
                    "goal": "Require a verified step", "steps": "Do one thing",
                })
                mission_id = planned.split("MISSION ", 1)[1].split(":", 1)[0]
                scope = self._new_scope("Do one thing")
                refused_before_start = mission.run({
                    "action": "complete_step", "mission_id": mission_id, "step_id": "S01",
                    "verification": "Forged success",
                }, session_memory=scope)
                self.assertIn("currently running step", refused_before_start)
                mission.run(
                    {"action": "start", "mission_id": mission_id},
                    session_memory=scope,
                )
                refused_without_receipt = mission.run({
                    "action": "complete_step", "mission_id": mission_id, "step_id": "S01",
                    "verification": "Forged success",
                }, session_memory=scope)
                self.assertIn("No matching verified tool result", refused_without_receipt)


class KnowledgeVaultTests(unittest.TestCase):
    def setUp(self):
        undo.clear()

    def tearDown(self):
        undo.clear()

    def test_markdown_notes_create_search_read_and_undo(self):
        from plugins import knowledge_vault as vault
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Vault"
            with patch.object(vault, "get_plugin_config", return_value={"vault_path": str(root)}):
                created = vault.run({
                    "action": "create", "path": "Projects/Jarvis",
                    "content": "# JARVIS\nMission Control and memory.",
                })
                self.assertIn("Saved Projects/Jarvis.md", created)
                note = root / "Projects" / "Jarvis.md"
                self.assertTrue(note.exists())

                found = vault.run({"action": "search", "query": "Mission Control"})
                self.assertIn("Mission Control", found)
                read = vault.run({"action": "read", "path": "Projects/Jarvis.md"})
                self.assertIn("# JARVIS", read)

                undone = undo.undo_last()
                self.assertIn("Undone", undone)
                self.assertFalse(note.exists())

    def test_vault_blocks_parent_path_escape(self):
        from plugins import knowledge_vault as vault
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Vault"
            with patch.object(vault, "get_plugin_config", return_value={"vault_path": str(root)}):
                result = vault.run({
                    "action": "create", "path": "../outside.md", "content": "do not write"
                })
            self.assertIn("failed", result.lower())
            self.assertFalse((Path(tmp) / "outside.md").exists())


class VoiceInterruptTests(unittest.TestCase):
    def test_echo_guard_needs_calibration_and_sustained_speech(self):
        from core.echo import EchoGuard
        guard = EchoGuard()
        self.assertFalse(guard.interrupt_ready)
        guard._residuals = [0.02] * 16
        guard._run = guard.required_blocks - 1
        self.assertFalse(guard.interrupt_ready)
        guard._run = guard.required_blocks
        self.assertTrue(guard.interrupt_ready)


if __name__ == "__main__":
    unittest.main(verbosity=2)
