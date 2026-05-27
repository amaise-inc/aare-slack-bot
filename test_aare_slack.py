"""Unit tests for the pure functions in aare_slack.

Run with: python -m unittest -v
"""
import unittest

from aare_slack import (
    AARE_GURU_URL,
    BAFU_URL,
    build_bar,
    build_payload,
    classify,
    forecast_trend,
)


def _section_text(payload: dict) -> str:
    """Return the text of the (single) section block in a payload."""
    blocks = payload["attachments"][0]["blocks"]
    for block in blocks:
        if block.get("type") == "section":
            return block["text"]["text"]
    raise AssertionError(f"no section block in {blocks!r}")


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

    def test_taglines_are_english(self) -> None:
        # No Bernese/German strings — taglines should be plain ASCII English.
        for temp in (2.0, 13.0, 16.0, 18.5, 20.5, 23.0):
            with self.subTest(temp=temp):
                _, _, _, tagline = classify(temp)
                self.assertTrue(
                    tagline.isascii(), f"non-ASCII tagline at {temp}°C: {tagline!r}"
                )


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
        bar = build_bar(18.0)  # midpoint of the 10-26 scale
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
        # Exactly +/- 0.3 should count as a real trend.
        self.assertEqual(forecast_trend(18.0, 18.3), "↗")
        self.assertEqual(forecast_trend(18.0, 17.7), "↘")


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
        blocks = build_payload(_cold_data())["attachments"][0]["blocks"]
        types = [b["type"] for b in blocks]
        self.assertNotIn("header", types)
        self.assertIn("section", types)

    def test_warm_payload_has_header_block(self) -> None:
        blocks = build_payload(_warm_data())["attachments"][0]["blocks"]
        self.assertEqual(blocks[0]["type"], "header")
        self.assertIn("TIME TO SWIM!", blocks[0]["text"]["text"])

    def test_attachment_color_set(self) -> None:
        payload = build_payload(_warm_data())
        self.assertEqual(payload["attachments"][0]["color"], "#2eb886")

    def test_forecast_arrow_warming(self) -> None:
        text = _section_text(build_payload(_warm_data()))
        self.assertIn("↗", text)
        self.assertIn("2h: 19.8°C", text)

    def test_forecast_arrow_cooling(self) -> None:
        data = {
            "aare": {"temperature": 19.0, "flow": 140, "forecast2h": 18.5},
            "weather": {"current": {"tt": 22}},
        }
        self.assertIn("↘", _section_text(build_payload(data)))

    def test_missing_forecast_omits_2h_line(self) -> None:
        self.assertNotIn("2h:", _section_text(build_payload({"aare": {"temperature": 17.0}})))

    def test_missing_weather_block_is_graceful(self) -> None:
        text = _section_text(build_payload({"aare": {"temperature": 17.0, "flow": 150}}))
        self.assertIn("17.0°C", text)
        self.assertNotIn("Air", text)
        self.assertIn("Flow 150", text)

    def test_missing_flow_omits_flow_string(self) -> None:
        data = {
            "aare": {"temperature": 17.0},
            "weather": {"current": {"tt": 20}},
        }
        text = _section_text(build_payload(data))
        self.assertIn("Air 20°C", text)
        self.assertNotIn("Flow", text)

    def test_english_labels_only(self) -> None:
        text = _section_text(build_payload(_warm_data()))
        self.assertIn("Air 24°C", text)
        self.assertIn("Flow 142 m³/s", text)
        for forbidden in ("Bärn", "äuä", "Plütterwarm", "wermer"):
            self.assertNotIn(forbidden, text)

    def test_tagline_present_in_message(self) -> None:
        self.assertIn("Officially swim-worthy", _section_text(build_payload(_warm_data())))

    def test_attribution_links_present(self) -> None:
        # aare.guru's terms ask integrators to link back to aare.guru and BAFU.
        blocks = build_payload(_warm_data())["attachments"][0]["blocks"]
        context_blocks = [b for b in blocks if b.get("type") == "context"]
        self.assertEqual(len(context_blocks), 1, "expected one context block")
        attribution = context_blocks[0]["elements"][0]["text"]
        self.assertIn(AARE_GURU_URL, attribution)
        self.assertIn(BAFU_URL, attribution)
        self.assertIn("aare.guru", attribution)
        self.assertIn("BAFU", attribution)

    def test_attribution_present_on_cold_payload_too(self) -> None:
        blocks = build_payload(_cold_data())["attachments"][0]["blocks"]
        self.assertTrue(any(b.get("type") == "context" for b in blocks))


if __name__ == "__main__":
    unittest.main()
