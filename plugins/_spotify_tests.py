"""Offline tests for the Spotify integration."""
import base64
import hashlib
import unittest
from unittest import mock

from plugins import spotify


class SpotifyPluginTests(unittest.TestCase):
    def test_plugin_contract_is_accepted_by_auto_loader(self):
        from core.plugin_loader import _validate
        record = _validate(spotify, "spotify.py")
        self.assertTrue(record.valid, record.error)
        self.assertEqual(record.name, "spotify")
        self.assertEqual(record.settings["namespace"], "spotify")

    def test_token_is_stored_outside_the_repository(self):
        token = spotify._token_path().resolve()
        with self.assertRaises(ValueError):
            token.relative_to(spotify._root().resolve())

    def test_pkce_uses_s256_challenge(self):
        verifier, challenge = spotify._pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertGreaterEqual(len(verifier), 43)
        self.assertEqual(challenge, expected)

    def test_oauth_scopes_are_limited_to_spotify_features(self):
        self.assertIn("user-read-playback-state", spotify._SCOPES)
        self.assertIn("user-modify-playback-state", spotify._SCOPES)
        self.assertIn("playlist-read-private", spotify._SCOPES)
        self.assertIn("playlist-modify-private", spotify._SCOPES)
        self.assertNotIn("user-read-email", spotify._SCOPES)
        self.assertNotIn("user-library-modify", spotify._SCOPES)

    def test_spotify_uri_validation_is_strict(self):
        self.assertEqual(
            spotify._playlist_uri("spotify:playlist:1234567890123456789012"),
            "spotify:playlist:1234567890123456789012",
        )
        self.assertEqual(
            spotify._playlist_uri("https://open.spotify.com/playlist/1234567890123456789012?si=abc"),
            "spotify:playlist:1234567890123456789012",
        )
        with self.assertRaises(ValueError):
            spotify._playlist_uri("spotify:track:1234567890123456789012")
        with self.assertRaises(ValueError):
            spotify._playlist_uri("https://evil.example/playlist/1234567890123456789012")

    def test_track_query_parser_is_bounded(self):
        with mock.patch.object(spotify, "_search", side_effect=[
            [{"uri": "spotify:track:1234567890123456789012", "name": "Song", "artists": [{"name": "Artist"}]}],
            [{"uri": "spotify:track:abcdefghijklmnopqrstuv", "name": "Another Song", "artists": [{"name": "Artist"}]}],
        ]):
            self.assertEqual(
                spotify._search_track_uris("Artist - Song\nArtist - Another Song"),
                (
                    ["spotify:track:1234567890123456789012", "spotify:track:abcdefghijklmnopqrstuv"],
                    ["Song — Artist", "Another Song — Artist"],
                ),
            )
        with mock.patch.object(spotify, "_search", return_value=[{"uri": "spotify:track:1234567890123456789012", "name": "Song"}]):
            with self.assertRaises(ValueError):
                spotify._search_track_uris("\n".join(f"Song {n}" for n in range(26)))

    def test_spotify_catalog_text_is_shown_locally_not_returned_to_model(self):
        player = mock.Mock()
        item = {
            "name": "Private Test Track",
            "uri": "spotify:track:1234567890123456789012",
            "artists": [{"name": "Private Test Artist"}],
        }
        with mock.patch.object(spotify, "_search", return_value=[item]), \
             mock.patch.object(spotify, "_show", return_value=True) as show:
            result = spotify.run({"action": "search", "query": "Private Test Track"}, player=player)
        self.assertEqual(result, "Spotify search results are displayed in the MARK HUD.")
        self.assertNotIn("Private Test Track", result)
        self.assertNotIn("Private Test Artist", result)
        show.assert_called_once()
        self.assertIn("Private Test Track", show.call_args.args[2])

    def test_play_uses_top_match_without_returning_catalog_metadata(self):
        item = {
            "name": "Test Track",
            "uri": "spotify:track:1234567890123456789012",
            "artists": [{"name": "Test Artist"}],
        }
        with mock.patch.object(spotify, "_search", return_value=[item]), \
             mock.patch.object(spotify, "_play_uri") as play, \
             mock.patch.object(spotify, "_show") as show:
            result = spotify.run({"action": "play", "query": "Test Artist Test Track"})
        self.assertEqual(result, "Started the top Spotify match for your request.")
        self.assertNotIn("Test Track", result)
        play.assert_called_once_with(item["uri"], None)
        show.assert_called_once()

    def test_playlist_list_keeps_catalog_names_out_of_tool_response(self):
        player = mock.Mock()
        data = {"items": [{
            "name": "Private Playlist Title",
            "id": "1234567890123456789012",
            "owner": {"display_name": "Private Owner"},
            "items": {"total": 7},
        }]}
        with mock.patch.object(spotify, "_api", return_value=data), \
             mock.patch.object(spotify, "_show") as show:
            result = spotify._playlist_list(player)
        self.assertEqual(result, "Your Spotify playlists are displayed in the MARK HUD.")
        self.assertNotIn("Private Playlist Title", result)
        show.assert_called_once()
        self.assertIn("Private Playlist Title", show.call_args.args[2])

    def test_playlist_creation_waits_for_hud_confirmation(self):
        matched = (
            "My Mix",
            "Description",
            ["spotify:track:1234567890123456789012"],
            ["Track — Artist"],
            False,
        )
        with mock.patch.object(spotify, "_prepare_playlist", return_value=matched), \
             mock.patch.object(spotify, "_show") as show, \
             mock.patch.object(spotify.confirm, "request", return_value="[CONFIRMATION_PENDING]") as gate, \
             mock.patch.object(spotify, "_create_playlist") as create:
            result = spotify.run({
                "action": "playlist_create",
                "name": "My Mix",
                "track_queries": "Artist - Track",
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            create.assert_not_called()
            show.assert_called_once()
            self.assertIn("Track — Artist", gate.call_args.kwargs["detail"])
            gate.call_args.kwargs["run"]()
            create.assert_called_once_with(
                "My Mix", "Description",
                ["spotify:track:1234567890123456789012"], False,
            )

    def test_playlist_add_waits_for_hud_confirmation(self):
        matched = (
            "playlist-id",
            "My Existing Playlist",
            ["spotify:track:1234567890123456789012"],
            ["Track — Artist"],
        )
        with mock.patch.object(spotify, "_prepare_playlist", return_value=matched), \
             mock.patch.object(spotify, "_show"), \
             mock.patch.object(spotify.confirm, "request", return_value="[CONFIRMATION_PENDING]") as gate, \
             mock.patch.object(spotify, "_add_to_playlist") as add:
            result = spotify.run({
                "action": "playlist_add",
                "playlist_name": "My Existing Playlist",
                "track_queries": "Artist - Track",
            })
            self.assertEqual(result, "[CONFIRMATION_PENDING]")
            add.assert_not_called()
            gate.call_args.kwargs["run"]()
            add.assert_called_once_with(
                "playlist-id", ["spotify:track:1234567890123456789012"]
            )

    def test_volume_is_bounded_and_never_accepts_booleans(self):
        with mock.patch.object(spotify, "_api") as api:
            self.assertIn("whole number", spotify.run({"action": "volume", "volume": True}))
            self.assertIn("0 to 100", spotify.run({"action": "volume", "volume": 101}))
            api.assert_not_called()

    def test_403_explains_development_mode_restrictions(self):
        response = mock.Mock(status_code=403, headers={}, content=b"", json=mock.Mock(return_value={"error": {"message": "forbidden"}}))
        with mock.patch.object(spotify, "_access_token", return_value="token"), \
             mock.patch.object(spotify.requests, "request", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Premium"):
                spotify._api("GET", "/me/player/devices")

    def test_unconfigured_status_is_actionable(self):
        with mock.patch.object(spotify, "_token_path") as path:
            path.return_value.is_file.return_value = False
            result = spotify.run({"action": "status"})
        self.assertIn("Client ID", result)
        self.assertIn("connect Spotify", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
