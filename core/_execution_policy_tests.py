"""Offline tests for confirmation of code execution."""
import unittest

from core.execution_policy import (
    requires_code_helper_confirmation, requires_dev_agent_confirmation,
)


class ExecutionPolicyTests(unittest.TestCase):
    def test_dev_agent_requires_confirmation_except_status(self):
        self.assertTrue(requires_dev_agent_confirmation({"mode": "build"}))
        self.assertTrue(requires_dev_agent_confirmation({"mode": "improve"}))
        self.assertTrue(requires_dev_agent_confirmation({}))
        self.assertFalse(requires_dev_agent_confirmation({"mode": "status"}))

    def test_code_helper_confirms_only_execution_intents(self):
        for action in ("run", "build", " RUN "):
            with self.subTest(action=action):
                self.assertTrue(requires_code_helper_confirmation(action))
        for action in ("write", "edit", "explain", "optimize", "screen_debug", "auto"):
            with self.subTest(action=action):
                self.assertFalse(requires_code_helper_confirmation(action))


if __name__ == "__main__":
    unittest.main()
