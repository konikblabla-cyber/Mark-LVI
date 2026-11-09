"""Offline tests for user-configured background monitor storage and limits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import background_monitor
from memory import memory_manager


class BackgroundMonitorReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "long_term.json"
        self.path_patch = patch.object(memory_manager, "MEMORY_PATH", self.store)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_monitor_state_uses_shared_atomic_memory_writer(self):
        memory_manager.save_memory({"identity": {"language": {"value": "Polish"}}})
        background_monitor._save({
            "python": {"topic": "Python", "added": "2026-11-09",
                       "last_check": "", "last_hash": ""}
        })
        stored = memory_manager.load_memory()
        self.assertEqual(stored["identity"]["language"]["value"], "Polish")
        self.assertIn("python", stored["monitors"])
        self.assertFalse(list(self.store.parent.glob(".long_term-*.tmp")))

    def test_monitor_save_refuses_to_overwrite_corrupt_memory(self):
        original = "{truncated"
        self.store.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "refusing to overwrite"):
            background_monitor._save({"python": {"topic": "Python"}})
        self.assertEqual(self.store.read_text(encoding="utf-8"), original)

    def test_topic_length_is_bounded(self):
        with patch.object(background_monitor, "_load", return_value={}), \
             patch.object(background_monitor, "_save") as save:
            result = background_monitor.add_monitor("x" * (background_monitor.MAX_TOPIC_LENGTH + 1))
        self.assertIn("too long", result)
        save.assert_not_called()

    def test_number_of_monitors_is_bounded(self):
        existing = {f"topic_{i}": {"topic": f"Topic {i}"} for i in range(background_monitor.MAX_MONITORS)}
        with patch.object(background_monitor, "_load", return_value=existing), \
             patch.object(background_monitor, "_save") as save:
            result = background_monitor.add_monitor("An additional topic")
        self.assertIn("limit reached", result)
        save.assert_not_called()

    def test_non_latin_topic_gets_a_stable_nonempty_slug(self):
        first = background_monitor._slug("日本語のニュース")
        second = background_monitor._slug("日本語のニュース")
        self.assertTrue(first.startswith("topic_"))
        self.assertEqual(first, second)
        self.assertNotEqual(first, background_monitor._slug("한국어 뉴스"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
