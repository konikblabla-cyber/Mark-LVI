"""Offline integration tests for live opt-in desktop context updates."""
import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main


def _agent():
    agent = main.JarvisLive.__new__(main.JarvisLive)
    agent.session = SimpleNamespace(send_context_update=AsyncMock())
    agent._base_system_instruction = "Base MARK instructions."
    agent._last_system_instruction = agent._base_system_instruction
    agent._focus_context = {"application": "Code.exe"}
    agent._engine = None
    agent._engine_caps = None
    agent._speaking_lock = threading.Lock()
    agent._is_speaking = False
    agent._last_user_speech = time.monotonic() - 10.0
    return agent


class LiveFocusContextIntegrationTests(unittest.TestCase):
    def test_opted_in_stable_foreground_updates_existing_live_session(self):
        agent = _agent()
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_awaited_once()
        instruction = agent.session.send_context_update.await_args.kwargs["system_instruction"]
        self.assertTrue(instruction.startswith(agent._base_system_instruction))
        self.assertIn("latest stable foreground application is code", instruction)
        self.assertEqual(agent._last_system_instruction, instruction)

    def test_opt_out_removes_previous_live_focus_context(self):
        agent = _agent()
        agent._last_system_instruction = (
            agent._base_system_instruction + "\n\n[LIVE DESKTOP CONTEXT]"
        )
        with patch("main.get_desktop_awareness_enabled", return_value=False):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_awaited_once_with(
            system_instruction=agent._base_system_instruction
        )
        self.assertEqual(agent._last_system_instruction, agent._base_system_instruction)

    def test_no_context_update_while_assistant_is_speaking(self):
        agent = _agent()
        agent._is_speaking = True
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_not_awaited()
        self.assertEqual(agent._last_system_instruction, agent._base_system_instruction)

    def test_missing_foreground_context_removes_stale_live_hint(self):
        agent = _agent()
        agent._focus_context = None
        agent._last_system_instruction = (
            agent._base_system_instruction + "\n\n[LIVE DESKTOP CONTEXT]"
        )
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_awaited_once_with(
            system_instruction=agent._base_system_instruction
        )
        self.assertEqual(agent._last_system_instruction, agent._base_system_instruction)

    def test_no_context_update_during_an_active_addressed_turn(self):
        agent = _agent()
        agent._addressed_turn_pending = True
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_not_awaited()

    def test_failed_context_update_is_throttled(self):
        agent = _agent()
        agent.session.send_context_update.side_effect = RuntimeError("temporary disconnect")
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
            retry_deadline = agent._focus_context_retry_at
            asyncio.run(agent._refresh_live_focus_context())
        self.assertGreater(retry_deadline, time.monotonic())
        agent.session.send_context_update.assert_awaited_once()

    def test_scheduler_deduplicates_an_update_already_in_flight(self):
        async def run():
            agent = _agent()
            release = asyncio.Event()

            async def pending_update():
                await release.wait()

            agent._refresh_live_focus_context = pending_update
            agent._schedule_live_focus_context_update()
            first = agent._focus_context_update_task
            agent._schedule_live_focus_context_update()
            self.assertIs(agent._focus_context_update_task, first)
            release.set()
            await first

        asyncio.run(run())

    def test_scheduler_executes_the_context_update_in_the_live_event_loop(self):
        async def run():
            agent = _agent()
            agent._refresh_live_focus_context = AsyncMock()
            agent._schedule_live_focus_context_update()
            task = agent._focus_context_update_task
            self.assertIsNotNone(task)
            await task
            agent._refresh_live_focus_context.assert_awaited_once()

        asyncio.run(run())

    def test_non_gemini_engine_never_receives_live_context_update(self):
        agent = _agent()
        agent._engine = object()
        with patch("main.get_desktop_awareness_enabled", return_value=True):
            asyncio.run(agent._refresh_live_focus_context())
        agent.session.send_context_update.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
