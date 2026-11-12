"""Keep the prompt's tool-once rule compatible with sequential missions."""
from pathlib import Path
import unittest


PROMPT = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")


class PromptExecutionContractTests(unittest.TestCase):
    def test_tool_once_rule_does_not_forbid_distinct_sequential_invocations(self):
        execution = PROMPT.split("[EXECUTION]", 1)[1].split("[AUTONOMY", 1)[0]
        self.assertIn("Execute each individual tool call once.", execution)
        self.assertIn("NOT a ban on a later, distinct invocation of the same tool", execution)
        self.assertIn("step's real tool result and application hand-off", execution)
        self.assertIn("Never pre-execute or batch", execution)

    def test_mission_control_still_requires_verified_step_completion(self):
        self.assertIn("immediately call complete_step", PROMPT)
        self.assertIn("matching structured status=done verified=true result", PROMPT)
        self.assertIn("continue until [MISSION_COMPLETE]", PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
