# aare-slack-bot

[![Tests](https://github.com/amaise-inc/aare-slack-bot/actions/workflows/test.yml/badge.svg)](https://github.com/amaise-inc/aare-slack-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

Posts the current Aare river water temperature in Bern to `#aare` four times
every **weekday** (Mon–Fri, no posts on weekends):

- **11:30 CH** — lunch decision
- **15:00 CH** — mid-afternoon check
- **16:30 CH** — pre-Feierabend
- **17:30 CH** — Feierabend

Each message shows the date + weekday, current temp with tier emoji, a 2-hour
forecast with trend arrow (↗ / → / ↘), air temp, flow, a short tagline, and an
attribution footer linking back to aare.guru and BAFU. Swim-worthy temperatures
(≥ 18 °C) get a coloured header so they really stand out.

## Sample output

The message has a clear visual hierarchy: **date + temperature** as the big
header, **bold slogan** below it, then a roomy **details** block (outlook,
forecast, flow, bar), and a small **attribution** footer. The vertical
attachment bar takes on the tier color — deep blue when frozen through to deep
red at record temps. The slogan combines **water tier × weather tier**, so
warm + sunny means "go now!" and **rain always blocks the swim recommendation**.

Warm + sunny:

```
🏊  Aare Bern  18.7°C   ·   Wed 27 May · 11:30       (header)
*🎉☀️ Swim-worthy and sunny — go now!*               (slogan, bold)
──────────────────────────────────────────────
☀️  Sunny right now, Air 27°C

↗  2h water: 19.0°C

💧  Flow 191 m³/s

`█████████░░░░░░░`  10°—26°
📡 Data: aare.guru · BAFU                            (footer)
```

Warm + raining (no swim regardless):

```
🏊  Aare Bern  18.7°C   ·   Wed 27 May · 16:00
*🚫🌧️ Warm enough but raining — wait it out.*
──────────────────────────────────────────────
🌧️  Raining right now, Air 18°C

↘  2h water: 18.4°C

💧  Flow 195 m³/s

`█████████░░░░░░░`  10°—26°
📡 Data: aare.guru · BAFU
```

## Decision matrix — water × weather

The slogan picker combines a coarse **water tier** with a coarse **weather
tier**. Water tier emoji + color still scale per-degree (so 22 °C, 23 °C,
24 °C look progressively more vibrant), but the slogan reflects both axes:

|                | ☀️ Sunny                       | ⛅ Cloudy                  | 🌧️ Rainy                          |
|----------------|--------------------------------|----------------------------|------------------------------------|
| **hot** ≥22 °C | Bathtub mode + sun — peak!     | Hot, grey — still worth it | 🚫 Warm but raining — wait        |
| **perfect** 20–21 °C | Perfect + perfect — go!  | Perfect, overcast — still go | 🚫 Wasted on rain — wait it out |
| **swim** 18–19 °C    | Swim-worthy and sunny — go now! | Swim-worthy, clouds — still a go | 🚫 Warm enough but raining — wait |
| **cool** 15–17 °C    | Brave souls in, towel ready | Cool + grey — not really   | 🚫 Skip it                        |
| **cold** 12–14 °C    | Cold water, warm sun — toes only | Cold and grey — coffee | 🚫 Definitely no                  |
| **frigid** <12 °C    | Ice water, pretty though   | Frigid and grey — winter coat | Frigid and wet — stay inside    |

**Rain rule.** Rain is detected aggressively (any of: current rain `> 0`,
forecast-period rain `> 0`, or forecast-period rain risk `rrisk ≥ 50%`). When
rain is detected the slogan never tells you to swim — it tells you to wait or
skip.

**Weather period mapping.** The bot reads `weather.today.v` (morning) before
12:00, `weather.today.n` (afternoon) 12:00–17:00, and `weather.today.a`
(evening) after 17:00 — i.e. *right now*, not later today.

**Water tier color escalation:** deep blue (<12 °C) → blue → cyan → lime → green
→ yellow → orange → red → deep red (≥24 °C).

Forecast arrow uses a ±0.3 °C threshold: `↗` warming, `↘` cooling, `→` steady.

## Multiple cities

**Setup is one env var.** By default the bot posts for **Bern** only. To post
for several Aare-side cities in one run, set the `CITIES` env var to a
comma-separated list of slugs that the [aare.guru API](https://aare.guru/)
recognises:

```yaml
# in .github/workflows/aare.yml, under the "Post …" step's env:
env:
  SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_AARE }}
  CITIES: bern,thun,interlaken
```

The bot will post one Slack message per city. If one city's fetch fails, the
others still post and the workflow exits non-zero so you notice in CI.

## Deploy

1. **Set the repo secret with the `#aare` webhook URL** (interactive, so the URL
   does not land in shell history):
   ```bash
   gh secret set SLACK_WEBHOOK_AARE
   # paste the webhook URL when prompted, then press Enter
   ```

2. **Push to GitHub.** The schedule activates automatically once `aare.yml` is on
   the default branch.

3. **Trigger a manual run** to verify:
   ```bash
   gh workflow run aare.yml
   gh run watch
   ```

## Local development

```bash
make test                                  # run unit tests
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
make run                                   # post to Slack (Bern only)

# Multi-city local run:
CITIES=bern,thun python aare_slack.py
```

## Data source

Data from the [aare.guru API](https://aare.guru/), an unofficial public API by
Bureau für digitale Existenz, sourced from the
[Bundesamt für Umwelt (BAFU)](https://www.hydrodaten.admin.ch/).
**Non-commercial use only.** Every Slack message includes a footer linking back
to both sources, per aare.guru's usage terms.

## Authors & credits

- **[Markus Baumgartner](https://www.linkedin.com/in/markus-baumgartner-43025a8a/)** — [amaise AG](https://amaise.com)
- **Data:** [aare.guru](https://aare.guru/) (Bureau für digitale Existenz) ·
  [BAFU](https://www.hydrodaten.admin.ch/) (Bundesamt für Umwelt)
- **Co-author:** Initial implementation drafted with
  [Claude Code](https://claude.com/claude-code)

## License

[MIT](LICENSE) — see the LICENSE file for the full text. Note: the MIT license
covers this source code only and does not extend any rights over the upstream
aare.guru / BAFU data, which remains non-commercial-use-only.

## Contributing

PRs welcome. Please keep the project stdlib-only (no `requirements.txt`) and
make sure `python -m unittest discover -v` stays green.
