"""Fetch current Aare river temperature for Bern and post to Slack.

Uses the public aare.guru API (https://aareguru.existenz.ch/) which is itself
backed by BAFU hydrology data. Standard library only — no external dependencies.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = (
    "https://aareguru.existenz.ch/v2018/current"
    "?city=bern&app=aare-slack-bot&version=1"
)
USER_AGENT = "aare-slack-bot/1.0 (+https://github.com/amaise/aare-slack-bot)"
HTTP_TIMEOUT_S = 10

TREND_THRESHOLD = 0.3  # °C difference that counts as a real trend

# aare.guru terms ask integrators to link back to aare.guru and BAFU.
AARE_GURU_URL = "https://aare.guru"
BAFU_URL = "https://www.hydrodaten.admin.ch"
ATTRIBUTION_TEXT = (
    f"Data: <{AARE_GURU_URL}|aare.guru> · <{BAFU_URL}|BAFU>"
)


# ---------------------------------------------------------------------------
# Pure functions (covered by tests)
# ---------------------------------------------------------------------------

def classify(temp: float) -> tuple[str, str, str | None, str]:
    """Map a water temperature to (emoji, slack_color, optional_header, tagline).

    The header is only set for swim-worthy temperatures (>= 18 °C); below that
    the message stays low-key. The tagline is a short fun line shown on every
    message.
    """
    if temp >= 22:
        return "🔥", "#d0021b", "BATHTUB MODE", "Spaghetti water. Bring sunscreen."
    if temp >= 20:
        return "☀️", "#2eb886", "PERFECT FOR A SWIM", "Grab the towel, this is the day."
    if temp >= 18:
        return "🏊", "#2eb886", "TIME TO SWIM!", "Officially swim-worthy. Get in."
    if temp >= 15:
        return "😬", "#f5a623", None, "Brave-souls only. Quick dip at most."
    if temp >= 12:
        return "🥶", "#4a90e2", None, "Toes-only territory."
    return "🧊", "#4a90e2", None, "Hard pass. Coffee weather."


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


def build_payload(data: dict) -> dict:
    """Build the Slack webhook JSON payload from an aare.guru `current` response.

    The aare.guru response is nested: `data["aare"]["temperature"]` is the
    water temp, `data["aare"]["flow"]` is the flow, `data["weather"]["current"]["tt"]`
    is the air temp. Optional fields are skipped gracefully if missing.
    """
    aare = data["aare"]
    temp = float(aare["temperature"])
    forecast2h_raw = aare.get("forecast2h")
    flow = aare.get("flow")
    weather_current = (data.get("weather") or {}).get("current") or {}
    atmp = weather_current.get("tt")

    emoji, color, header, tagline = classify(temp)
    bar = build_bar(temp)

    headline = f"{emoji} *Aare Bern: {temp}°C*"
    if forecast2h_raw is not None:
        forecast2h = float(forecast2h_raw)
        arrow = forecast_trend(temp, forecast2h)
        headline += f"   {arrow} *2h: {forecast2h}°C*"

    extras = []
    if atmp is not None:
        extras.append(f"Air {atmp}°C")
    if flow is not None:
        extras.append(f"Flow {flow} m³/s")

    text_lines = [headline, f"`{bar}`  _10°—26°_"]
    if extras:
        text_lines.append(" · ".join(extras))
    text_lines.append(f"_{tagline}_")
    text = "\n".join(text_lines)

    blocks: list[dict] = []
    if header:
        blocks.append(
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🌊 {header}"},
            }
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
    )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": ATTRIBUTION_TEXT},
            ],
        }
    )

    return {"attachments": [{"color": color, "blocks": blocks}]}


# ---------------------------------------------------------------------------
# IO (not covered by unit tests)
# ---------------------------------------------------------------------------

def fetch_aare_data(url: str = API_URL) -> dict:
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

def main() -> int:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print(
            "ERROR: SLACK_WEBHOOK_URL environment variable not set",
            file=sys.stderr,
        )
        return 1

    try:
        data = fetch_aare_data()
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR: failed to fetch aare.guru API: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON from aare.guru API: {exc}", file=sys.stderr)
        return 2

    aare = data.get("aare")
    if not isinstance(aare, dict) or "temperature" not in aare:
        print(
            f"ERROR: API response missing aare.temperature: {data!r}",
            file=sys.stderr,
        )
        return 2

    payload = build_payload(data)

    try:
        post_to_slack(payload, webhook_url)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        print(f"ERROR: failed to post to Slack: {exc}", file=sys.stderr)
        return 3

    print(f"Posted: Aare Bern {aare['temperature']}°C")
    return 0


if __name__ == "__main__":
    sys.exit(main())
