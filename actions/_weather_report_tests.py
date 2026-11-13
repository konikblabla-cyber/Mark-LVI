"""Offline tests for grounded weather reporting."""
import unittest
from unittest.mock import Mock, patch

from actions.weather_report import weather_action


class WeatherReportTests(unittest.TestCase):
    def test_missing_city_does_not_query_or_open_a_browser(self):
        with patch("actions.web_search._gemini_search") as search:
            result = weather_action({})
        self.assertIn("need a city", result)
        search.assert_not_called()

    def test_returns_grounded_report_and_saves_it(self):
        player = Mock()
        memory = Mock()
        with patch("actions.web_search._gemini_search", return_value="12°C, light rain, 30% chance of precipitation.") as search:
            result = weather_action({"city": "Brzesko", "time": "tomorrow"}, player, memory)
        self.assertIn("Weather report for Brzesko (tomorrow)", result)
        self.assertIn("12°C", result)
        search.assert_called_once()
        prompt = search.call_args.args[0]
        self.assertIn("Brzesko", prompt)
        self.assertIn("tomorrow", prompt)
        self.assertIn("Celsius", prompt)
        player.show_content.assert_called_once()
        memory.set_last_search.assert_called_once()

    def test_search_snippets_are_labelled_as_unverified_fallback(self):
        with patch("actions.web_search._gemini_search", side_effect=RuntimeError("quota")), patch(
            "actions.web_search._ddg_search", return_value=[{
                "title": "Weather",
                "snippet": "Forecast page",
                "url": "https://example.com/weather",
            }]
        ):
            result = weather_action({"city": "Brzesko"})
        self.assertIn("could not verify a complete live forecast", result)
        self.assertIn("not as a confirmed weather reading", result)
        self.assertIn("https://example.com/weather", result)

    def test_no_results_never_claims_a_forecast(self):
        with patch("actions.web_search._gemini_search", side_effect=RuntimeError("quota")), patch(
            "actions.web_search._ddg_search", return_value=[]
        ):
            result = weather_action({"city": "Brzesko"})
        self.assertIn("could not retrieve a reliable weather report", result.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
