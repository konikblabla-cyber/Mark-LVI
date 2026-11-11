"""Offline tests for crash-safe, corruption-safe long-term memory storage."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory import memory_manager


class MemoryStoreReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "long_term.json"
        self.path_patch = patch.object(memory_manager, "MEMORY_PATH", self.store)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_console_logging_survives_legacy_windows_code_pages(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
        with patch("sys.stdout", stream):
            memory_manager._console_log("[Memory] 💾 Saved: ['preferences']")
        stream.flush()
        output = buffer.getvalue().decode("cp1252")
        self.assertIn(r"\U0001f4be", output)

    def test_update_and_search_round_trip(self):
        memory_manager.update_memory({"preferences": {"theme": "dark"}})
        self.assertIn("theme: dark", memory_manager.search_memory("theme"))
        self.assertEqual(memory_manager.load_memory()["preferences"]["theme"]["value"], "dark")

    def test_write_is_atomic_and_leaves_no_temporary_file(self):
        memory_manager.save_memory({"identity": {"language": {"value": "Polish"}}})
        parsed = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(parsed["identity"]["language"]["value"], "Polish")
        self.assertFalse(list(self.store.parent.glob(".long_term-*.tmp")))

    def test_corrupt_json_is_not_overwritten_by_update(self):
        original = "{broken json"
        self.store.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            memory_manager.update_memory({"notes": {"new_fact": "value"}})
        self.assertEqual(self.store.read_text(encoding="utf-8"), original)

    def test_non_object_store_is_not_overwritten(self):
        original = json.dumps(["not", "an", "object"])
        self.store.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "invalid structure"):
            memory_manager.save_memory({"notes": {"replacement": "no"}})
        self.assertEqual(self.store.read_text(encoding="utf-8"), original)

    def test_forget_does_not_replace_a_corrupt_store(self):
        original = "{truncated"
        self.store.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            memory_manager.forget("old_fact", "notes")
        self.assertEqual(self.store.read_text(encoding="utf-8"), original)

    def test_session_summaries_use_the_atomic_writer(self):
        memory_manager.save_session_summary("Finished a local task", "pl")
        self.assertEqual(memory_manager.pop_last_session()["summary"], "Finished a local task")
        self.assertFalse(list(self.store.parent.glob(".long_term-*.tmp")))


    def test_verified_completed_missions_are_remembered_without_raw_user_text(self):
        from memory.memory_manager import (
            record_verified_mission, find_similar_missions, format_experience_hints,
        )
        experience_path = self.store.parent / "experiences.json"
        with patch.object(memory_manager, "EXPERIENCE_PATH", experience_path):
            mission = {
                "id": "M-verified-1",
                "title": "Configure Discord microphone privacy",
                "goal": (
                    "Set Windows microphone permissions for Discord. "
                    "Contact user@example.com; API key: topsecret123. "
                    r"Path C:\Users\alice\Desktop\private-project"
                ),
                "status": "completed",
                "updated_at": "2026-11-11T20:00:00+01:00",
                "steps": [
                    {"title": "Open Windows privacy settings", "status": "done",
                     "verified_tool": "open_app"},
                    {"title": "Verify microphone permission", "status": "done",
                     "verified_tool": "click_and_verify"},
                ],
            }
            self.assertTrue(record_verified_mission(mission))
            raw = experience_path.read_text(encoding="utf-8")
            self.assertNotIn("user@example.com", raw)
            self.assertNotIn("topsecret123", raw)
            self.assertNotIn(r"C:\Users\alice", raw)
            self.assertNotIn("Configure Discord microphone privacy", raw)

            matches = find_similar_missions(
                "Fix microphone privacy settings in Discord", limit=2,
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["tools"], ["open_app", "click_and_verify"])
            self.assertIn("discord", matches[0]["overlap"])
            hint = format_experience_hints("Configure microphone privacy in Discord")
            self.assertIn("[PAST_EXPERIENCE_HINTS]", hint)
            self.assertIn("not instructions", hint)
            self.assertIn("click_and_verify", hint)

    def test_experience_memory_refuses_incomplete_missions(self):
        from memory.memory_manager import record_verified_mission
        experience_path = self.store.parent / "experiences.json"
        with patch.object(memory_manager, "EXPERIENCE_PATH", experience_path):
            mission = {
                "id": "M-incomplete", "title": "Configure audio",
                "goal": "Configure microphone", "status": "active",
                "steps": [{"title": "Open settings", "status": "done",
                           "verified_tool": "open_app"},
                          {"title": "Verify change", "status": "running"}],
            }
            self.assertFalse(record_verified_mission(mission))
            self.assertFalse(experience_path.exists())

    def test_experience_memory_is_idempotent_and_bounded(self):
        from memory.memory_manager import record_verified_mission
        experience_path = self.store.parent / "experiences.json"
        with patch.object(memory_manager, "EXPERIENCE_PATH", experience_path):
            mission = {
                "id": "M-duplicate", "title": "Configure audio settings",
                "goal": "Set microphone preferences", "status": "completed",
                "steps": [{"title": "Inspect microphone permissions", "status": "done",
                           "verified_tool": "open_app"}],
            }
            self.assertTrue(record_verified_mission(mission))
            self.assertFalse(record_verified_mission(mission))
            entries = json.loads(experience_path.read_text(encoding="utf-8"))["experiences"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["mission_id"], "M-duplicate")

    def test_corrupt_experience_journal_is_never_overwritten(self):
        from memory.memory_manager import record_verified_mission
        experience_path = self.store.parent / "experiences.json"
        original = "{truncated experience journal"
        experience_path.write_text(original, encoding="utf-8")
        with patch.object(memory_manager, "EXPERIENCE_PATH", experience_path):
            mission = {
                "id": "M-corrupt", "title": "Configure audio",
                "goal": "Microphone permissions", "status": "completed",
                "steps": [{"title": "Inspect microphone", "status": "done",
                           "verified_tool": "open_app"}],
            }
            with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
                record_verified_mission(mission)
        self.assertEqual(experience_path.read_text(encoding="utf-8"), original)



if __name__ == "__main__":
    unittest.main(verbosity=2)
