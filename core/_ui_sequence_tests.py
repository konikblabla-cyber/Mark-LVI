"""Offline tests for multi-step verified UI workflows."""
import unittest
from unittest.mock import patch

from actions import ui_sequence
from core.action_loader import _validate


class UISequenceTests(unittest.TestCase):
    def setUp(self):
        with ui_sequence._SEQUENCE_STATE_LOCK:
            ui_sequence._ACTIVE_SEQUENCE_ID = None

    def tearDown(self):
        with ui_sequence._SEQUENCE_STATE_LOCK:
            ui_sequence._ACTIVE_SEQUENCE_ID = None

    @staticmethod
    def _step(label, expected):
        return {"button_label": label, "expected_result": expected}

    @staticmethod
    def _fill_step(label, text):
        return {"action": "fill_text", "field_label": label, "text": text}

    @staticmethod
    def _tree_step(label, expanded):
        return {"action": "set_tree_item_expanded", "item_label": label, "expanded": expanded}

    @staticmethod
    def _snapshot(label, role="Button"):
        return {
            "handle": 101,
            "process_id": 4321,
            "records": [{"role": "Text", "name": "Application"}],
            "clickables": [{
                "role": role,
                "name": label,
                "automation_id": label.lower().replace(" ", "_"),
                "runtime_id": (1, 2, 3),
                "rect": (10, 10, 100, 40),
            }],
        }

    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_sequence, "ui_sequence.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "run_ui_sequence")
        self.assertEqual(record.parameters["required"], ["steps"])

    def test_validation_accepts_safe_fill_text_step(self):
        steps, error = ui_sequence._validate_steps([
            self._fill_step("Project title", "Quarterly plan"),
            self._step("Save", "Saved"),
        ])
        self.assertEqual(error, "")
        self.assertEqual(steps[0], {
            "action": "fill_text",
            "field_label": "Project title",
            "text": "Quarterly plan",
        })
        self.assertEqual(steps[1]["action"], "click")

    def test_validation_accepts_tree_expand_and_collapse_steps(self):
        steps, error = ui_sequence._validate_steps([
            self._tree_step("Projects", True),
            self._step("Open panel", "Panel ready"),
            self._tree_step("Projects", False),
        ])
        self.assertEqual(error, "")
        self.assertEqual(steps[0], self._tree_step("Projects", True))
        self.assertEqual(steps[2], self._tree_step("Projects", False))

    def test_validation_rejects_malformed_or_sensitive_tree_steps(self):
        invalid = [
            {"action": "set_tree_item_expanded", "item_label": "Projects", "expanded": "true"},
            {"action": "set_tree_item_expanded", "item_label": "", "expanded": True},
            {"action": "set_tree_item_expanded", "item_label": "PIN", "expanded": True},
            {"action": "set_tree_item_expanded", "item_label": "Projects", "expanded": True, "extra": 1},
            {"action": "set_tree_item_expanded", "item_label": "x" * 121, "expanded": False},
        ]
        for step in invalid:
            with self.subTest(step=step):
                validated, error = ui_sequence._validate_steps([
                    step, self._step("Open panel", "Panel ready"),
                ])
                self.assertIsNone(validated)
                self.assertTrue(error)

    def test_validation_rejects_malformed_fill_text_steps(self):
        invalid = [
            {"action": "fill_text", "field_label": "Title", "text": ""},
            {"action": "fill_text", "field_label": "", "text": "Value"},
            {"action": "fill_text", "field_label": "Title", "text": "Value", "password": False},
            {"action": "fill_text", "field_label": "Title", "text": "bad\x00value"},
            {"action": "type_anything", "field_label": "Title", "text": "Value"},
        ]
        for step in invalid:
            with self.subTest(step=step):
                validated, error = ui_sequence._validate_steps([
                    step, self._step("Save", "Saved"),
                ])
                self.assertIsNone(validated)
                self.assertTrue(error)

    def test_validation_rejects_short_long_and_extra_fields(self):
        self.assertIsNone(ui_sequence._validate_steps([])[0])
        self.assertIsNone(ui_sequence._validate_steps([
            self._step("A", "A"), self._step("B", "B"),
            *[self._step("C", "C") for _ in range(7)],
        ])[0])
        self.assertIsNone(ui_sequence._validate_steps([
            {"button_label": "Save", "expected_result": "Saved", "confirmed": True},
            self._step("Next", "Next page"),
        ])[0])

    def test_invalid_sequence_never_inspects_or_clicks_desktop(self):
        with patch.object(ui_sequence.ui, "_snapshot") as snapshot, \
             patch.object(ui_sequence.ui, "_click_and_verify") as click:
            result = ui_sequence.run_ui_sequence({"steps": [self._step("Only", "One")]})
        self.assertIn("status=failed", result)
        snapshot.assert_not_called()
        click.assert_not_called()

    def test_two_routine_steps_run_in_order_and_both_must_verify(self):
        snapshots = [
            self._snapshot("Open panel"),
            self._snapshot("Finish"),
        ]
        with patch.object(ui_sequence.ui, "_snapshot", side_effect=snapshots), \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 side_effect=[
                     "status=done action=click_and_verify verified=true",
                     "status=done action=click_and_verify verified=true",
                 ],
             ) as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Open panel", "Panel ready"),
                    self._step("Finish", "Completed"),
                ],
            })
        self.assertTrue(result.startswith("status=done action=run_ui_sequence "), result)
        self.assertIn("steps_completed=2", result)
        self.assertEqual([call.args[0] for call in click.call_args_list],
                         ["Open panel", "Finish"])

    def test_mixed_fill_and_click_steps_run_in_order(self):
        initial = self._snapshot("Application")
        click_snapshot = self._snapshot("Save")
        events = []
        with patch.object(ui_sequence, "_capture_active_window", return_value=initial), \
             patch.object(ui_sequence.ui, "_snapshot", return_value=click_snapshot), \
             patch.object(
                 ui_sequence.ui, "_fill_text_and_verify",
                 side_effect=lambda *args: (events.append(("fill", args[0])) or
                     "status=done action=fill_text_and_verify verified=true changed=true"),
             ) as fill, \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 side_effect=lambda *args: (events.append(("click", args[0])) or
                     "status=done action=click_and_verify verified=true"),
             ) as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._fill_step("Project title", "Quarterly plan"),
                    self._step("Save", "Saved"),
                ],
            })
        self.assertTrue(result.startswith("status=done action=run_ui_sequence "), result)
        self.assertEqual(events, [("fill", "Project title"), ("click", "Save")])
        fill.assert_called_once_with("Project title", "Quarterly plan", 101, 4321)
        click.assert_called_once()

    def test_unverified_fill_stops_before_next_button(self):
        initial = self._snapshot("Application")
        with patch.object(ui_sequence, "_capture_active_window", return_value=initial), \
             patch.object(
                 ui_sequence.ui, "_fill_text_and_verify",
                 return_value="status=unverified action=fill_text_and_verify reason=readback_mismatch",
             ) as fill, \
             patch.object(ui_sequence.ui, "_snapshot") as snapshot, \
             patch.object(ui_sequence.ui, "_click_and_verify") as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._fill_step("Project title", "Quarterly plan"),
                    self._step("Save", "Saved"),
                ],
            })
        self.assertTrue(result.startswith("status=unverified action=run_ui_sequence "), result)
        self.assertIn("failed_step=1", result)
        fill.assert_called_once()
        snapshot.assert_not_called()
        click.assert_not_called()

    def test_unverified_step_stops_every_later_step(self):
        snapshots = [
            self._snapshot("Open panel"),
            self._snapshot("Finish"),
        ]
        with patch.object(ui_sequence.ui, "_snapshot", side_effect=snapshots), \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 return_value="status=unverified action=click_and_verify reason=not_observed",
             ) as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Open panel", "Panel ready"),
                    self._step("Finish", "Completed"),
                ],
            })
        self.assertTrue(result.startswith("status=unverified action=run_ui_sequence "), result)
        self.assertIn("failed_step=1", result)
        self.assertEqual(click.call_count, 1)

    def test_ambiguous_later_step_reports_partial_progress_without_false_ask(self):
        first = self._snapshot("Open panel")
        second = self._snapshot("Continue")
        second["clickables"].append(dict(second["clickables"][0]))
        with patch.object(
            ui_sequence.ui, "_snapshot", side_effect=[first, second],
        ), patch.object(
            ui_sequence.ui, "_click_and_verify",
            return_value="status=done action=click_and_verify verified=true",
        ) as click, patch(
            "core.confirm.pending_title", return_value="",
        ):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Open panel", "Panel ready"),
                    self._step("Continue", "Page loaded"),
                ],
            })
        self.assertTrue(
            result.startswith("status=unverified action=run_ui_sequence "),
            result,
        )
        self.assertIn("steps_completed=1", result)
        self.assertIn("failed_step=2", result)
        self.assertNotIn("NOTHING was done yet", result)
        click.assert_called_once()

    def test_duplicate_target_stops_before_any_click(self):
        snapshot = self._snapshot("Continue")
        snapshot["clickables"].append(dict(snapshot["clickables"][0]))
        with patch.object(ui_sequence.ui, "_snapshot", return_value=snapshot), \
             patch.object(ui_sequence.ui, "_click_and_verify") as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Continue", "Page one"),
                    self._step("Finish", "Done"),
                ],
            })
        self.assertTrue(result.startswith("status=ask action=run_ui_sequence "), result)
        click.assert_not_called()

    def test_high_impact_step_waits_for_hud_then_resumes_remaining_steps(self):
        from core import confirm
        snapshots = [
            self._snapshot("Delete"),
            self._snapshot("Open panel"),
        ]
        with patch.object(ui_sequence.ui, "_snapshot", side_effect=snapshots), \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 side_effect=[
                     "status=done action=click_and_verify verified=true",
                     "status=done action=click_and_verify verified=true",
                 ],
             ) as click, \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", return_value="[CONFIRMATION_PENDING]") as request:
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Delete", "Deleted"),
                    self._step("Open panel", "Panel ready"),
                ],
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            self.assertEqual(click.call_count, 0)
            request.assert_called_once()
            callback = request.call_args.kwargs["run"]
            cancel = request.call_args.kwargs["on_cancel"]
            self.assertTrue(callable(cancel))
            resumed = callback()

        self.assertTrue(resumed.startswith("status=done action=run_ui_sequence "), resumed)
        self.assertIn("steps_completed=2", resumed)
        self.assertEqual(click.call_count, 2)

    def test_tree_expand_then_click_runs_in_order_and_uses_pinned_window(self):
        initial = self._snapshot("Application")
        click_snapshot = self._snapshot("Open panel")
        events = []
        with patch.object(ui_sequence, "_capture_active_window", return_value=initial), \
             patch.object(ui_sequence.ui, "_snapshot", return_value=click_snapshot) as snapshot, \
             patch.object(ui_sequence.ui_tree, "_requires_confirmation", return_value=False), \
             patch.object(
                 ui_sequence.ui_tree, "_set_tree_item_expanded_in_window",
                 side_effect=lambda *args: (
                     events.append(("tree", args[0], args[1], args[2], args[3]))
                     or "status=done action=set_tree_item_expanded verified=true expanded=true"
                 ),
             ) as expand, \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 side_effect=lambda *args: (
                     events.append(("click", args[0]))
                     or "status=done action=click_and_verify verified=true"
                 ),
             ) as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._tree_step("Projects", True),
                    self._step("Open panel", "Panel ready"),
                ],
            })
        self.assertTrue(result.startswith("status=done action=run_ui_sequence "), result)
        self.assertIn("steps_completed=2", result)
        self.assertEqual(events, [
            ("tree", "Projects", True, 101, 4321),
            ("click", "Open panel"),
        ])
        expand.assert_called_once_with("Projects", True, 101, 4321)
        click.assert_called_once()
        snapshot.assert_called_once_with(101, 4321)

    def test_unverified_tree_step_stops_before_click(self):
        initial = self._snapshot("Application")
        with patch.object(ui_sequence, "_capture_active_window", return_value=initial), \
             patch.object(ui_sequence.ui_tree, "_requires_confirmation", return_value=False), \
             patch.object(
                 ui_sequence.ui_tree, "_set_tree_item_expanded_in_window",
                 return_value="status=unverified action=set_tree_item_expanded reason=state_not_observed",
             ) as expand, \
             patch.object(ui_sequence.ui, "_snapshot") as snapshot, \
             patch.object(ui_sequence.ui, "_click_and_verify") as click, \
             patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._tree_step("Projects", True),
                    self._step("Open panel", "Panel ready"),
                ],
            })
        self.assertTrue(result.startswith("status=unverified action=run_ui_sequence "), result)
        self.assertIn("failed_step=1", result)
        expand.assert_called_once_with("Projects", True, 101, 4321)
        snapshot.assert_not_called()
        click.assert_not_called()

    def test_high_impact_tree_step_waits_for_hud_then_resumes(self):
        from core import confirm
        initial = self._snapshot("Application")
        click_snapshot = self._snapshot("Open panel")
        requests = []
        def fake_request(**kwargs):
            requests.append(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(ui_sequence, "_capture_active_window", return_value=initial), \
             patch.object(ui_sequence.ui, "_snapshot", return_value=click_snapshot), \
             patch.object(ui_sequence.ui_tree, "_requires_confirmation", return_value=True), \
             patch.object(
                 ui_sequence.ui_tree, "_set_tree_item_expanded_in_window",
                 return_value="status=done action=set_tree_item_expanded verified=true expanded=true",
             ) as expand, \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 return_value="status=done action=click_and_verify verified=true",
             ) as click, \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", side_effect=fake_request):
            pending = ui_sequence.run_ui_sequence({
                "steps": [
                    self._tree_step("Privacy", True),
                    self._step("Open panel", "Panel ready"),
                ],
            })
            self.assertEqual(pending, "[CONFIRMATION_PENDING]")
            expand.assert_not_called()
            click.assert_not_called()
            self.assertEqual(len(requests), 1)
            self.assertIn("Privacy", requests[0]["title"])
            resumed = requests[0]["run"]()

        self.assertTrue(resumed.startswith("status=done action=run_ui_sequence "), resumed)
        self.assertIn("steps_completed=2", resumed)
        expand.assert_called_once_with("Privacy", True, 101, 4321)
        click.assert_called_once()

    def test_two_critical_steps_request_two_distinct_hud_confirmations(self):
        from core import confirm
        snapshots = [
            self._snapshot("Open panel"),
            self._snapshot("Delete"),
            self._snapshot("Send"),
        ]
        requests = []
        def fake_request(**kwargs):
            requests.append(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(ui_sequence.ui, "_snapshot", side_effect=snapshots), \
             patch.object(
                 ui_sequence.ui, "_click_and_verify",
                 side_effect=[
                     "status=done action=click_and_verify verified=true",
                     "status=done action=click_and_verify verified=true",
                     "status=done action=click_and_verify verified=true",
                 ],
             ) as click, \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", side_effect=fake_request):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Open panel", "Panel ready"),
                    self._step("Delete", "Deleted"),
                    self._step("Send", "Sent"),
                ],
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            self.assertEqual(click.call_count, 1)
            second_pending = requests[0]["run"]()
            self.assertEqual(second_pending, "[CONFIRMATION_PENDING]")
            self.assertEqual(click.call_count, 2)
            final_result = requests[1]["run"]()

        self.assertTrue(final_result.startswith("status=done action=run_ui_sequence "), final_result)
        self.assertEqual(len(requests), 2)
        self.assertEqual(click.call_count, 3)

    def test_cancelled_sequence_releases_guard_for_next_task(self):
        from core import confirm
        requests = []
        def fake_request(**kwargs):
            requests.append(kwargs)
            return "[CONFIRMATION_PENDING]"

        with patch.object(ui_sequence.ui, "_snapshot", return_value=self._snapshot("Delete")), \
             patch.object(ui_sequence.ui, "_click_and_verify") as click, \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", side_effect=fake_request):
            pending = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Delete", "Deleted"),
                    self._step("Open panel", "Panel ready"),
                ],
            })
            self.assertEqual(pending, "[CONFIRMATION_PENDING]")
            requests[0]["on_cancel"]()
            click.assert_not_called()

        with patch.object(ui_sequence.ui, "_snapshot", side_effect=[
            self._snapshot("Open panel"), self._snapshot("Finish"),
        ]), patch.object(
            ui_sequence.ui, "_click_and_verify",
            return_value="status=done action=click_and_verify verified=true",
        ), patch("core.confirm.pending_title", return_value=""):
            result = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Open panel", "Panel ready"),
                    self._step("Finish", "Completed"),
                ],
            })
        self.assertTrue(result.startswith("status=done action=run_ui_sequence "), result)

    def test_second_sequence_is_refused_while_first_waits_for_confirmation(self):
        from core import confirm
        with patch.object(ui_sequence.ui, "_snapshot", return_value=self._snapshot("Delete")), \
             patch.object(ui_sequence.ui, "_click_and_verify") as click, \
             patch.object(confirm, "pending_title", return_value=""), \
             patch.object(confirm, "request", return_value="[CONFIRMATION_PENDING]"):
            first = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Delete", "Deleted"),
                    self._step("Open panel", "Panel ready"),
                ],
            })
            second = ui_sequence.run_ui_sequence({
                "steps": [
                    self._step("Delete", "Deleted"),
                    self._step("Open panel", "Panel ready"),
                ],
            })
        self.assertEqual(first, "[CONFIRMATION_PENDING]")
        self.assertIn("status=ask", second)
        click.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
