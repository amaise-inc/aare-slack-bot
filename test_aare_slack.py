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
    SLOGAN_MATRIX,
    WEATHER_EMOJI,
    WEATHER_LABEL,
    WEATHER_TIERS,
    build_api_url,
    build_bar,
    build_datetime_str,
    build_payload,
    classify,
    classify_water,
    classify_weather,
    forecast_trend,
    parse_cities,
)

# A fixed point in time (Wed 27 May 2026, 11:30 Europe/Zurich) for deterministic
# date-line assertions.
FIXED_NOW = datetime(2026, 5, 27, 11, 30, tzinfo=ZoneInfo("Europe/Zurich"))

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Hours at which the bot fires in real life — used in the period-selection
# tests so we catch a regression that would route the wrong forecast bucket to
# the actual posts.
POST_HOURS = (11, 16)


# ---------------------------------------------------------------------------
# Block lookup helpers
# ---------------------------------------------------------------------------

def _find_block(payload: dict, block_id: str) -> dict:
    for block in payload["attachments"][0]["blocks"]:
        if block.get("block_id") == block_id:
            return block
    raise AssertionError(f"no block with id {block_id!r}")


def _text_of(payload: dict, block_id: str) -> str:
    block = _find_block(payload, block_id)
    if block["type"] in ("header", "section"):
        return block["text"]["text"]
    if block["type"] == "context":
        return block["elements"][0]["text"]
    raise AssertionError(f"unsupported block type {block['type']!r}")


# ---------------------------------------------------------------------------
# classify_water (per-degree water tiers)
# ---------------------------------------------------------------------------

class TestClassifyWater(unittest.TestCase):
    """Per-degree tiers, hotter colors get more vibrant."""

    def test_every_degree_returns_well_formed_tuple(self) -> None:
        for t in range(-5, 35):
            with self.subTest(temp=t):
                emoji, color, tier = classify_water(float(t))
                self.assertTrue(emoji, f"missing emoji at {t}°C")
                self.assertRegex(color, HEX_COLOR_RE, f"bad color at {t}°C: {color!r}")
                self.assertIn(tier, {"frigid", "cold", "cool", "swim", "perfect", "hot"})

    def test_named_tier_boundaries(self) -> None:
        cases = [
            (-2.0, "frigid"),
            (11.99, "frigid"),
            (12.0, "cold"),
            (14.99, "cold"),
            (15.0, "cool"),
            (17.99, "cool"),
            (18.0, "swim"),
            (19.99, "swim"),
            (20.0, "perfect"),
            (21.99, "perfect"),
            (22.0, "hot"),
            (30.0, "hot"),
        ]
        for temp, expected in cases:
            with self.subTest(temp=temp):
                _, _, tier = classify_water(temp)
                self.assertEqual(tier, expected)

    def test_all_colors_are_valid_hex(self) -> None:
        for t in range(-5, 35):
            _, color, _ = classify_water(float(t))
            self.assertRegex(color, HEX_COLOR_RE)


# ---------------------------------------------------------------------------
# classify_weather (sunny / cloudy / rainy / unknown)
# ---------------------------------------------------------------------------

def _weather(period_key: str, **fields) -> dict:
    """Build a minimal API response with a single 'today' period populated."""
    return {
        "aare": {"temperature": 18.0},
        "weather": {
            "current": {"tt": 22, "rr": fields.pop("current_rr", 0)},
            "today": {period_key: fields},
        },
    }


class TestClassifyWeather(unittest.TestCase):
    def test_missing_weather_returns_unknown(self) -> None:
        _, tier = classify_weather({"aare": {"temperature": 18}}, FIXED_NOW)
        self.assertEqual(tier, "unknown")

    def test_empty_weather_returns_unknown(self) -> None:
        _, tier = classify_weather({"aare": {"temperature": 18}, "weather": {}}, FIXED_NOW)
        self.assertEqual(tier, "unknown")

    def test_period_selection_morning(self) -> None:
        # Hour 9 → morning period 'v'.
        now = FIXED_NOW.replace(hour=9, minute=0)
        _, tier = classify_weather(_weather("v", symt=1, rr=0, rrisk=0), now)
        self.assertEqual(tier, "sunny")
        _, tier = classify_weather(_weather("n", symt=1, rr=0, rrisk=0), now)
        self.assertEqual(tier, "unknown", "afternoon period must not be read at 09:00")

    def test_period_selection_afternoon(self) -> None:
        # Hour 14 → afternoon period 'n'.
        now = FIXED_NOW.replace(hour=14, minute=0)
        _, tier = classify_weather(_weather("n", symt=1, rr=0, rrisk=0), now)
        self.assertEqual(tier, "sunny")
        _, tier = classify_weather(_weather("v", symt=1, rr=0, rrisk=0), now)
        self.assertEqual(tier, "unknown", "morning must not be read at 14:00")

    def test_period_selection_evening(self) -> None:
        now = FIXED_NOW.replace(hour=19, minute=0)
        _, tier = classify_weather(_weather("a", symt=1, rr=0, rrisk=0), now)
        self.assertEqual(tier, "sunny")

    def test_real_post_hours_use_the_right_period(self) -> None:
        # The bot only fires at these hours — make sure the period mapping
        # routes them to the right bucket.
        for hour, expected_key in [(11, "v"), (16, "n")]:
            with self.subTest(hour=hour):
                now = FIXED_NOW.replace(hour=hour, minute=0)
                _, tier = classify_weather(
                    _weather(expected_key, symt=1, rr=0, rrisk=0), now
                )
                self.assertEqual(tier, "sunny")

    def test_current_rain_overrides_sunny_forecast(self) -> None:
        data = _weather("v", symt=1, rr=0, rrisk=0, current_rr=2.0)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "rainy")

    def test_period_rain_marks_rainy(self) -> None:
        data = _weather("v", symt=1, rr=1.5, rrisk=0)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "rainy")

    def test_high_rrisk_marks_rainy(self) -> None:
        data = _weather("v", symt=1, rr=0, rrisk=75)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "rainy", "rrisk≥50 must trigger rainy regardless of symt")

    def test_rrisk_at_threshold_is_rainy(self) -> None:
        # The boundary value (50.0) is rainy.
        data = _weather("v", symt=1, rr=0, rrisk=50)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "rainy")

    def test_rrisk_just_below_threshold_is_sunny(self) -> None:
        # Just below the boundary, with the sun symbol and no rain, is sunny.
        data = _weather("v", symt=1, rr=0, rrisk=49.9)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "sunny")

    def test_low_rrisk_with_sun_symbol_is_sunny(self) -> None:
        data = _weather("v", symt=1, rr=0, rrisk=10)
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "sunny")

    def test_today_missing_target_period_returns_unknown(self) -> None:
        # If today's morning bucket is missing entirely (e.g., bot ran early),
        # we fall back to "unknown" rather than guessing cloudy.
        data = {
            "aare": {"temperature": 18.0},
            "weather": {
                "current": {"tt": 22, "rr": 0},
                "today": {"n": {"symt": 1, "rr": 0, "rrisk": 0}},
            },
        }
        now = FIXED_NOW.replace(hour=9)  # routes to "v", which is absent
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "unknown")

    def test_period_present_but_no_symt_is_cloudy(self) -> None:
        # We have a period with no rain signal and no sun symbol — be honest
        # and call it cloudy, not sunny.
        data = _weather("v", rr=0, rrisk=0)  # no symt
        now = FIXED_NOW.replace(hour=9)
        _, tier = classify_weather(data, now)
        self.assertEqual(tier, "cloudy")

    def test_non_sun_symbol_is_cloudy(self) -> None:
        for symt in (2, 3, 5, 7):
            with self.subTest(symt=symt):
                data = _weather("v", symt=symt, rr=0, rrisk=0)
                now = FIXED_NOW.replace(hour=9)
                _, tier = classify_weather(data, now)
                self.assertEqual(tier, "cloudy")


# ---------------------------------------------------------------------------
# Slogan matrix
# ---------------------------------------------------------------------------

class TestSloganMatrix(unittest.TestCase):
    WATER_TIERS = ("frigid", "cold", "cool", "swim", "perfect", "hot")

    def test_matrix_is_complete(self) -> None:
        # Every (water, weather) pair must have a slogan — missing keys would
        # crash classify() at runtime.
        for water in self.WATER_TIERS:
            for weather in WEATHER_TIERS:
                with self.subTest(water=water, weather=weather):
                    self.assertIn((water, weather), SLOGAN_MATRIX)

    def test_no_extraneous_keys(self) -> None:
        # Catch typos in matrix keys.
        valid_water = set(self.WATER_TIERS)
        valid_weather = set(WEATHER_TIERS)
        for water, weather in SLOGAN_MATRIX.keys():
            with self.subTest(key=(water, weather)):
                self.assertIn(water, valid_water)
                self.assertIn(weather, valid_weather)

    def test_all_slogans_are_unique(self) -> None:
        slogans = list(SLOGAN_MATRIX.values())
        self.assertEqual(len(set(slogans)), len(slogans),
                         f"duplicate slogans in matrix: {slogans}")

    def test_rainy_slogans_block_swimming(self) -> None:
        # The user's hard rule: "rain → no swimming". Every rainy slogan must
        # contain a clear negative signal AND must not affirmatively tell the
        # reader to swim. Word-boundary match for short tokens so "no" doesn't
        # match "now".
        negative_tokens = (r"🚫", r"\bskip\b", r"\bwait\b", r"\bstay\b", r"\bno\b", r"\bdefinitely\b")
        negative_re = re.compile("|".join(negative_tokens), re.IGNORECASE)
        positive_swim_phrases = ("go now", "get in", "get there", "go for it")
        for water in self.WATER_TIERS:
            slogan = SLOGAN_MATRIX[(water, "rainy")]
            lower = slogan.lower()
            with self.subTest(water=water, slogan=slogan):
                self.assertRegex(
                    slogan, negative_re,
                    f"rainy {water!r} slogan lacks a negative signal",
                )
                for phrase in positive_swim_phrases:
                    self.assertNotIn(
                        phrase, lower,
                        f"rainy slogan must not say {phrase!r}: {slogan!r}",
                    )

    def test_warm_sunny_is_enthusiastic(self) -> None:
        for water in ("swim", "perfect", "hot"):
            slogan = SLOGAN_MATRIX[(water, "sunny")].lower()
            with self.subTest(water=water):
                self.assertTrue(
                    "go" in slogan or "now" in slogan or "peak" in slogan,
                    f"warm+sunny slogan should be enthusiastic: {slogan!r}",
                )


# ---------------------------------------------------------------------------
# classify (water + weather → emoji, color, slogan)
# ---------------------------------------------------------------------------

class TestClassify(unittest.TestCase):
    def test_returns_water_tier_emoji_and_color(self) -> None:
        for temp in (8.0, 13.0, 16.0, 18.5, 20.5, 23.0):
            with self.subTest(temp=temp):
                w_emoji, w_color, _ = classify_water(temp)
                emoji, color, _ = classify(temp, weather_tier="sunny")
                self.assertEqual(emoji, w_emoji)
                self.assertEqual(color, w_color)

    def test_weather_tier_changes_slogan(self) -> None:
        # Same water, different weather → different slogan.
        _, _, sunny = classify(18.5, weather_tier="sunny")
        _, _, rainy = classify(18.5, weather_tier="rainy")
        self.assertNotEqual(sunny, rainy)

    def test_unknown_weather_uses_neutral_slogan(self) -> None:
        # The unknown-weather column should never mention rain or sun.
        for temp in (-5, 10, 13, 16, 18, 20, 23):
            _, _, slogan = classify(float(temp), weather_tier="unknown")
            lower = slogan.lower()
            with self.subTest(temp=temp, slogan=slogan):
                self.assertNotIn("rain", lower)
                self.assertNotIn("sunny", lower)


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
# build_api_url (now configurable via app/version)
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
# build_payload — block layout & content (with weather)
# ---------------------------------------------------------------------------

def _full_data(*, water_temp: float = 19.2, weather_period: str = "v",
               symt: int = 1, rr: float = 0, rrisk: float = 0, atmp: float = 24,
               current_rr: float = 0, flow: int = 142, forecast2h: float = 19.8) -> dict:
    """Build a realistic API response for tests.

    `weather_period` is the today.* key to populate. FIXED_NOW (11:30) maps
    to the morning bucket "v"; pass "n" or "a" to target other hours.
    """
    return {
        "aare": {"temperature": water_temp, "flow": flow, "forecast2h": forecast2h},
        "weather": {
            "current": {"tt": atmp, "rr": current_rr},
            "today": {weather_period: {"symt": symt, "rr": rr, "rrisk": rrisk, "tt": atmp}},
        },
    }


class TestBuildPayloadStructure(unittest.TestCase):
    """The block order is the visual hierarchy — guard it carefully."""

    def test_block_order_is_headline_slogan_divider_details_attribution(self) -> None:
        payload = build_payload(_full_data(), now=FIXED_NOW)
        blocks = payload["attachments"][0]["blocks"]
        ids = [b.get("block_id") for b in blocks]
        types = [b["type"] for b in blocks]
        self.assertEqual(ids, ["headline", "slogan", None, "details", "attribution"])
        self.assertEqual(types, ["header", "section", "divider", "section", "context"])

    def test_block_order_is_same_when_cold(self) -> None:
        payload = build_payload(_full_data(water_temp=8.5), now=FIXED_NOW)
        ids = [b.get("block_id") for b in payload["attachments"][0]["blocks"]]
        self.assertEqual(ids, ["headline", "slogan", None, "details", "attribution"])

    def test_attachment_color_is_water_tier_color(self) -> None:
        _, expected, _ = classify_water(_full_data()["aare"]["temperature"])
        self.assertEqual(
            build_payload(_full_data(), now=FIXED_NOW)["attachments"][0]["color"],
            expected,
        )


class TestBuildPayloadHeadline(unittest.TestCase):
    def test_headline_contains_temperature_date_and_city(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "headline")
        self.assertIn("19.2°C", text)
        self.assertIn("Aare Bern", text)
        self.assertIn("Wed 27 May", text)
        self.assertIn("11:30", text)

    def test_headline_uses_custom_city_label(self) -> None:
        text = _text_of(
            build_payload(_full_data(), city_label="Thun", now=FIXED_NOW),
            "headline",
        )
        self.assertIn("Aare Thun", text)
        self.assertNotIn("Aare Bern", text)


class TestBuildPayloadSlogan(unittest.TestCase):
    def test_slogan_is_bold_mrkdwn(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "slogan")
        self.assertTrue(text.startswith("*") and text.endswith("*"), text)

    def test_slogan_reflects_water_and_weather(self) -> None:
        # Sunny + swim-worthy
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "slogan")
        self.assertIn(SLOGAN_MATRIX[("swim", "sunny")], text)

    def test_rainy_slogan_overrides_warm_water(self) -> None:
        data = _full_data(water_temp=19.0, current_rr=2.5)
        text = _text_of(build_payload(data, now=FIXED_NOW), "slogan")
        self.assertIn(SLOGAN_MATRIX[("swim", "rainy")], text)

    def test_unknown_weather_uses_neutral_slogan(self) -> None:
        data = {"aare": {"temperature": 18.0}}
        text = _text_of(build_payload(data, now=FIXED_NOW), "slogan")
        self.assertIn(SLOGAN_MATRIX[("swim", "unknown")], text)


class TestBuildPayloadDetails(unittest.TestCase):
    def test_outlook_line_shows_weather_and_air(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "details")
        self.assertIn("Sunny", text)
        self.assertIn("Air 24°C", text)

    def test_rainy_outlook_shows_rain_emoji_and_label(self) -> None:
        data = _full_data(water_temp=19.0, current_rr=1.5)
        text = _text_of(build_payload(data, now=FIXED_NOW), "details")
        self.assertIn(WEATHER_EMOJI["rainy"], text)
        self.assertIn("Raining", text)

    def test_bar_and_forecast_and_flow_present(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "details")
        self.assertIn("10°—26°", text)
        self.assertIn("↗", text)
        self.assertIn("19.8°C", text)
        self.assertIn("Flow 142 m³/s", text)

    def test_missing_forecast_omits_2h(self) -> None:
        data = {"aare": {"temperature": 17.0}}
        text = _text_of(build_payload(data, now=FIXED_NOW), "details")
        self.assertNotIn("2h", text)

    def test_missing_weather_falls_back_to_plain_air(self) -> None:
        data = {"aare": {"temperature": 17.0, "flow": 150}}
        text = _text_of(build_payload(data, now=FIXED_NOW), "details")
        self.assertNotIn("Sunny", text)
        self.assertNotIn("Raining", text)

    def test_missing_flow_omits_flow(self) -> None:
        data = _full_data(flow=None)  # type: ignore[arg-type]
        text = _text_of(build_payload(data, now=FIXED_NOW), "details")
        self.assertNotIn("Flow", text)

    def test_no_bernese_german_leaks(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "details")
        for forbidden in ("Bärn", "äuä", "Plütterwarm", "wermer", "sunnig", "Donschti"):
            self.assertNotIn(forbidden, text)


class TestBuildPayloadAttribution(unittest.TestCase):
    """aare.guru's terms require linking back to aare.guru and BAFU."""

    def test_links_present_warm(self) -> None:
        text = _text_of(build_payload(_full_data(), now=FIXED_NOW), "attribution")
        self.assertIn(AARE_GURU_URL, text)
        self.assertIn(BAFU_URL, text)
        self.assertIn("aare.guru", text)
        self.assertIn("BAFU", text)

    def test_links_present_cold(self) -> None:
        text = _text_of(
            build_payload(_full_data(water_temp=8.5), now=FIXED_NOW),
            "attribution",
        )
        self.assertIn(AARE_GURU_URL, text)
        self.assertIn(BAFU_URL, text)


if __name__ == "__main__":
    unittest.main()
