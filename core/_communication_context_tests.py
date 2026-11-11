"""Offline tests for communication-app microphone privacy gating."""
import sys
import types
import unittest
from unittest.mock import Mock, patch

from core.communication_context import (
    communication_apps_from_sessions,
    get_active_communication_apps,
    is_communication_app,
    is_communication_context,
    normalize_process_name,
    should_auto_wake,
    communication_gate_state,
    should_forward_microphone,
    should_stream_call_audio,
)


class CommunicationContextTests(unittest.TestCase):
    def test_windows_starts_with_microphone_gate_closed(self):
        from core.communication_context import initial_communication_gate
        self.assertTrue(initial_communication_gate("win32"))
        self.assertFalse(initial_communication_gate("linux"))
        self.assertFalse(initial_communication_gate("darwin"))

    def test_first_successful_probe_is_never_skipped(self):
        from core.communication_context import communication_context_unchanged
        self.assertFalse(communication_context_unchanged(True, True, False))
        self.assertTrue(communication_context_unchanged(True, True, True))
        self.assertFalse(communication_context_unchanged(False, True, True))

    def test_process_names_are_normalized(self):
        self.assertEqual(
            normalize_process_name(r"C:\\Program Files\\Discord\\Discord.exe"),
            "discord",
        )
        self.assertEqual(normalize_process_name("MS-TEAMS.EXE"), "ms-teams")

    def test_known_chat_and_call_apps_are_detected(self):
        for name in (
            "Discord.exe", "DiscordPTB.exe", "Teams.exe", "ms-teams.exe",
            "Zoom.exe", "PhoneExperienceHost.exe", "WhatsApp.exe",
            "Telegram.exe", "Webex.exe",
        ):
            with self.subTest(name=name):
                self.assertTrue(is_communication_app(name))

    def test_unknown_apps_are_not_misclassified(self):
        self.assertFalse(is_communication_app("notepad.exe"))
        self.assertFalse(is_communication_app("chrome.exe"))
        self.assertFalse(is_communication_app(""))

    def test_browser_is_only_a_protected_app_when_microphone_capture_is_active(self):
        from core.communication_context import BROWSER_CAPTURE_APP_NAMES
        class Process:
            def __init__(self, name):
                self._name = name
            def name(self):
                return self._name
        class Session:
            def __init__(self, state, name):
                self.State = state
                self.Process = Process(name)

        browser_audio = [Session(1, "chrome.exe")]
        self.assertEqual(communication_apps_from_sessions(browser_audio), set())
        self.assertEqual(
            communication_apps_from_sessions(
                browser_audio, include_browser_capture=True
            ),
            {"chrome"},
        )
        self.assertIn("opera", BROWSER_CAPTURE_APP_NAMES)
        self.assertFalse(is_communication_app("chrome.exe"))
        self.assertTrue(is_communication_context("game.exe", {"chrome"}))

    def test_only_active_audio_sessions_count_as_background_calls(self):
        class Process:
            def __init__(self, name):
                self._name = name
            def name(self):
                return self._name

        class Session:
            def __init__(self, state, name):
                self.State = state
                self.Process = Process(name)

        sessions = [
            Session(1, "Discord.exe"),
            Session(0, "Teams.exe"),
            Session(1, "spotify.exe"),
        ]
        self.assertEqual(
            communication_apps_from_sessions(sessions),
            {"discord"},
        )

    def test_expired_and_processless_sessions_are_ignored(self):
        class Session:
            State = 2
            Process = None

        self.assertEqual(communication_apps_from_sessions([Session()]), set())

    def test_open_chat_app_alone_does_not_count_as_background_call(self):
        self.assertFalse(is_communication_context("opera.exe", set()))

    def test_focused_chat_app_without_call_audio_does_not_block_commands(self):
        self.assertFalse(is_communication_context("Discord.exe", set()))

    def test_active_background_call_is_private_while_game_is_foreground(self):
        self.assertTrue(is_communication_context("game.exe", {"discord"}))

    def test_unknown_background_audio_does_not_trigger_privacy_gate(self):
        self.assertFalse(is_communication_context("game.exe", {"musicplayer"}))

    def test_wasapi_probe_checks_render_and_capture_sessions(self):
        render_process = types.SimpleNamespace(name=lambda: "Discord.exe")
        render_session = types.SimpleNamespace(State=1, Process=render_process)

        capture_process = types.SimpleNamespace(name=lambda: "Teams.exe")
        browser_capture_process = types.SimpleNamespace(name=lambda: "chrome.exe")
        class CaptureControl:
            State = 1
            Process = capture_process
            def QueryInterface(self, interface):
                self.interface = interface
                return self

        control = CaptureControl()
        class BrowserCaptureControl:
            State = 1
            Process = browser_capture_process
            def QueryInterface(self, interface):
                return self
        browser_control = BrowserCaptureControl()
        class SessionEnumerator:
            def GetCount(self):
                return 2
            def GetSession(self, index):
                self.assert_index = index
                return (control, browser_control)[index]

        manager = types.SimpleNamespace(
            GetSessionEnumerator=lambda: SessionEnumerator()
        )
        capture_device = types.SimpleNamespace(AudioSessionManager=manager)

        class WrappedSession:
            def __init__(self, ctl):
                self.State = ctl.State
                self.Process = ctl.Process

        comtypes = types.SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        audio = types.SimpleNamespace(
            GetAllSessions=Mock(return_value=[render_session]),
            GetAllDevices=Mock(return_value=[capture_device]),
        )
        interface = object()
        api_package = types.ModuleType("pycaw.api")
        api_package.__path__ = []
        policy_module = types.ModuleType("pycaw.api.audiopolicy")
        policy_module.IAudioSessionControl2 = interface
        constants_module = types.ModuleType("pycaw.constants")
        constants_module.EDataFlow = types.SimpleNamespace(
            eCapture=types.SimpleNamespace(value=1)
        )
        constants_module.DEVICE_STATE = types.SimpleNamespace(
            ACTIVE=types.SimpleNamespace(value=1)
        )
        utils_module = types.ModuleType("pycaw.utils")
        utils_module.AudioSession = WrappedSession
        pycaw_package = types.ModuleType("pycaw")
        pycaw_package.__path__ = []
        pycaw_module = types.ModuleType("pycaw.pycaw")
        pycaw_module.AudioUtilities = audio
        with patch.dict(sys.modules, {
            "comtypes": comtypes,
            "pycaw": pycaw_package,
            "pycaw.api": api_package,
            "pycaw.api.audiopolicy": policy_module,
            "pycaw.constants": constants_module,
            "pycaw.pycaw": pycaw_module,
            "pycaw.utils": utils_module,
        }):
            result = get_active_communication_apps()

        self.assertEqual(result, {"discord", "teams", "chrome"})
        audio.GetAllDevices.assert_called_once_with(
            data_flow=1, device_state=1
        )
        self.assertIs(control.interface, interface)
        comtypes.CoInitialize.assert_called_once_with()
        comtypes.CoUninitialize.assert_called_once_with()

    def test_wasapi_probe_uninitializes_com_when_capture_enumeration_fails(self):
        comtypes = types.SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        audio = types.SimpleNamespace(
            GetAllSessions=Mock(return_value=[]),
            GetAllDevices=Mock(side_effect=RuntimeError("WASAPI unavailable")),
        )
        interface = object()
        api_package = types.ModuleType("pycaw.api")
        api_package.__path__ = []
        policy_module = types.ModuleType("pycaw.api.audiopolicy")
        policy_module.IAudioSessionControl2 = interface
        constants_module = types.ModuleType("pycaw.constants")
        constants_module.EDataFlow = types.SimpleNamespace(
            eCapture=types.SimpleNamespace(value=1)
        )
        constants_module.DEVICE_STATE = types.SimpleNamespace(
            ACTIVE=types.SimpleNamespace(value=1)
        )
        utils_module = types.ModuleType("pycaw.utils")
        utils_module.AudioSession = object
        pycaw_package = types.ModuleType("pycaw")
        pycaw_package.__path__ = []
        pycaw_module = types.ModuleType("pycaw.pycaw")
        pycaw_module.AudioUtilities = audio
        with patch.dict(sys.modules, {
            "comtypes": comtypes,
            "pycaw": pycaw_package,
            "pycaw.api": api_package,
            "pycaw.api.audiopolicy": policy_module,
            "pycaw.constants": constants_module,
            "pycaw.pycaw": pycaw_module,
            "pycaw.utils": utils_module,
        }):
            result = get_active_communication_apps()
        self.assertIsNone(result)
        comtypes.CoInitialize.assert_called_once_with()
        comtypes.CoUninitialize.assert_called_once_with()

    def test_audio_api_failure_uses_foreground_fallback_on_non_windows(self):
        with patch("core.communication_context.os.name", "posix"):
            self.assertTrue(is_communication_context("Teams.exe", None))
            self.assertFalse(is_communication_context("opera.exe", None))

    def test_windows_audio_api_failure_fails_closed_in_background_apps(self):
        with patch("core.communication_context.os.name", "nt"):
            self.assertTrue(is_communication_context("opera.exe", None))
            self.assertTrue(is_communication_context("game.exe", None))

    def test_active_call_detection_does_not_need_chat_to_be_foreground(self):
        self.assertTrue(is_communication_context("opera.exe", {"teams"}))

    def test_communication_gate_closes_immediately(self):
        self.assertEqual(
            communication_gate_state(True, None, 10.0),
            (True, 10.0),
        )

    def test_brief_wasapi_session_drop_does_not_open_the_microphone(self):
        last_active = 10.0
        self.assertEqual(
            communication_gate_state(False, last_active, 11.0),
            (True, last_active),
        )
        self.assertEqual(
            communication_gate_state(False, last_active, 12.99),
            (True, last_active),
        )

    def test_gate_reopens_only_after_full_release_grace(self):
        self.assertEqual(
            communication_gate_state(False, 10.0, 13.0),
            (False, 10.0),
        )

    def test_new_call_during_release_grace_restarts_the_grace_period(self):
        gate, last_active = communication_gate_state(False, 10.0, 11.0)
        self.assertTrue(gate)
        self.assertEqual(
            communication_gate_state(True, last_active, 11.5),
            (True, 11.5),
        )
        self.assertEqual(
            communication_gate_state(False, 11.5, 14.0),
            (True, 11.5),
        )

    def test_invalid_monotonic_time_fails_closed(self):
        for invalid_time in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(invalid_time=invalid_time):
                self.assertEqual(
                    communication_gate_state(False, 10.0, invalid_time),
                    (True, 10.0),
                )

    def test_fresh_desktop_input_auto_wakes_outside_calls(self):
        self.assertTrue(should_auto_wake(0.0, False))
        self.assertTrue(should_auto_wake(12.5, False))
        self.assertFalse(should_auto_wake(16.0, False))
        self.assertFalse(should_auto_wake(None, False))

    def test_desktop_activity_never_auto_wakes_inside_a_call(self):
        self.assertFalse(should_auto_wake(0.1, True))

    def test_microphone_is_blocked_in_call_apps_without_explicit_address(self):
        self.assertFalse(should_forward_microphone(True, False, False))

    def test_wake_phrase_opens_one_request(self):
        self.assertTrue(should_forward_microphone(True, True, False))

    def test_push_to_talk_is_an_explicit_override(self):
        self.assertTrue(should_forward_microphone(True, False, True))


    def test_call_audio_semantic_addressing_requires_explicit_consent_and_live_proactivity(self):
        self.assertTrue(should_stream_call_audio(True, True, True, True, context_known=True))
        self.assertFalse(should_stream_call_audio(True, False, True, True, context_known=True))
        self.assertFalse(should_stream_call_audio(True, True, False, True, context_known=True))
        self.assertFalse(should_stream_call_audio(True, True, True, False, context_known=True))
        self.assertFalse(should_stream_call_audio(False, True, True, True, context_known=True))
        self.assertFalse(should_stream_call_audio(True, True, True, True))

    def test_unknown_communication_context_keeps_audio_local(self):
        self.assertFalse(should_stream_call_audio(
            True, True, True, True, context_known=False
        ))

    def test_phone_audio_never_opens_the_pc_communication_audio_gate(self):
        self.assertFalse(should_stream_call_audio(
            True, True, True, True, phone_audio_active=True, context_known=True
        ))

    def test_in_call_consent_configuration_defaults_to_false_and_rejects_malformed_values(self):
        from memory import config_manager
        with patch.object(config_manager, "load_api_keys", return_value={}):
            self.assertFalse(config_manager.get_in_call_addressing_enabled())
        with patch.object(config_manager, "load_api_keys", return_value={"in_call_addressing": "true"}):
            self.assertFalse(config_manager.get_in_call_addressing_enabled())
        with patch.object(config_manager, "load_api_keys", return_value={"in_call_addressing": 1}):
            self.assertFalse(config_manager.get_in_call_addressing_enabled())
        with patch.object(config_manager, "load_api_keys", return_value={"in_call_addressing": True}):
            self.assertTrue(config_manager.get_in_call_addressing_enabled())

    def test_in_call_consent_saves_only_a_boolean_value(self):
        from memory import config_manager
        with patch.object(config_manager, "_save_flag") as save:
            config_manager.save_in_call_addressing_enabled(True)
        save.assert_called_once_with("in_call_addressing", True)
        with patch.object(config_manager, "_save_flag") as save:
            config_manager.save_in_call_addressing_enabled(False)
        save.assert_called_once_with("in_call_addressing", False)

    def test_call_audio_semantic_addressing_is_disabled_by_default(self):
        self.assertFalse(should_stream_call_audio(True, False, True, True))

    def test_normal_desktop_behaviour_is_unchanged(self):
        self.assertTrue(should_forward_microphone(False, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
