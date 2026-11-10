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


if __name__ == "__main__":
    unittest.main(verbosity=2)
