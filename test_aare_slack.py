"""Unit tests for the pure functions in aare_slack.

Run with: python -m unittest -v
"""
import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from aare_slack import (
    AARE_GURU_URL,
    APP_NAME,
    APP_VERSION,
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

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------------------
# Block lookup helpers
# ---------------------------------------------------------------------------

def _find_block(payload: dict, block_id: str) -> dict:
    for block in payload["attachments"][0]["blocks"]:
        if block.get("block_id") == block_id:
            return block
    raise AssertionError(f"no block with id {block_id!r}")


def _block_types(payload: dict) -> list[str]:
    return [b["type"] for b in payload["attachments"][0]["blocks"]]


def _text_of(payload: dict, block_id: str) -> str:
    block = _find_block(payload, block_id)
    if block["type"] in ("header", "section"):
        return block["text"]["text"]
    if block["type"] == "context":
        return block["elements"][0]["text"]
    raise AssertionError(f"unsupported block type {block['type']!r}")


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class TestClassify(unittest.TestCase):
    """Per-degree tiers, with hotter = more colorful."""

    def test_every_degree_returns_well_formed_tuple(self) -> None:
        for t in range(-5, 35):
            with self.subTest(temp=t):
                emoji, color, slogan = classify(float(t))
                self.assertTrue(emoji, f"missing emoji at {t}°C")
                self.assertRegex(color, HEX_COLOR_RE, f"bad color at {t}°C: {color!r}")
                self.assertGreater(len(slogan), 5, f"slogan too short at {t}°C")

    def test_tier_slogans_in_named_range_are_unique(self) -> None:
        # Each whole degree from 12 to 24 inclusive should have a distinct
        # slogan — that's the whole point of "more thresholds".
        slogans = [classify(float(t))[2] for t in range(12, 25)]
        self.assertEqual(
            len(set(slogans)), len(slogans),
            f"duplicate slogans across tiers 12–24: {slogans}",
        )

    def test_sub_freezing_returns_ice(self) -> None:
        emoji, _, slogan = classify(-2.0)
        self.assertEqual(emoji, "🧊")
        self.assertIn("coffee", slogan.lower())

    def test_boundary_swim_at_18(self) -> None:
        self.assertNotIn("SWIM-WORTHY", classify(17.99)[2])
        self.assertIn("SWIM-WORTHY", classify(18.0)[2])

    def test_boundary_at_19_is_distinct_from_18(self) -> None:
        self.assertNotEqual(classify(18.5)[2], classify(19.0)[2])

    def test_boundary_perfect_at_20(self) -> None:
        self.assertNotIn("PERFECT", classify(19.99)[2])
        self.assertIn("PERFECT", classify(20.0)[2])

    def test_boundary_bathtub_at_22(self) -> None:
        self.assertNotIn("BATHTUB", classify(21.99)[2])
        self.assertIn("BATHTUB", classify(22.0)[2])

    def test_off_the_charts_at_24(self) -> None:
        self.assertIn("OFF THE CHARTS", classify(24.0)[2])

    def test_top_tier_has_more_emoji_than_swim_tier(self) -> None:
        # "the hotter the better, more colorful" — by 24 °C ("off the charts")
        # we should have visibly more emoji than at the entry-level swim tier.
        def emoji_count(s: str) -> int:
            return sum(1 for ch in s if ord(ch) > 0x2600)

        self.assertGreater(emoji_count(classify(24.0)[2]), emoji_count(classify(18.0)[2]))

    def test_all_tier_colors_are_valid_hex(self) -> None:
        for t in range(-5, 35):
            _, color, _ = classify(float(t))
            self.assertRegex(color, HEX_COLOR_RE)


# ---------------------------------------------------------------------------
# build_bar
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# forecast_trend
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# build_datetime_str
# ---------------------------------------------------------------------------

class TestBuildDatetimeStr(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(build_datetime_str(FIXED_NOW), "Wed 27 May · 11:30")

    def test_winter_afternoon(self) -> None:
        winter = datetime(2026, 1, 5, 15, 0, tzinfo=ZoneInfo("Europe/Zurich"))
        self.assertEqual(build_datetime_str(winter), "Mon 05 Jan · 15:00")


# ---------------------------------------------------------------------------
# parse_cities
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# build_api_url (now configurable)
# ---------------------------------------------------------------------------

class TestBuildApiUrl(unittest.TestCase):
    def test_defaults_use_module_constants(self) -> None:
        url = build_api_url("bern")
        self.assertIn("city=bern", url)
        self.assertIn(f"app={APP_NAME}", url)
        self.assertIn(f"version={APP_VERSION}", url)
        self.assertTrue(url.startswith("https://aareguru.existenz.ch/v2018/current?"))

    def test_arbitrary_city(self) -> None:
        self.assertIn("city=thun", build_api_url("thun"))

    def test_custom_app_and_version(self) -> None:
        # aare.guru's terms: "Bei Requests auf die API bitte frei wählbare app
        # und version mitgeben" — make sure we actually pass them through.
        url = build_api_url("bern", app="my.app.example", version="1.42")
        self.assertIn("app=my.app.example", url)
        self.assertIn("version=1.42", url)
        self.assertNotIn(f"app={APP_NAME}", url)


# ---------------------------------------------------------------------------
# build_payload — block layout & content
# ---------------------------------------------------------------------------

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


class TestBuildPayloadStructure(unittest.TestCase):
    """The block order is the visual hierarchy — guard it carefully."""

    def test_block_order_is_headline_slogan_divider_details_attribution(self) -> None:
        payload = build_payload(_warm_data(), now=FIXED_NOW)
        blocks = payload["attachments"][0]["blocks"]
        ids = [b.get("block_id") for b in blocks]
        types = [b["type"] for b in blocks]
        self.assertEqual(
            ids,
            ["headline", "slogan", None, "details", "attribution"],
        )
        self.assertEqual(
            types,
            ["header", "section", "divider", "section", "context"],
        )

    def test_block_order_is_same_when_cold(self) -> None:
        # The hierarchy doesn't change with temperature — only the colour and
        # slogan text do. (Old design used to suppress a banner for cold; new
        # design keeps the same blocks so the message structure is consistent.)
        payload = build_payload(_cold_data(), now=FIXED_NOW)
        ids = [b.get("block_id") for b in payload["attachments"][0]["blocks"]]
        self.assertEqual(ids, ["headline", "slogan", None, "details", "attribution"])

    def test_attachment_color_is_tier_color(self) -> None:
        _, expected_color, _ = classify(_warm_data()["aare"]["temperature"])
        self.assertEqual(
            build_payload(_warm_data(), now=FIXED_NOW)["attachments"][0]["color"],
            expected_color,
        )


class TestBuildPayloadHeadline(unittest.TestCase):
    def test_headline_contains_temperature_date_and_city(self) -> None:
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "headline")
        self.assertIn("19.2°C", text)
        self.assertIn("Aare Bern", text)
        self.assertIn("Wed 27 May", text)
        self.assertIn("11:30", text)

    def test_headline_uses_custom_city_label(self) -> None:
        text = _text_of(
            build_payload(_warm_data(), city_label="Thun", now=FIXED_NOW),
            "headline",
        )
        self.assertIn("Aare Thun", text)
        self.assertNotIn("Aare Bern", text)


class TestBuildPayloadSlogan(unittest.TestCase):
    def test_slogan_is_bold_mrkdwn(self) -> None:
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "slogan")
        self.assertTrue(text.startswith("*") and text.endswith("*"), text)

    def test_slogan_matches_classify(self) -> None:
        _, _, expected = classify(_warm_data()["aare"]["temperature"])
        self.assertIn(expected, _text_of(build_payload(_warm_data(), now=FIXED_NOW), "slogan"))


class TestBuildPayloadDetails(unittest.TestCase):
    def test_bar_and_extras_present(self) -> None:
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "details")
        self.assertIn("10°—26°", text)
        self.assertIn("Air 24°C", text)
        self.assertIn("Flow 142 m³/s", text)

    def test_forecast_arrow_warming(self) -> None:
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "details")
        self.assertIn("↗", text)
        self.assertIn("19.8°C", text)
        self.assertIn("2h forecast", text)

    def test_forecast_arrow_cooling(self) -> None:
        data = {
            "aare": {"temperature": 19.0, "flow": 140, "forecast2h": 18.5},
            "weather": {"current": {"tt": 22}},
        }
        self.assertIn("↘", _text_of(build_payload(data, now=FIXED_NOW), "details"))

    def test_missing_forecast_omits_2h(self) -> None:
        text = _text_of(
            build_payload({"aare": {"temperature": 17.0}}, now=FIXED_NOW),
            "details",
        )
        self.assertNotIn("2h:", text)

    def test_missing_weather_block_is_graceful(self) -> None:
        text = _text_of(
            build_payload({"aare": {"temperature": 17.0, "flow": 150}}, now=FIXED_NOW),
            "details",
        )
        self.assertNotIn("Air", text)
        self.assertIn("Flow 150", text)

    def test_missing_flow_omits_flow(self) -> None:
        data = {"aare": {"temperature": 17.0}, "weather": {"current": {"tt": 20}}}
        text = _text_of(build_payload(data, now=FIXED_NOW), "details")
        self.assertIn("Air 20°C", text)
        self.assertNotIn("Flow", text)

    def test_no_bernese_german_leaks(self) -> None:
        # The upstream API returns Bernese German for some fields — none of
        # them should reach the user-visible message.
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "details")
        for forbidden in ("Bärn", "äuä", "Plütterwarm", "wermer"):
            self.assertNotIn(forbidden, text)


class TestBuildPayloadAttribution(unittest.TestCase):
    """aare.guru's terms require linking back to aare.guru and BAFU."""

    def test_links_present_warm(self) -> None:
        text = _text_of(build_payload(_warm_data(), now=FIXED_NOW), "attribution")
        self.assertIn(AARE_GURU_URL, text)
        self.assertIn(BAFU_URL, text)
        self.assertIn("aare.guru", text)
        self.assertIn("BAFU", text)

    def test_links_present_cold(self) -> None:
        text = _text_of(build_payload(_cold_data(), now=FIXED_NOW), "attribution")
        self.assertIn(AARE_GURU_URL, text)
        self.assertIn(BAFU_URL, text)


if __name__ == "__main__":
    unittest.main()
