"""Offline tests for privacy-preserving UI form entry."""
import sys
import types
import unittest
from unittest.mock import patch

from actions import ui_form
from core.action_loader import _validate


class _Rect:
    left, top, right, bottom = 10, 10, 180, 38
    def width(self): return self.right - self.left
    def height(self): return self.bottom - self.top


class _Pattern:
    def __init__(self, value="", read_only=False, mutate_before_error=False):
        self.CurrentValue = value
        self.CurrentIsReadOnly = read_only
        self.mutate_before_error = mutate_before_error
    def SetValue(self, value):
        self.CurrentValue = value
        if self.mutate_before_error:
            raise RuntimeError("simulated post-write provider error")


class _Info:
    control_type = "Edit"
    is_password = False
    is_offscreen = False
    process_id = 4242
    def __init__(self, name, automation_id):
        self.name = name
        self.automation_id = automation_id
        self.runtime_id = (1, 2, 3)


class _Control:
    def __init__(self, name, value="", read_only=False, mutate_before_error=False):
        self.element_info = _Info(name, name.lower().replace(" ", "_"))
        self._pattern = _Pattern(value, read_only, mutate_before_error)
    def rectangle(self): return _Rect()
    def is_enabled(self): return True
    @property
    def iface_value(self): return self._pattern


class _Window:
    handle = 77
    def __init__(self, controls):
        self._controls = controls
        self.element_info = types.SimpleNamespace(process_id=4242)
    def is_visible(self): return True
    def descendants(self): return list(self._controls)


class _Desktop:
    def __init__(self, window):
        self._window = window
    def window(self, handle):
        assert int(handle) == self._window.handle
        return types.SimpleNamespace(wrapper_object=lambda: self._window)


class UIFormTests(unittest.TestCase):
    def _fields(self, *pairs):
        return [{"field_label": label, "text": value} for label, value in pairs]

    def _with_desktop(self, controls, callback):
        window = _Window(controls)
        fake = types.ModuleType("pywinauto")
        fake.Desktop = lambda backend="uia": _Desktop(window)
        with patch.dict(sys.modules, {"pywinauto": fake}):
            return callback(window)

    def test_action_is_discoverable_by_real_loader(self):
        record = _validate(ui_form, "ui_form.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "fill_ui_fields")
        self.assertEqual(record.parameters["required"], ["fields"])

    def test_validation_accepts_explicit_non_sensitive_fields(self):
        fields, error = ui_form._validate_fields(self._fields(
            ("Display name", "MARK"),
            ("City", "Szczecin"),
        ))
        self.assertEqual(error, "")
        self.assertEqual(len(fields), 2)

    def test_validation_rejects_malformed_or_excessive_input(self):
        invalid = [
            None, [], [self._fields(("Name", "A"))[0]] * 9,
            [{"field_label": "Name", "text": "A", "confirmed": True}],
            [{"field_label": "Name", "text": ""}],
            [{"field_label": "Name", "text": "x" * 2001}],
            [{"field_label": "Name", "text": "bad\\x00value"}],
            self._fields(("Name", "A"), (" name ", "B")),
        ]
        for fields in invalid:
            with self.subTest(fields=str(fields)[:80]):
                self.assertIsNone(ui_form._validate_fields(fields)[0])

    def test_validation_refuses_credentials_and_sensitive_identifiers(self):
        for label in (
            "Password", "PIN", "One-time code", "CVV", "Card number",
            "Bank account", "PESEL", "API key", "Hasło",
        ):
            with self.subTest(label=label):
                fields, error = ui_form._validate_fields(self._fields((label, "value")))
                self.assertIsNone(fields)
                self.assertIn("will not fill", error)

    def test_exact_values_are_set_and_verified_without_returning_contents(self):
        name = _Control("Display name", "Old name")
        city = _Control("City", "Old city")
        result = self._with_desktop(
            [name, city],
            lambda _window: ui_form._fill_fields_on_window(
                77, 4242, self._fields(
                    ("Display name", "MARK"), ("City", "Szczecin"),
                ), __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=done action=fill_ui_fields "), result)
        self.assertEqual(name.iface_value.CurrentValue, "MARK")
        self.assertEqual(city.iface_value.CurrentValue, "Szczecin")
        self.assertNotIn("Szczecin", result)
        self.assertNotIn("MARK", result)
        self.assertIn("submitted=false", result)

    def test_duplicate_exact_labels_are_not_ranked(self):
        first, second = _Control("City"), _Control("City")
        result = self._with_desktop(
            [first, second],
            lambda _window: ui_form._fill_fields_on_window(
                77, 4242, self._fields(("City", "Szczecin")),
                __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=ask action=fill_ui_fields "), result)
        self.assertEqual(first.iface_value.CurrentValue, "")
        self.assertEqual(second.iface_value.CurrentValue, "")

    def test_read_only_field_is_rejected_before_any_write(self):
        first = _Control("City", "Old")
        second = _Control("Display name", read_only=True)
        result = self._with_desktop(
            [first, second],
            lambda _window: ui_form._fill_fields_on_window(
                77, 4242, self._fields(
                    ("City", "New"), ("Display name", "MARK"),
                ), __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=failed action=fill_ui_fields "), result)
        self.assertEqual(first.iface_value.CurrentValue, "Old")

    def test_pid_mismatch_prevents_any_field_change(self):
        control = _Control("City", "Old")
        result = self._with_desktop(
            [control],
            lambda _window: ui_form._fill_fields_on_window(
                77, 9999, self._fields(("City", "New")),
                __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=failed action=fill_ui_fields "), result)
        self.assertEqual(control.iface_value.CurrentValue, "Old")

    def test_provider_error_after_write_is_unverified_not_failed(self):
        control = _Control("City", "Old", mutate_before_error=True)
        result = self._with_desktop(
            [control],
            lambda _window: ui_form._fill_fields_on_window(
                77, 4242, self._fields(("City", "New")),
                __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=unverified action=fill_ui_fields "), result)
        self.assertEqual(control.iface_value.CurrentValue, "New")
        self.assertIn("inspect it before retrying", result)

    def test_already_correct_value_is_not_written_again(self):
        control = _Control("City", "Szczecin")
        result = self._with_desktop(
            [control],
            lambda _window: ui_form._fill_fields_on_window(
                77, 4242, self._fields(("City", "Szczecin")),
                __import__("threading").Event(),
            ),
        )
        self.assertTrue(result.startswith("status=done action=fill_ui_fields "), result)
        self.assertIn("fields_filled=0", result)
        self.assertIn("fields_already_correct=1", result)

    def test_high_impact_form_context_requires_hud_confirmation_without_values(self):
        fields = self._fields(("Amount", "123.45"))
        snapshot = {
            "handle": 77, "process_id": 4242,
            "context": [{"role": "Text", "name": "Payment details"}],
            "fields": [],
        }
        with (
            patch.object(ui_form, "_form_snapshot", return_value=snapshot),
            patch.object(ui_form, "_run_fill_with_timeout") as fill,
            patch("core.confirm.pending_title", return_value=""),
            patch("core.confirm.request", return_value="[CONFIRMATION_PENDING]") as request,
        ):
            result = ui_form.fill_ui_fields({"fields": fields})
        self.assertEqual(result, "[CONFIRMATION_PENDING]")
        request.assert_called_once()
        self.assertNotIn("123.45", request.call_args.kwargs["detail"])
        fill.assert_not_called()

    def test_routine_form_entry_uses_local_worker_without_confirmation(self):
        fields = self._fields(("City", "Szczecin"))
        snapshot = {
            "handle": 77, "process_id": 4242,
            "context": [{"role": "Text", "name": "Profile"}],
            "fields": [],
        }
        with (
            patch.object(ui_form, "_form_snapshot", return_value=snapshot),
            patch.object(
            ui_form, "_run_fill_with_timeout",
            return_value="status=done action=fill_ui_fields fields_filled=1",
            ) as fill,
            patch("core.confirm.request") as request,
        ):
            result = ui_form.fill_ui_fields({"fields": fields})
        self.assertIn("status=done", result)
        fill.assert_called_once_with(77, 4242, fields)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
