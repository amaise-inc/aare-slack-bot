"""Unit tests for the pure functions in aare_slack.

Run with: python -m unittest -v
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from aare_slack import (
    AARE_GURU_URL,
    BAFU_URL,
    DEFAULT_CITIES,
    build_api_url,
    build_bar,
    build_datetime_str,
    build_payload,
    classify,
    forecast_trend,
    parse_cities,
)

# A fixed point in time (Wed 27 May 2026, 11:30 Europe/Zurich) for deterministic
# date-line assertions.
FIXED_NOW = datetime(2026, 5, 27, 11, 30, tzinfo=ZoneInfo("Europe/Zurich"))


def _section_text(payload: dict) -> str:
    blocks = payload["attachments"][0]["blocks"]
    for block in blocks:
        if block.get("block_id") == "main":
            return block["text"]["text"]
    raise AssertionError(f"no main block in {blocks!r}")


def _block_by_id(payload: dict, block_id: str) -> dict | None:
    for block in payload["attachments"][0]["blocks"]:
        if block.get("block_id") == block_id:
            return block
    return None


class TestClassify(unittest.TestCase):
    def test_below_freezing_is_ice(self) -> None:
        emoji, _, header, tagline = classify(2.0)
        self.assertEqual(emoji, "🧊")
        self.assertIsNone(header)
        self.assertTrue(tagline)

    def test_cold_has_no_header(self) -> None:
        for temp in (12.0, 14.9, 15.0, 17.99):
            with self.subTest(temp=temp):
                _, _, header, _ = classify(temp)
                self.assertIsNone(header, f"unexpected header at {temp}°C")

    def test_swim_threshold_18_gets_header(self) -> None:
        emoji, color, header, _ = classify(18.0)
        self.assertEqual(emoji, "🏊")
        self.assertEqual(color, "#2eb886")
        self.assertEqual(header, "TIME TO SWIM!")

    def test_warm_tier_20(self) -> None:
        _, _, header, _ = classify(20.5)
        self.assertEqual(header, "PERFECT FOR A SWIM")

    def test_hot_tier_22(self) -> None:
        emoji, color, header, _ = classify(23.0)
        self.assertEqual(emoji, "🔥")
        self.assertEqual(color, "#d0021b")
        self.assertEqual(header, "BATHTUB MODE")

    def test_every_tier_has_a_tagline(self) -> None:
        for temp in (2.0, 13.0, 16.0, 18.5, 20.5, 23.0):
            with self.subTest(temp=temp):
                _, _, _, tagline = classify(temp)
                self.assertTrue(tagline, f"missing tagline at {temp}°C")
                self.assertGreater(len(tagline), 5)


class TestBuildBar(unittest.TestCase):
    def test_at_minimum_is_empty(self) -> None:
        self.assertEqual(build_bar(10.0), "░" * 16)

    def test_at_maximum_is_full(self) -> None:
        self.assertEqual(build_bar(26.0), "█" * 16)

    def test_below_minimum_is_clamped(self) -> None:
        self.assertEqual(build_bar(0.0), "░" * 16)

    def test_above_maximum_is_clamped(self) -> None:
        self.assertEqual(build_bar(100.0), "█" * 16)

    def test_midpoint(self) -> None:
        bar = build_bar(18.0)
        self.assertEqual(bar.count("█"), 8)
        self.assertEqual(bar.count("░"), 8)
        self.assertEqual(len(bar), 16)

    def test_invalid_scale_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_bar(15.0, scale_min=20, scale_max=20)


class TestForecastTrend(unittest.TestCase):
    def test_warming(self) -> None:
        self.assertEqual(forecast_trend(18.0, 18.5), "↗")

    def test_cooling(self) -> None:
        self.assertEqual(forecast_trend(18.0, 17.5), "↘")

    def test_steady(self) -> None:
        self.assertEqual(forecast_trend(18.0, 18.0), "→")
        self.assertEqual(forecast_trend(18.0, 18.1), "→")
        self.assertEqual(forecast_trend(18.0, 17.9), "→")

    def test_threshold_boundary(self) -> None:
        self.assertEqual(forecast_trend(18.0, 18.3), "↗")
        self.assertEqual(forecast_trend(18.0, 17.7), "↘")


class TestBuildDatetimeStr(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(build_datetime_str(FIXED_NOW), "Wed 27 May · 11:30")

    def test_winter_afternoon(self) -> None:
        winter = datetime(2026, 1, 5, 15, 0, tzinfo=ZoneInfo("Europe/Zurich"))
        self.assertEqual(build_datetime_str(winter), "Mon 05 Jan · 15:00")


class TestParseCities(unittest.TestCase):
    def test_empty_uses_default(self) -> None:
        self.assertEqual(parse_cities(None), list(DEFAULT_CITIES))
        self.assertEqual(parse_cities(""), list(DEFAULT_CITIES))
        self.assertEqual(parse_cities("   "), list(DEFAULT_CITIES))

    def test_single_city(self) -> None:
        self.assertEqual(parse_cities("bern"), ["bern"])

    def test_comma_separated(self) -> None:
        self.assertEqual(parse_cities("bern,thun,interlaken"), ["bern", "thun", "interlaken"])

    def test_strips_whitespace_and_lowercases(self) -> None:
        self.assertEqual(parse_cities(" BERN ,  Thun "), ["bern", "thun"])

    def test_skips_empty_entries(self) -> None:
        self.assertEqual(parse_cities("bern,,thun,"), ["bern", "thun"])


class TestBuildApiUrl(unittest.TestCase):
    def test_bern(self) -> None:
        self.assertEqual(
            build_api_url("bern"),
            "https://aareguru.existenz.ch/v2018/current?city=bern&app=aare-slack-bot&version=1",
        )

    def test_arbitrary_city(self) -> None:
        self.assertIn("city=thun", build_api_url("thun"))
        self.assertIn("app=aare-slack-bot", build_api_url("thun"))


def _warm_data() -> dict:
    return {
        "aare": {"temperature": 19.2, "flow": 142, "forecast2h": 19.8},
        "weather": {"current": {"tt": 24}},
    }


def _cold_data() -> dict:
    return {
        "aare": {"temperature": 8.5, "flow": 200, "forecast2h": 8.3},
        "weather": {"current": {"tt": 5}},
    }


class TestBuildPayload(unittest.TestCase):
    def test_cold_payload_has_no_header_block(self) -> None:
        payload = build_payload(_cold_data(), now=FIXED_NOW)
        self.assertIsNone(_block_by_id(payload, "header"))
        self.assertIsNotNone(_block_by_id(payload, "main"))

    def test_warm_payload_has_header_block(self) -> None:
        payload = build_payload(_warm_data(), now=FIXED_NOW)
        header = _block_by_id(payload, "header")
        self.assertIsNotNone(header)
        self.assertIn("TIME TO SWIM!", header["text"]["text"])

    def test_attachment_color_set(self) -> None:
        payload = build_payload(_warm_data(), now=FIXED_NOW)
        self.assertEqual(payload["attachments"][0]["color"], "#2eb886")

    def test_forecast_arrow_warming(self) -> None:
        text = _section_text(build_payload(_warm_data(), now=FIXED_NOW))
        self.assertIn("↗", text)
        self.assertIn("2h: 19.8°C", text)

    def test_forecast_arrow_cooling(self) -> None:
        data = {
            "aare": {"temperature": 19.0, "flow": 140, "forecast2h": 18.5},
            "weather": {"current": {"tt": 22}},
        }
        self.assertIn("↘", _section_text(build_payload(data, now=FIXED_NOW)))

    def test_missing_forecast_omits_2h_line(self) -> None:
        text = _section_text(build_payload({"aare": {"temperature": 17.0}}, now=FIXED_NOW))
        self.assertNotIn("2h:", text)

    def test_missing_weather_block_is_graceful(self) -> None:
        text = _section_text(
            build_payload({"aare": {"temperature": 17.0, "flow": 150}}, now=FIXED_NOW)
        )
        self.assertIn("17.0°C", text)
        self.assertNotIn("Air", text)
        self.assertIn("Flow 150", text)

    def test_missing_flow_omits_flow_string(self) -> None:
        data = {
            "aare": {"temperature": 17.0},
            "weather": {"current": {"tt": 20}},
        }
        text = _section_text(build_payload(data, now=FIXED_NOW))
        self.assertIn("Air 20°C", text)
        self.assertNotIn("Flow", text)

    def test_english_labels_only(self) -> None:
        text = _section_text(build_payload(_warm_data(), now=FIXED_NOW))
        self.assertIn("Air 24°C", text)
        self.assertIn("Flow 142 m³/s", text)
        for forbidden in ("Bärn", "äuä", "Plütterwarm", "wermer"):
            self.assertNotIn(forbidden, text)

    def test_tagline_present_in_message(self) -> None:
        self.assertIn(
            "Officially swim-worthy",
            _section_text(build_payload(_warm_data(), now=FIXED_NOW)),
        )

    def test_attribution_links_present(self) -> None:
        payload = build_payload(_warm_data(), now=FIXED_NOW)
        attribution = _block_by_id(payload, "attribution")
        self.assertIsNotNone(attribution)
        text = attribution["elements"][0]["text"]
        self.assertIn(AARE_GURU_URL, text)
        self.assertIn(BAFU_URL, text)
        self.assertIn("aare.guru", text)
        self.assertIn("BAFU", text)

    def test_attribution_present_on_cold_payload_too(self) -> None:
        payload = build_payload(_cold_data(), now=FIXED_NOW)
        self.assertIsNotNone(_block_by_id(payload, "attribution"))

    def test_date_block_present(self) -> None:
        payload = build_payload(_warm_data(), now=FIXED_NOW)
        date_block = _block_by_id(payload, "date")
        self.assertIsNotNone(date_block)
        self.assertIn("Wed 27 May", date_block["elements"][0]["text"])
        self.assertIn("11:30", date_block["elements"][0]["text"])

    def test_default_city_label_is_bern(self) -> None:
        text = _section_text(build_payload(_warm_data(), now=FIXED_NOW))
        self.assertIn("Aare Bern", text)

    def test_custom_city_label(self) -> None:
        text = _section_text(
            build_payload(_warm_data(), city_label="Thun", now=FIXED_NOW)
        )
        self.assertIn("Aare Thun", text)


if __name__ == "__main__":
    unittest.main()
