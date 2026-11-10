"""Tests for app-owned mission-step evidence binding."""
import unittest

from core.mission_evidence import (
    activate_step,
    clear_step,
    record_tool_result,
    seed_step_evidence,
    verified_evidence,
    verification_summary,
)


class MissionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.scope = {"user_request": "Complete a safe task"}

    def test_only_matching_done_result_is_accepted(self):
        self.assertTrue(activate_step(self.scope, "M-1", "S01"))
        self.assertTrue(record_tool_result(
            self.scope, "click_and_verify",
            "status=done action=click_and_verify verified=true button=Save",
        ))
        evidence = verified_evidence(self.scope, "M-1", "S01")
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence["tool_name"], "click_and_verify")
        self.assertIn("status=done", verification_summary(evidence))

    def test_done_without_verified_true_is_rejected_and_clears_previous_receipt(self):
        activate_step(self.scope, "M-1", "S01")
        self.assertTrue(record_tool_result(
            self.scope, "click_and_verify",
            "status=done action=click_and_verify verified=true",
        ))
        for result in (
            "status=done action=click_and_verify",
            "status=done action=click_and_verify verified=false",
            "status=done action=click_and_verify verified=True",
            "status=done action=click_and_verify verified=false reason=no_change",
        ):
            with self.subTest(result=result):
                self.assertFalse(record_tool_result(self.scope, "click_and_verify", result))
                self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))

    def test_seed_rejects_receipts_without_verified_true(self):
        activate_step(self.scope, "M-1", "S01")
        invalid = {
            "status": "done",
            "tool_name": "click_and_verify",
            "action": "click_and_verify",
            "result_sha256": "0" * 64,
        }
        self.assertFalse(seed_step_evidence(
            self.scope, "M-1", "S01", invalid,
        ))
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))

    def test_model_only_text_cannot_create_evidence(self):
        activate_step(self.scope, "M-1", "S01")
        self.assertFalse(record_tool_result(
            self.scope, "click_and_verify", "The button was definitely clicked.",
        ))
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))

    def test_tool_name_must_match_reported_action(self):
        activate_step(self.scope, "M-1", "S01")
        self.assertFalse(record_tool_result(
            self.scope, "browser_control",
            "status=done action=send_message verified=true",
        ))
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))

    def test_failed_unverified_or_pending_result_clears_prior_success(self):
        activate_step(self.scope, "M-1", "S01")
        self.assertTrue(record_tool_result(
            self.scope, "click_and_verify",
            "status=done action=click_and_verify verified=true",
        ))
        for result in (
            "status=failed action=click_and_verify reason=missing",
            "status=unverified action=click_and_verify reason=timeout",
            "[CONFIRMATION_PENDING] wait for the HUD",
            "No result could be established",
        ):
            with self.subTest(result=result):
                self.assertFalse(record_tool_result(self.scope, "click_and_verify", result))
                self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))

    def test_evidence_is_bound_to_exact_mission_and_step(self):
        activate_step(self.scope, "M-1", "S01")
        record_tool_result(
            self.scope, "open_app", "status=done action=open_app verified=true",
        )
        self.assertIsNone(verified_evidence(self.scope, "M-2", "S01"))
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S02"))

    def test_new_step_replaces_previous_evidence(self):
        activate_step(self.scope, "M-1", "S01")
        record_tool_result(
            self.scope, "open_app", "status=done action=open_app verified=true",
        )
        activate_step(self.scope, "M-1", "S02")
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S01"))
        self.assertIsNone(verified_evidence(self.scope, "M-1", "S02"))

    def test_new_turn_scope_does_not_inherit_evidence(self):
        activate_step(self.scope, "M-1", "S01")
        record_tool_result(
            self.scope, "open_app", "status=done action=open_app verified=true",
        )
        next_turn = {"user_request": "Continue"}
        self.assertIsNone(verified_evidence(next_turn, "M-1", "S01"))

    def test_clear_is_scoped(self):
        activate_step(self.scope, "M-1", "S01")
        clear_step(self.scope, mission_id="M-2", step_id="S01")
        self.assertIn("_mission_execution", self.scope)
        clear_step(self.scope, mission_id="M-1", step_id="S01")
        self.assertNotIn("_mission_execution", self.scope)

    def test_missing_scope_fails_closed(self):
        self.assertFalse(activate_step(None, "M-1", "S01"))
        self.assertFalse(record_tool_result(None, "open_app", "status=done action=open_app"))
        self.assertIsNone(verified_evidence(None, "M-1", "S01"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
