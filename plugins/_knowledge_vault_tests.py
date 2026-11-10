"""Offline regression tests for Knowledge Vault write and Undo safety."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import knowledge_vault as vault


class KnowledgeVaultUndoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.callbacks = []
        self.root_patch = patch.object(vault, "_root", return_value=self.root)
        self.undo_patch = patch.object(
            vault, "push_undo",
            side_effect=lambda _description, callback: self.callbacks.append(callback),
        )
        self.root_patch.start()
        self.undo_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.undo_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def _create(self, content="Jarvis note"):
        result = vault.run({"action": "create", "path": "Notes/test.md", "content": content})
        self.assertIn("Saved ", result)
        target = self.root / "Notes" / "test.md"
        self.assertTrue(target.is_file())
        self.assertTrue(self.callbacks)
        return target

    def test_undo_created_note_removes_unchanged_file(self):
        target = self._create()
        result = self.callbacks[-1]()
        self.assertIn("Removed newly created note", result)
        self.assertFalse(target.exists())

    def test_undo_created_note_preserves_later_user_edit(self):
        target = self._create()
        target.write_text("User's later edit", encoding="utf-8")
        result = self.callbacks[-1]()
        self.assertIn("changed since this action", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "User's later edit")

    def test_undo_append_restores_original_when_unchanged(self):
        target = self.root / "Notes" / "test.md"
        target.parent.mkdir(parents=True)
        target.write_text("Existing note\n", encoding="utf-8")
        result = vault.run({
            "action": "append", "path": "Notes/test.md", "content": "New section",
        })
        self.assertIn("Saved ", result)
        self.assertIn("New section", target.read_text(encoding="utf-8"))
        undo_result = self.callbacks[-1]()
        self.assertIn("Restored test.md", undo_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "Existing note\n")

    def test_undo_append_preserves_later_user_edit(self):
        target = self.root / "Notes" / "test.md"
        target.parent.mkdir(parents=True)
        target.write_text("Existing note\n", encoding="utf-8")
        result = vault.run({
            "action": "append", "path": "Notes/test.md", "content": "Jarvis addition",
        })
        self.assertIn("Saved ", result)
        target.write_text("User's newer note", encoding="utf-8")
        undo_result = self.callbacks[-1]()
        self.assertIn("changed since this action", undo_result)
        self.assertEqual(target.read_text(encoding="utf-8"), "User's newer note")

    def test_undo_refuses_a_replaced_symlink(self):
        target = self._create()
        outside = self.root.parent / (self.root.name + "-outside.md")
        outside.write_text("outside data", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic link creation is not permitted")
        result = self.callbacks[-1]()
        self.assertIn("symbolic link", result)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside data")
        self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main(verbosity=2)
