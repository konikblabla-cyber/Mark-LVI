"""Behavior-level offline tests for browser safety guards.

Playwright is intentionally not installed in the lightweight CI job. We patch
its discovery check while importing the module and test the real wrapper and
session methods with small async fakes instead of opening a real browser.
"""
import asyncio
import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


with patch("importlib.util.find_spec", return_value=object()):
    browser = importlib.import_module("actions.browser_control")


class FakeLocator:
    def __init__(self, count=1, visible=True, enabled=True, attrs=None):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self.attrs = attrs or {}
        self.clicked = 0
        self.typed = []
        self.cleared = 0

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def is_enabled(self):
        return self._enabled

    async def get_attribute(self, key):
        return self.attrs.get(key)

    async def click(self, **kwargs):
        self.clicked += 1

    async def clear(self):
        self.cleared += 1

    async def type(self, value, **kwargs):
        self.typed.append(value)


class FakePage:
    def __init__(self, locator):
        self._locator = locator

    def locator(self, selector):
        return self._locator

    def get_by_text(self, text, **kwargs):
        return self._locator

    def get_by_role(self, role, **kwargs):
        return self._locator

    def get_by_placeholder(self, text, **kwargs):
        return self._locator

    def get_by_label(self, text, **kwargs):
        return self._locator


class BrowserBehaviorSafetyTests(unittest.TestCase):
    def test_click_refuses_ambiguous_locator(self):
        locator = FakeLocator(count=2)
        session = SimpleNamespace(_get_page=lambda: _async_value(FakePage(locator)))
        result = asyncio.run(browser._BrowserSession.click(session, selector="button.save"))
        self.assertIn("failed", result)
        self.assertEqual(locator.clicked, 0)

    def test_click_uses_exact_unique_visible_enabled_locator(self):
        locator = FakeLocator()
        session = SimpleNamespace(_get_page=lambda: _async_value(FakePage(locator)))
        result = asyncio.run(browser._BrowserSession.click(session, text="Save"))
        self.assertIn("done", result)
        self.assertEqual(locator.clicked, 1)

    def test_type_text_blocks_password_without_reading_its_value(self):
        locator = FakeLocator(attrs={"type": "password", "name": "current-password"})
        session = SimpleNamespace(_get_page=lambda: _async_value(FakePage(locator)))
        result = asyncio.run(
            browser._BrowserSession.type_text(session, selector="#pass", text="secret")
        )
        self.assertIn("sensitive fields", result)
        self.assertEqual(locator.cleared, 0)
        self.assertEqual(locator.typed, [])

    def test_fill_form_skips_sensitive_fields_and_fills_safe_fields(self):
        safe = FakeLocator()
        secret = FakeLocator(attrs={"type": "password"})
        page = FakePage(safe)
        page.locator = lambda selector: secret if "password" in selector else safe
        session = SimpleNamespace(_get_page=lambda: _async_value(page))
        result = asyncio.run(browser._BrowserSession.fill_form(
            session, {"input[name=password]": "secret", "#city": "Szczecin"}
        ))
        self.assertIn("blocked sensitive field", result)
        self.assertEqual(secret.typed, [])
        self.assertEqual(safe.typed, ["Szczecin"])

    def test_high_impact_click_waits_for_hud_callback(self):
        executed = []
        request_args = {}
        def fake_request(**kwargs):
            request_args.update(kwargs)
            return "[CONFIRMATION_PENDING]"
        with patch.object(browser.confirm, "request", side_effect=fake_request), \
             patch.object(browser, "_browser_control_execute",
                          side_effect=lambda *a, **k: executed.append("ran") or "done"):
            result = browser.browser_control(
                {"action": "click", "text": "Send"}
            )
        self.assertIn("[CONFIRMATION_PENDING]", result)
        self.assertEqual(executed, [])
        request_args["run"]()
        self.assertEqual(executed, ["ran"])

    def test_safe_click_does_not_ask_confirmation(self):
        with patch.object(browser.confirm, "request") as request, \
             patch.object(browser, "_browser_control_execute", return_value="done") as execute:
            result = browser.browser_control(
                {"action": "click", "text": "Open settings"}
            )
        self.assertEqual(result, "done")
        execute.assert_called_once()
        request.assert_not_called()


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main(verbosity=2)
