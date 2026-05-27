# aare-slack-bot

Posts the current Aare river water temperature in Bern to `#aare` twice daily:

- **11:30 CH** — lunch decision
- **16:00 CH** — Feierabend decision

Each message includes a 2-hour forecast, a trend arrow (↗ / → / ↘), a short
tagline, and an attribution footer linking back to aare.guru and BAFU.
Swim-worthy temperatures (≥ 18 °C) get a coloured header so they stand out.

## Sample output

Cold (no header):

```
🥶 Aare Bern: 13.1°C   ↘ 2h: 12.8°C
██░░░░░░░░░░░░░░  10°—26°
Air 18°C · Flow 195 m³/s
Toes-only territory.
Data: aare.guru · BAFU
```

Warm (with header):

```
🌊 TIME TO SWIM!
🏊 Aare Bern: 19.2°C   ↗ 2h: 19.8°C
█████████░░░░░░░  10°—26°
Air 24°C · Flow 142 m³/s
Officially swim-worthy. Get in.
Data: aare.guru · BAFU
```

## Temperature tiers

| Range         | Emoji | Header             | Slack color | Tagline                              |
|---------------|-------|--------------------|-------------|--------------------------------------|
| ≥ 22 °C       | 🔥    | BATHTUB MODE       | red         | Spaghetti water. Bring sunscreen.    |
| 20 – 21.9 °C  | ☀️    | PERFECT FOR A SWIM | green       | Grab the towel, this is the day.     |
| 18 – 19.9 °C  | 🏊    | TIME TO SWIM!      | green       | Officially swim-worthy. Get in.      |
| 15 – 17.9 °C  | 😬    | (none)             | orange      | Brave-souls only. Quick dip at most. |
| 12 – 14.9 °C  | 🥶    | (none)             | blue        | Toes-only territory.                 |
| < 12 °C       | 🧊    | (none)             | blue        | Hard pass. Coffee weather.           |

Forecast arrow uses a ±0.3 °C threshold:
`↗` warming, `↘` cooling, `→` steady.

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
make run                                   # post to Slack
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

## Data source & license

Data from the [aare.guru API](https://aare.guru/), an unofficial public API by
Bureau für digitale Existenz, sourced from the
[Bundesamt für Umwelt (BAFU)](https://www.hydrodaten.admin.ch/).
**Non-commercial use only.** Every Slack message includes a footer linking back
to both sources, per aare.guru's usage terms.

## Future ideas (not implemented)

- **Skip when very cold** — add `if temp < 12: return 0` early in `main()` to
  silence winter posts entirely
- **Multiple cities** — accept a comma-separated env var like `CITIES=bern,thun`
- **Change detection** — only post when the temperature has moved > 0.5 °C since
  the last run (would need to persist state, e.g. in a GitHub Actions cache)
- **Weekend-only mode** — restrict the schedule to Sat/Sun in winter
