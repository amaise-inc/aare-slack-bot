# aare-slack-bot

[![Tests](https://github.com/amaise-inc/aare-slack-bot/actions/workflows/test.yml/badge.svg)](https://github.com/amaise-inc/aare-slack-bot/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

Posts the current Aare river water temperature in Bern to `#aare` twice every
**weekday** (Mon–Fri, no posts on weekends):

- **11:30 CH** — lunch decision
- **16:00 CH** — Feierabend decision

Each message shows the date + weekday, current temp with tier emoji, a 2-hour
forecast with trend arrow (↗ / → / ↘), air temp, flow, a short tagline, and an
attribution footer linking back to aare.guru and BAFU. Swim-worthy temperatures
(≥ 18 °C) get a coloured header so they really stand out.

## Sample output

The message has a clear visual hierarchy: **date + temperature** as the big
header, **bold slogan** below it, then **details** (forecast, air, flow, bar),
and a small **attribution** footer. The vertical attachment bar takes on the
tier color — deep blue when frozen through to deep red at record temps.

Warm (≥ 18 °C):

```
🏊 Aare Bern 19.2°C  ·  Wed 27 May · 11:30       (header — big & bold)
*🎉 SWIM-WORTHY — get in.*                       (slogan — bold)
──────────────────────────────────────────────
`█████████░░░░░░░`  10°—26°
↗ 2h: 19.8°C  ·  🌡️ Air 24°C  ·  💧 Flow 142 m³/s
📡 Data: aare.guru · BAFU                        (footer — small)
```

Cold (< 18 °C):

```
🥶 Aare Bern 13.1°C  ·  Wed 27 May · 11:30
*🥶 Numb fingers in seconds.*
──────────────────────────────────────────────
`██░░░░░░░░░░░░░░`  10°—26°
↘ 2h: 12.8°C  ·  🌡️ Air 18°C  ·  💧 Flow 195 m³/s
📡 Data: aare.guru · BAFU
```

## Temperature tiers — one per degree, hotter = more colorful

| Temp     | Emoji | Slogan                                                  | Color    |
|----------|-------|---------------------------------------------------------|----------|
| ≥ 24 °C  | 🔥    | 🔥🌶️🔥 OFF THE CHARTS — record territory!               | deep red |
| 23 °C    | 🔥    | 🔥🔥 Hotter than a hot tub.                             | red      |
| 22 °C    | 🔥    | 🔥🏊 BATHTUB MODE — spaghetti water.                    | red      |
| 21 °C    | ☀️    | 🏖️☀️ Linger after work.                                | orange-red |
| 20 °C    | ☀️    | 🌞 PERFECT — grab the towel.                            | orange   |
| 19 °C    | 🏊    | 🏊‍♀️🏊 Properly nice, go for it.                       | green    |
| 18 °C    | 🏊    | 🎉 SWIM-WORTHY — get in.                                | lime     |
| 17 °C    | 🤔    | 🤔 Almost swimmable, depending on bravery.              | yellow   |
| 16 °C    | 😬    | 🤐 Quick dip if you must.                               | orange   |
| 15 °C    | 😬    | 🥽 Brave-souls only.                                    | orange   |
| 14 °C    | 🥶    | 🧣 Cold enough to lie about it.                         | light blue |
| 13 °C    | 🥶    | 🥶 Numb fingers in seconds.                             | blue     |
| 12 °C    | 🥶    | 🦶 Toes-only territory.                                 | blue     |
| < 12 °C  | 🧊    | ☕ Hard pass — coffee weather.                          | deep blue |

Forecast arrow uses a ±0.3 °C threshold:
`↗` warming, `↘` cooling, `→` steady.

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

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Workflow fails with `ERROR: SLACK_WEBHOOK_URL environment variable not set` | Repo secret `SLACK_WEBHOOK_AARE` missing | Re-run `gh secret set SLACK_WEBHOOK_AARE` |
| Workflow fails with `Slack returned 403` or `404` | Webhook revoked or wrong URL | Regenerate the webhook in Slack, update the secret |
| Scheduled run is 5–15 min late | Normal GitHub Actions queue delay | Nothing to do — cron is best-effort |
| Posts arrive 1h earlier between Oct–Mar | CH switches to winter time (UTC+1), cron stays in UTC | Acceptable for a swim bot — water is too cold anyway |
| Posts land in the wrong channel | Webhook is bound to a channel other than `#aare` | Recreate the webhook with `#aare` selected, update the secret |

## Rotating the webhook

If the webhook URL is ever exposed (accidentally committed, shared in a chat,
posted in an issue), rotate it:

1. https://api.slack.com/apps → your App → Incoming Webhooks → Remove the
   compromised webhook → Add New Webhook to Workspace (target `#aare`)
2. `gh secret set SLACK_WEBHOOK_AARE` and paste the new URL
3. `gh workflow run aare.yml` to verify

## Data source

Data from the [aare.guru API](https://aare.guru/), an unofficial public API by
Bureau für digitale Existenz, sourced from the
[Bundesamt für Umwelt (BAFU)](https://www.hydrodaten.admin.ch/).
**Non-commercial use only.** Every Slack message includes a footer linking back
to both sources, per aare.guru's usage terms.

## Authors & credits

- **[Markus Baumgartner](https://github.com/markusbaumg)** — [amaise AG](https://amaise.com)
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
