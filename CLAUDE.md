# CLAUDE.md

Project context for Claude Code working in this repo.

## What this is

A tiny GitHub-Actions cron that fetches the Aare river water temperature from
the aare.guru API and posts to a Slack incoming webhook. Two posts per weekday
(11:30 and 16:00 Swiss summer time, Mon–Fri only). Defaults to Bern; supports
multiple cities via the `CITIES` env var. Full user-facing spec lives in
the README.

## Hard constraints

- **Stdlib only.** No `requirements.txt`, no `pip install`, no third-party
  imports. Anything new must work with Python 3.12 stdlib.
- **English only in user-visible strings.** The upstream API returns Bernese
  German text (`temperature_text`, `forecast2h_text`, etc.) — do not surface
  them in the Slack message. There is a test guarding this.
- **Attribution footer is required.** aare.guru's usage terms ask integrators
  to link back to aare.guru and BAFU. The footer context block is mandatory,
  not cosmetic — there is a test for it.

## File map

| File | Purpose |
| --- | --- |
| `aare_slack.py` | Single-file script. Pure functions (`parse_cities`, `build_api_url`, `classify_water`, `classify_weather`, `classify`, `build_bar`, `forecast_trend`, `build_datetime_str`, `build_payload`) are tested. IO (`fetch_aare_data`, `post_to_slack`) is not. |
| `test_aare_slack.py` | `unittest` tests for the pure functions. Run via `make test` or `python -m unittest discover -v`. |
| `.github/workflows/aare.yml` | Cron + manual-dispatch workflow that posts to Slack. Uses repo secret `SLACK_WEBHOOK_AARE`. Weekdays only (`* * 1-5`). |
| `.github/workflows/test.yml` | Runs tests on every PR and push to `main`. |
| `Makefile` | `make test`, `make run`, `make lint`, `make help`. Uses tabs (Make requirement). |
| `LICENSE` | MIT for source. Note: upstream aare.guru/BAFU data remains non-commercial. |

## API gotcha — don't get it wrong again

The aare.guru `/v2018/current` response is **nested**:

```json
{
  "aare":    { "temperature": 18.6, "flow": 190, "forecast2h": 18.9, ... },
  "weather": { "current": { "tt": 26.6, ... }, ... }
}
```

So:
- Water temp  → `data["aare"]["temperature"]`
- Flow        → `data["aare"]["flow"]`
- 2h forecast → `data["aare"]["forecast2h"]`
- Air temp    → `data["weather"]["current"]["tt"]`

Don't write `data["flow"]` or `data["atmp"]` — those keys don't exist at the
top level.

## Slack payload shape

One attachment with five blocks, always in this order:

1. `headline` — header block (big, plain_text): emoji + city + temp + date/time
2. `slogan` — section block (mrkdwn, bold): the tier-signature line from `SLOGAN_MATRIX`
3. (no `block_id`) — divider
4. `details` — section block (mrkdwn): outlook (weather + air), forecast,
   flow, bar. Lines are joined with `\n\n` for paragraph spacing.
5. `attribution` — context block (mrkdwn): `📡 Data: <aare.guru> · <BAFU>`
   — mandatory per aare.guru's terms

Attachment `color` comes from `classify_water` (per-degree color escalation).

Tests find blocks by `block_id` (see `_find_block` / `_text_of` helpers).
If you add a block, give it a stable `block_id` so tests don't have to count
indices.

## Classification — water × weather matrix

The slogan is picked from `SLOGAN_MATRIX[(water_tier, weather_tier)]`:

- **Water tier** comes from `classify_water(temp)`: `frigid`, `cold`, `cool`,
  `swim`, `perfect`, `hot`. Per-degree color/emoji escalation lives in
  `_WATER_TIERS`.
- **Weather tier** comes from `classify_weather(data, now)`: `sunny`,
  `cloudy`, `rainy`, `unknown`. Reads `weather.current.rr` for live rain
  and the matching period of `weather.today` (`v` morning <12h, `n`
  afternoon 12–17h, `a` evening 17h+). **Hard rule: rain → no swim** — any
  of (current rain > 0, period rain > 0, period `rrisk` ≥ 50) marks the
  message as rainy and the slogan blocks the swim recommendation.

When adding new combinations: keep slogans English-only (no Bernese German),
unique across the whole matrix, and ASCII-safe in the rain-blocking detection
(the test uses `\b` word boundaries so don't make the negative signal `"no"`
collide with `"now"`).

## Configuration

- `SLACK_WEBHOOK_URL` (required) — set in CI as repo secret `SLACK_WEBHOOK_AARE`.
- `CITIES` (optional) — comma-separated city slugs, default `bern`. Each city
  produces one Slack message. Failures on individual cities don't stop the rest.
- `AARE_APP` (optional) — value of the `app` query param sent to aare.guru.
  Default `aare-slack-bot`. aare.guru asks every integrator to identify itself.
- `AARE_VERSION` (optional) — value of the `version` query param. Default `1`.

## Exit codes

The script uses distinct exit codes so workflow logs are diagnostic:
- `1` — config error (missing `SLACK_WEBHOOK_URL`)
- `2` — fetch / parse error (API unreachable, bad JSON, missing field)
- `3` — Slack post error

When multiple cities are configured, the exit code is the **max** of all
per-city errors. Don't collapse them.

## Time / timezone

- Display time is `datetime.now(ZoneInfo("Europe/Zurich"))` — handles DST.
- Cron in `.github/workflows/aare.yml` is in UTC, tuned for CEST (UTC+2),
  four posts per weekday: `23 9` → ~11:30 · `53 12` → ~15:00 · `23 14` →
  ~16:30 · `23 15` → ~17:30 (all CH summer). In winter (CET = UTC+1), posts
  arrive 1 hour earlier — accepted trade-off, since the water is too cold to
  swim anyway.
- Minutes are deliberately off the `:00`/`:30` marks. GitHub's scheduler
  queues those popular slots behind everyone else's and routinely delays them
  60min+ or drops them entirely (this is why the old `30 9` 11:30 post kept
  not firing). Off-peak minutes a few min before each target fire far more
  reliably and land near the intended time once GitHub's usual delay applies.
- Tests pass an explicit `now=FIXED_NOW` to `build_payload` for determinism.
  Always do the same when adding new payload tests.

## Common commands

```bash
make test                                  # run unit tests
make help                                  # list targets
python -m unittest discover -v             # equivalent to `make test`

# Dry run against the live API (no Slack post):
python3 -c "from aare_slack import fetch_aare_data, build_payload, build_api_url; \
  import json; print(json.dumps(build_payload(fetch_aare_data(build_api_url('bern'))), indent=2, ensure_ascii=False))"
```

## GitHub Actions notes

- Cron is best-effort, 5–15 min late under load is normal.
- Scheduled workflows only fire from the **default branch** (`main`).
- `permissions: contents: read` and `timeout-minutes: 5` are set on both
  workflows — keep them when adding new jobs.

## Adding new features

- New pure helper → add it to `aare_slack.py`, add tests with a fixed `now`.
- Don't introduce a config file or env-var soup. If a feature needs config,
  weigh whether it earns the complexity first.
- Keep the message dense and skimmable. Slack readers scan, not read.
- Emojis are welcome but should add information (tier, type of metric) rather
  than just decorate.
