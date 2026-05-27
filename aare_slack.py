"""Fetch current Aare river temperature and post to Slack.

Uses the public aare.guru API (https://aare.guru/) which is itself backed by
BAFU hydrology data. Standard library only — no external dependencies.

By default posts for Bern only. Set the ``CITIES`` env var to a comma-separated
list (e.g. ``CITIES=bern,thun,interlaken``) to post for several cities in one run.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
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
_RAIN_RISK_THRESHOLD = 50.0  # `rrisk` >= this means "expect rain", => no swim
CH_TZ = ZoneInfo("Europe/Zurich")
DEFAULT_CITIES = ("bern",)
# aare.guru city slugs are short lowercase identifiers. Reject anything else
# to keep the outbound URL safe from injection via env vars.
_CITY_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,32}$")

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
    """Parse the CITIES env var, dropping anything that isn't a valid slug.

    Slugs are short lowercase identifiers (e.g. ``bern``, ``thun``,
    ``interlaken-ost``). Invalid entries are skipped so a typo or hostile env
    value can't smuggle extra query params into the outbound URL.
    """
    if not env_value:
        return list(DEFAULT_CITIES)
    cities: list[str] = []
    for raw in env_value.split(","):
        slug = raw.strip().lower()
        if slug and _CITY_SLUG_RE.match(slug):
            cities.append(slug)
        elif slug:
            print(f"WARN: ignoring invalid city slug {slug!r}", file=sys.stderr)
    return cities or list(DEFAULT_CITIES)


def build_api_url(
    city: str,
    app: str = APP_NAME,
    version: str = APP_VERSION,
) -> str:
    """Build the aare.guru `current` API URL for a given city slug.

    aare.guru asks integrators to identify themselves via ``app`` and ``version``.
    All params are URL-encoded so user-supplied env values can't smuggle extra
    query params or fragment markers.
    """
    query = urllib.parse.urlencode({"city": city, "app": app, "version": version})
    return f"{API_BASE}?{query}"


# One water tier per whole degree from 12 °C up to "off the charts". Hotter
# tiers use warmer / more vibrant colors. `_WATER_TIERS` returns a tier *name*
# (used as the matrix row key) so the slogan picker can combine it with the
# current weather.
# Order: highest threshold first. `classify_water` walks down and returns the
# first match.
_WATER_TIERS: tuple[tuple[float, str, str, str], ...] = (
    (24, "🔥", "#7a0010", "hot"),
    (23, "🔥", "#b00020", "hot"),
    (22, "🔥", "#d0021b", "hot"),
    (21, "☀️", "#f5511a", "perfect"),
    (20, "☀️", "#f59020", "perfect"),
    (19, "🏊", "#2eb886", "swim"),
    (18, "🏊", "#7ed321", "swim"),
    (17, "🤔", "#f5d020", "cool"),
    (16, "😬", "#f5a623", "cool"),
    (15, "😬", "#f5a623", "cool"),
    (14, "🥶", "#6cb7d8", "cold"),
    (13, "🥶", "#5b9bd5", "cold"),
    (12, "🥶", "#4a90e2", "cold"),
)
_SUB_FREEZING_TIER: tuple[str, str, str] = ("🧊", "#2a5e8a", "frigid")

# Weather tier names. "unknown" is the fallback when the API gives us nothing
# useful to read — we still post but use a weather-neutral slogan.
WEATHER_TIERS = ("sunny", "cloudy", "rainy", "unknown")

# Decision matrix: water tier × weather tier → slogan. Rain always blocks the
# swim recommendation (the user's hard rule: "rain → no swimming"). Sunny +
# warm is the best case. "unknown" slogans avoid mentioning weather.
SLOGAN_MATRIX: dict[tuple[str, str], str] = {
    # frigid (<12 °C)
    ("frigid", "sunny"):   "🧊☀️ Ice water, warm sun — pretty though.",
    ("frigid", "cloudy"):  "🧊☁️ Frigid and grey — winter-coat weather.",
    ("frigid", "rainy"):   "🧊🌧️ Frigid and wet — stay inside.",
    ("frigid", "unknown"): "☕ Hard pass — coffee weather.",
    # cold (12–14 °C)
    ("cold", "sunny"):     "🦶☀️ Cold water, warm sun — toes only.",
    ("cold", "cloudy"):    "🥶☁️ Cold and grey — coffee day.",
    ("cold", "rainy"):     "🚫🌧️ Cold and raining — definitely no.",
    ("cold", "unknown"):   "🥶 Toes-only territory.",
    # cool (15–17 °C)
    ("cool", "sunny"):     "🥽☀️ Cool water, bright sun — brave souls in, towel ready.",
    ("cool", "cloudy"):    "😬☁️ Cool and grey — not really swim weather.",
    ("cool", "rainy"):     "🚫🌧️ Cool and raining — skip it.",
    ("cool", "unknown"):   "🥽 Brave-souls only.",
    # swim (18–19 °C)
    ("swim", "sunny"):     "🎉☀️ Swim-worthy and sunny — go now!",
    ("swim", "cloudy"):    "🏊☁️ Swim-worthy under clouds — still a go.",
    ("swim", "rainy"):     "🚫🌧️ Warm enough but raining — wait it out.",
    ("swim", "unknown"):   "🎉 SWIM-WORTHY — get in.",
    # perfect (20–21 °C)
    ("perfect", "sunny"):  "🌞🏊 Perfect water + perfect sun — get there now!",
    ("perfect", "cloudy"): "🏖️☁️ Perfect water, overcast — still go.",
    ("perfect", "rainy"):  "🚫🌧️ Perfect water wasted on rain — wait it out.",
    ("perfect", "unknown"): "🌞 PERFECT — grab the towel.",
    # hot (22+ °C)
    ("hot", "sunny"):      "🔥☀️ Bathtub mode + full sun — peak conditions!",
    ("hot", "cloudy"):     "🔥☁️ Hot water, grey skies — still worth it.",
    ("hot", "rainy"):      "🚫🌧️ Warm but raining — wait for the sun.",
    ("hot", "unknown"):    "🔥 BATHTUB MODE.",
}

WEATHER_EMOJI: dict[str, str] = {
    "sunny": "☀️",
    "cloudy": "⛅",
    "rainy": "🌧️",
    "unknown": "",
}

WEATHER_LABEL: dict[str, str] = {
    "sunny": "Sunny",
    "cloudy": "Cloudy",
    "rainy": "Raining",
    "unknown": "",
}


def classify_water(temp: float) -> tuple[str, str, str]:
    """Map a water temperature to (emoji, slack_color, water_tier_name).

    One tier per whole degree from 12 °C up to "off the charts" at 24 °C+.
    Hotter tiers get warmer / more vibrant colors.
    """
    for threshold, emoji, color, tier_name in _WATER_TIERS:
        if temp >= threshold:
            return emoji, color, tier_name
    return _SUB_FREEZING_TIER


def _pick_period(today: dict, hour: int) -> dict:
    """Pick today.v / today.n / today.a based on current hour ("right now").

    `v` = Vormittag (morning, < 12 h), `n` = Nachmittag (afternoon, 12–17 h),
    `a` = Abend (evening, ≥ 17 h).
    """
    key = "v" if hour < 12 else ("n" if hour < 17 else "a")
    return today.get(key) or {}


def classify_weather(data: dict, now: datetime) -> tuple[str, str]:
    """Return (weather_emoji, weather_tier) ∈ {sunny, cloudy, rainy, unknown}.

    Reads `weather.current.rr` for live rain and the matching `weather.today`
    period (chosen by `now.hour`) for sun/rain forecast. Rain is detected
    aggressively (any of: current rain > 0, period rain > 0, period rrisk ≥
    `_RAIN_RISK_THRESHOLD`) because the user's rule is "rain → no swimming".
    """
    weather = data.get("weather") or {}
    if not weather:
        return WEATHER_EMOJI["unknown"], "unknown"

    current = weather.get("current") or {}
    today = weather.get("today") or {}
    period = _pick_period(today, now.hour)

    current_rr = float(current.get("rr") or 0)
    period_rr = float(period.get("rr") or 0)
    rrisk = float(period.get("rrisk") or 0)

    if current_rr > 0 or period_rr > 0 or rrisk >= _RAIN_RISK_THRESHOLD:
        return WEATHER_EMOJI["rainy"], "rainy"

    symt = period.get("symt")
    if symt == 1 and rrisk < _RAIN_RISK_THRESHOLD:
        return WEATHER_EMOJI["sunny"], "sunny"

    if not period:
        # No forecast for this period and no rain signal we could parse —
        # don't claim sunny or cloudy, fall back to the neutral slogans.
        return WEATHER_EMOJI["unknown"], "unknown"

    return WEATHER_EMOJI["cloudy"], "cloudy"


def classify(temp: float, weather_tier: str = "unknown") -> tuple[str, str, str]:
    """Map (water_temp, weather_tier) to (emoji, slack_color, combined_slogan).

    Thin wrapper around `classify_water` + the slogan matrix. The emoji and
    colour come from the per-degree water tier; the slogan comes from the
    matrix and reflects both dimensions ("rain → no swim", "sunny + warm =
    go now!").
    """
    emoji, color, water_tier = classify_water(temp)
    slogan = SLOGAN_MATRIX[(water_tier, weather_tier)]
    return emoji, color, slogan


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

    when = now if now is not None else datetime.now(CH_TZ)
    weather_emoji, weather_tier = classify_weather(data, when)
    emoji, color, slogan = classify(temp, weather_tier=weather_tier)
    bar = build_bar(temp)

    # 1. Headline — date + temp are the two big things.
    headline_text = (
        f"{emoji}  Aare {city_label}  {temp}°C"
        f"   ·   {build_datetime_str(when)}"
    )

    # 4. Details — "outlook for right now" first (answers: are we going now?),
    #    then forecast, flow, and the bar. Blank lines between for breathing
    #    room (Slack renders "\n\n" as a paragraph break).
    detail_sections: list[str] = []

    # Outlook: weather right now + air temp, fused into one short verdict line.
    if weather_tier != "unknown" and atmp is not None:
        detail_sections.append(
            f"{weather_emoji}  *{WEATHER_LABEL[weather_tier]}* right now, Air {atmp}°C"
        )
    elif weather_tier != "unknown":
        detail_sections.append(f"{weather_emoji}  *{WEATHER_LABEL[weather_tier]}* right now")
    elif atmp is not None:
        detail_sections.append(f"🌡️  Air {atmp}°C")

    if forecast2h_raw is not None:
        forecast2h = float(forecast2h_raw)
        arrow = forecast_trend(temp, forecast2h)
        detail_sections.append(f"{arrow}  *2h water: {forecast2h}°C*")

    if flow is not None:
        detail_sections.append(f"💧  Flow {flow} m³/s")

    detail_sections.append(f"`{bar}`   _10°—26°_")

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
    """GET the API and return parsed JSON.

    Raises ``RuntimeError`` on non-2xx responses. Read is capped to keep a
    pathological response from buffering arbitrarily much.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"aare.guru returned HTTP {resp.status}")
        body = resp.read(2_000_000)  # 2 MB cap — real responses are ~5 KB
    return json.loads(body)


def post_to_slack(payload: dict, webhook_url: str) -> None:
    """POST a payload to the Slack incoming webhook.

    Raises ``RuntimeError`` if Slack does not return HTTP 2xx with body "ok".

    SECURITY: ``webhook_url`` is a secret. Never include it in raised errors
    or log lines — keep error messages limited to the Slack response.
    """
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = resp.read(10_000).decode("utf-8", errors="replace")
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
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"ERROR: failed to fetch aare.guru API for {city}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue
        except json.JSONDecodeError as exc:
            print(f"ERROR: invalid JSON from aare.guru API for {city}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

        aare = data.get("aare")
        if not isinstance(aare, dict) or "temperature" not in aare:
            keys = sorted((data or {}).keys()) if isinstance(data, dict) else type(data).__name__
            print(
                f"ERROR: API response missing aare.temperature for {city}; top-level keys: {keys}",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
            continue

        try:
            payload = build_payload(data, city_label=_city_label(city))
        except (KeyError, TypeError, ValueError) as exc:
            print(f"ERROR: bad API payload shape for {city}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue

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
