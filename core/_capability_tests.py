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
            path.write_text('{"role":"user","text":"private"}\\n', encoding="utf-8")
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
    def test_plan_progress_pause_and_resume_persist(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            with patch.object(mission, "_DATA_PATH", path):
                created = mission.run({
                    "action": "plan",
                    "title": "Build the assistant",
                    "goal": "Create a safe multi-step assistant",
                    "steps": "Audit capabilities\nAdd persistent task tracking",
                })
                self.assertIn("MISSION M-", created)
                mission_id = created.split("MISSION ", 1)[1].split(":", 1)[0]

                next_step = mission.run({"action": "next"})
                self.assertIn("S01", next_step)
                done = mission.run({
                    "action": "complete_step", "mission_id": mission_id, "step_id": "S01"
                })
                self.assertIn("[done]", done)
                paused = mission.run({"action": "pause", "mission_id": mission_id})
                self.assertIn("Status: paused", paused)
                resumed = mission.run({"action": "resume", "mission_id": mission_id})
                self.assertIn("Status: active", resumed)

                stored = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(stored["missions"][0]["steps"][0]["status"], "done")
                self.assertEqual(stored["missions"][0]["status"], "active")

    def test_corrupt_mission_store_is_not_overwritten(self):
        from plugins import mission_control as mission
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missions.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(mission, "_DATA_PATH", path):
                result = mission.run({"action": "list"})
            self.assertIn("unreadable", result.lower())
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


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
