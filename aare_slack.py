"""Fetch current Aare river temperature and post to Slack.

Uses the public aare.guru API (https://aare.guru/) which is itself backed by
BAFU hydrology data. Standard library only — no external dependencies.

By default posts for Bern only. Set the ``CITIES`` env var to a comma-separated
list (e.g. ``CITIES=bern,thun,interlaken``) to post for several cities in one run.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API_BASE = "https://aareguru.existenz.ch/v2018/current"
# aare.guru asks integrators to identify themselves with `app` and `version`
# query params. Override per-deployment via AARE_APP and AARE_VERSION env vars.
APP_NAME = "aare-slack-bot"
APP_VERSION = "1"
USER_AGENT = "aare-slack-bot/1.0 (+https://github.com/amaise-inc/aare-slack-bot)"
HTTP_TIMEOUT_S = 10

TREND_THRESHOLD = 0.3  # °C difference that counts as a real trend
CH_TZ = ZoneInfo("Europe/Zurich")
DEFAULT_CITIES = ("bern",)

# aare.guru terms ask integrators to link back to aare.guru and BAFU.
AARE_GURU_URL = "https://aare.guru"
BAFU_URL = "https://www.hydrodaten.admin.ch"
ATTRIBUTION_TEXT = (
    f"📡 Data: <{AARE_GURU_URL}|aare.guru> · <{BAFU_URL}|BAFU>"
)


# ---------------------------------------------------------------------------
# Pure functions (covered by tests)
# ---------------------------------------------------------------------------

def parse_cities(env_value: str | None) -> list[str]:
    """Parse a comma-separated CITIES env var. Empty/missing → default."""
    if not env_value:
        return list(DEFAULT_CITIES)
    cities = [c.strip().lower() for c in env_value.split(",") if c.strip()]
    return cities or list(DEFAULT_CITIES)


def build_api_url(
    city: str,
    app: str = APP_NAME,
    version: str = APP_VERSION,
) -> str:
    """Build the aare.guru `current` API URL for a given city slug.

    aare.guru asks integrators to identify themselves via ``app`` and ``version``.
    """
    return f"{API_BASE}?city={city}&app={app}&version={version}"


# One tier per whole degree from 12 °C up to "off the charts". Hotter tiers
# use warmer/more vibrant colors and more celebratory emoji.
# Order: highest threshold first. `classify` walks down and returns the first match.
_TIERS: tuple[tuple[float, str, str, str], ...] = (
    (24, "🔥", "#7a0010", "🔥🌶️🔥 OFF THE CHARTS — record territory!"),
    (23, "🔥", "#b00020", "🔥🔥 Hotter than a hot tub."),
    (22, "🔥", "#d0021b", "🔥🏊 BATHTUB MODE — spaghetti water."),
    (21, "☀️", "#f5511a", "🏖️☀️ Linger after work."),
    (20, "☀️", "#f59020", "🌞 PERFECT — grab the towel."),
    (19, "🏊", "#2eb886", "🏊‍♀️🏊 Properly nice, go for it."),
    (18, "🏊", "#7ed321", "🎉 SWIM-WORTHY — get in."),
    (17, "🤔", "#f5d020", "🤔 Almost swimmable, depending on bravery."),
    (16, "😬", "#f5a623", "🤐 Quick dip if you must."),
    (15, "😬", "#f5a623", "🥽 Brave-souls only."),
    (14, "🥶", "#6cb7d8", "🧣 Cold enough to lie about it."),
    (13, "🥶", "#5b9bd5", "🥶 Numb fingers in seconds."),
    (12, "🥶", "#4a90e2", "🦶 Toes-only territory."),
)
_SUB_FREEZING: tuple[str, str, str] = (
    "🧊",
    "#2a5e8a",
    "☕ Hard pass — coffee weather.",
)


def classify(temp: float) -> tuple[str, str, str]:
    """Map a water temperature to (emoji, slack_color, slogan).

    One tier per whole degree from 12 °C up to "off the charts" at 24 °C+.
    Hotter tiers get warmer colors and more celebratory emoji. The slogan is
    the tier's signature line — short, fun, and the second-most prominent
    element in the Slack message (the temperature itself is first).
    """
    for threshold, emoji, color, slogan in _TIERS:
        if temp >= threshold:
            return emoji, color, slogan
    return _SUB_FREEZING


def build_bar(
    temp: float,
    scale_min: int = 10,
    scale_max: int = 26,
    width: int = 16,
) -> str:
    """Render a unicode bar showing where `temp` sits on the scale."""
    if scale_max <= scale_min:
        raise ValueError("scale_max must be greater than scale_min")
    pos = round((temp - scale_min) / (scale_max - scale_min) * width)
    pos = max(0, min(width, pos))
    return "█" * pos + "░" * (width - pos)


def forecast_trend(temp: float, forecast2h: float) -> str:
    """Arrow describing the 2h forecast direction relative to current temp."""
    delta = forecast2h - temp
    if delta >= TREND_THRESHOLD:
        return "↗"
    if delta <= -TREND_THRESHOLD:
        return "↘"
    return "→"


def build_datetime_str(now: datetime) -> str:
    """Format the date/time line shown at the top of every Slack message."""
    return now.strftime("%a %d %b · %H:%M")


def build_payload(
    data: dict,
    *,
    city_label: str = "Bern",
    now: datetime | None = None,
) -> dict:
    """Build the Slack webhook JSON payload from an aare.guru `current` response.

    The aare.guru response is nested: `data["aare"]["temperature"]` is the
    water temp, `data["aare"]["flow"]` is the flow, `data["weather"]["current"]["tt"]`
    is the air temp. Optional fields are skipped gracefully if missing.

    `now` is the timestamp shown in the headline; defaults to now in Europe/Zurich.

    Visual hierarchy:
        1. ``headline`` — big header block: date + emoji + temperature + city.
        2. ``slogan``   — bold one-liner (tier signature).
        3. divider
        4. ``details``  — forecast / air / flow / bar (small).
        5. ``attribution`` — context block with aare.guru + BAFU links.
    """
    aare = data["aare"]
    temp = float(aare["temperature"])
    forecast2h_raw = aare.get("forecast2h")
    flow = aare.get("flow")
    weather_current = (data.get("weather") or {}).get("current") or {}
    atmp = weather_current.get("tt")

    emoji, color, slogan = classify(temp)
    bar = build_bar(temp)
    when = now if now is not None else datetime.now(CH_TZ)

    # 1. Headline — date + temp are the two big things.
    headline_text = (
        f"{emoji}  Aare {city_label}  {temp}°C"
        f"   ·   {build_datetime_str(when)}"
    )

    # 4. Details — bar, then forecast, then ambient metrics, with blank lines
    #    in between for breathing room. Slack renders "\n\n" as a paragraph
    #    break, "\n" as a soft newline.
    detail_sections: list[str] = [f"`{bar}`   _10°—26°_"]
    if forecast2h_raw is not None:
        forecast2h = float(forecast2h_raw)
        arrow = forecast_trend(temp, forecast2h)
        detail_sections.append(f"{arrow}  *2h forecast: {forecast2h}°C*")

    ambient: list[str] = []
    if atmp is not None:
        ambient.append(f"🌡️  Air {atmp}°C")
    if flow is not None:
        ambient.append(f"💧  Flow {flow} m³/s")
    if ambient:
        detail_sections.append("   ·   ".join(ambient))

    details_text = "\n\n".join(detail_sections)

    blocks: list[dict] = [
        {
            "type": "header",
            "block_id": "headline",
            "text": {"type": "plain_text", "text": headline_text, "emoji": True},
        },
        {
            "type": "section",
            "block_id": "slogan",
            "text": {"type": "mrkdwn", "text": f"*{slogan}*"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "block_id": "details",
            "text": {"type": "mrkdwn", "text": details_text},
        },
        {
            "type": "context",
            "block_id": "attribution",
            "elements": [
                {"type": "mrkdwn", "text": ATTRIBUTION_TEXT},
            ],
        },
    ]

    return {"attachments": [{"color": color, "blocks": blocks}]}


# ---------------------------------------------------------------------------
# IO (not covered by unit tests)
# ---------------------------------------------------------------------------

def fetch_aare_data(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def post_to_slack(payload: dict, webhook_url: str) -> None:
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status >= 300 or body.strip() != "ok":
            raise RuntimeError(f"Slack returned {resp.status}: {body!r}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _city_label(city: str) -> str:
    """Display label for a city slug (e.g. 'bern' → 'Bern')."""
    return city.replace("-", " ").title()


def main() -> int:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print(
            "ERROR: SLACK_WEBHOOK_URL environment variable not set",
            file=sys.stderr,
        )
        return 1

    cities = parse_cities(os.environ.get("CITIES"))
    app = os.environ.get("AARE_APP") or APP_NAME
    version = os.environ.get("AARE_VERSION") or APP_VERSION
    exit_code = 0

    for city in cities:
        url = build_api_url(city, app=app, version=version)
        try:
            data = fetch_aare_data(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"ERROR: failed to fetch aare.guru API for {city}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue
        except json.JSONDecodeError as exc:
            print(f"ERROR: invalid JSON from aare.guru API for {city}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

        aare = data.get("aare")
        if not isinstance(aare, dict) or "temperature" not in aare:
            print(
                f"ERROR: API response missing aare.temperature for {city}: {data!r}",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
            continue

        payload = build_payload(data, city_label=_city_label(city))

        try:
            post_to_slack(payload, webhook_url)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"ERROR: failed to post {city} to Slack: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 3)
            continue

        print(f"Posted: Aare {_city_label(city)} {aare['temperature']}°C")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
