"""Offline tests for the on-demand project status action."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import project_status


class FakePlayer:
    def __init__(self, current_file):
        self.current_file = str(current_file)
        self.content = None

    def show_content(self, title, content):
        self.content = (title, content)


class ProjectStatusTests(unittest.TestCase):
    def test_inspects_selected_folder_and_remembers_only_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Jarvis"
            root.mkdir()
            (root / "main.py").write_text("PRIVATE_SOURCE = 'do not read'", encoding="utf-8")
            player = FakePlayer(root)
            with patch.object(project_status, "remember_project_snapshot") as remember:
                result = project_status.run({}, player=player)
            self.assertIn("Selected project folder: Jarvis", result)
            self.assertNotIn("PRIVATE_SOURCE", result)
            self.assertNotIn(str(root), result)
            remember.assert_called_once()
            self.assertEqual(player.content[0], "PROJECT OVERVIEW")

    def test_requires_selected_directory(self):
        self.assertIsNone(project_status.inspect_selected_project(None))
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "file.py"
            file_path.write_text("x", encoding="utf-8")
            self.assertIsNone(project_status.inspect_selected_project(FakePlayer(file_path)))

    def test_action_registers_its_handler(self):
        self.assertTrue(callable(project_status.TOOL.get("handler")))

    def test_missing_project_is_reported_clearly(self):
        result = project_status.run({}, player=None)
        self.assertIn("No project folder is selected", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
