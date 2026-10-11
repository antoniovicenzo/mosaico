# Mosaico

An always-on agent that watches Instagram reels about *"making money with AI, no investment needed"*,
pulls out the actual methods, throws away the scams, **backtests** the rest against real-world evidence,
and turns the best ones into small, cheap experiments. It also produces the materials for each
experiment and learns from your real results.

```
reels ──► extract ideas ──► merge duplicates ──► compliance gate ──► web research ──► economic model
                                                      │ (reject)                              │
                                                      ▼                                       ▼
                                               "Rejected" list               Monte Carlo backtest (4,000 runs)
                                                                                              │
                     calibration ◄── your logged results ◄── experiment ◄── score & rank ◄────┘
                    (forward test)                         (assets + your to-dos)
```

## What to expect

Read this before you run it.

- **It will not make money unattended.** Every legitimate way to earn money online needs a person at some
  point: creating seller accounts (identity/KYC, tax details), publishing, talking to customers, receiving
  payment. Platforms also ban accounts that automate these steps. Mosaico runs everything else around the
  clock (finding ideas, research, backtesting, writing product files, listings, pitches and scripts) and
  gives you a short to-do list. Plan on **a few hours per week** from you.
- **Most reel ideas are bad.** Reels are survivorship bias plus course funnels. The backtest is built to say
  so: it reports a *hype ratio* (claimed income ÷ simulated typical income), and ratios of 10–100× are normal.
  Expect the first real euro in weeks, not days, and expect most experiments to be killed. Killing them
  quickly is how the system works.
- **"Backtesting" a business idea** can't replay price history like a trading strategy can. Here it means:
  research what actually happens to beginners (independent sources, marketplace data, policies), turn that
  into an economic model with uncertainty ranges, and simulate thousands of 90-day attempts using *your*
  available hours. Real experiment results then recalibrate the model per category (forward-testing).
- **Running costs are capped at about $15 in total.** Ideas need no investment, but the agent pays for Claude
  API calls. The spending runs in phases automatically (see [Budget phases](#budget-phases)). Before it either
  pays for itself or switches itself off, it spends at most **$12 + $3 = $15** (≈ €14).

## What it refuses to do

The compliance gate (regex rules first, then an LLM review) rejects, and never researches or plans:
fake reviews or engagement, spam and bulk unsolicited DMs or emails, ban evasion and multi-accounting,
academic cheating ("humanizers" that get past Turnitin), trading signals and "AI trading bots",
MLM schemes, impersonation and deepfakes, copyright reuploads, unlicensed financial/medical/legal advice,
and anything that needs upfront cash above your limit. Legitimate ideas that carry real policy risk
(mass-produced AI content, KDP disclosure rules, cold outreach under GDPR and Spain's LSSI) pass with a
**caution** flag and a "safe variant" that the plan has to follow.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[transcribe,dev]"      # or just `pip install -e .` for the core only
mosaico init                             # writes config.toml; edit [profile] and [budget]
export ANTHROPIC_API_KEY=sk-ant-...
export APIFY_TOKEN=apify_api_...         # optional, enables automatic Instagram scraping

mosaico ingest --seed                    # 12 sample reels, so you can try everything immediately
mosaico run --once                       # one full tick: extract → research → plan → execute
mosaico run                              # run continuously (Ctrl+C stops it cleanly)
mosaico budget                           # current phase, what's been spent, what's allowed
```

Open `data/report.html` for the dashboard. It shows ranked opportunities with backtest stats,
research memos and sources, your to-dos, experiments, rejected ideas, and recent activity.

### Getting reels in

| Source | How | Notes |
|---|---|---|
| **Apify** (automatic) | set `APIFY_TOKEN`; hashtags in `[sources]` | Scraping runs on Apify's infrastructure, not your IP or account. Free tier is enough to start. |
| **URL inbox** (manual) | paste reel links into `data/inbox.txt` or `mosaico ingest --url <url>...` | Uses `yt-dlp`. If Instagram blocks anonymous access, export cookies from your own browser to `cookies.txt` and set `cookies_file`. |
| **File import** | `mosaico ingest --file reels.json` | JSON, JSONL or CSV with `caption`/`transcript`/`url`/`views`; Apify dataset exports work as-is. |
| **Samples** | `mosaico ingest --seed` | Synthetic reels for testing, including several the gate must reject. |

With `pip install 'mosaico[transcribe]'` (plus `ffmpeg`), reels are transcribed locally with
faster-whisper. This matters because most reels say the method out loud and only put hype in the caption.

Automated collection from Instagram is against Instagram's Terms of Use, whichever tool does it.
Keep volumes modest, never use the account you care about for it, and use the manual inbox if you'd
rather stay clearly within the rules.

### Your loop

```bash
mosaico tasks                                   # what needs a human: publish, create account, contact…
mosaico done 42 --note "listing live: <url>"    # tick one off
mosaico log 7 --revenue 9 --hours 2 --metric visits=140 sales=1   # log results (by opportunity id or slug)
mosaico show 7                                  # research, backtest, experiment status, generated files
```

Each experiment has success and kill criteria. When the success criteria are met, the opportunity moves
to **scaling** and gets a next-stage plan. When the deadline passes without success, it is **killed**. Either
way, actual vs. predicted revenue updates that category's calibration factor, so future backtests in that
category become more realistic.

Generated assets (product content, listing copy, outreach drafts, scripts) are written to
`workspace/<opportunity>/exp-<n>/`. Each folder has a `NOTES.md` listing what to check before you use it.

### Other commands

`mosaico status` · `budget` · `resume` · `ls [--all]` · `research [--id N]` · `plan [--id N]` · `execute` · `report` · `spend` ·
`retry` (requeue parked items) · `rescore` (re-run simulations with the latest calibration)

## Running it 24/7

- **Docker**: `docker compose up -d --build`. Put `ANTHROPIC_API_KEY` etc. in a `.env` file next to
  `docker-compose.yml`. The dashboard is served at <http://localhost:8080/report.html>.
- **systemd** on a small VPS or a Raspberry Pi: see `deploy/mosaico.service`.
- Set `MOSAICO_WEBHOOK_URL` to a Slack or Discord incoming webhook to receive a daily digest, plus a ping
  whenever a new experiment starts or one finishes.

The loop ticks every 60 s. During discovery, ingest runs every 6 h, and each tick processes a little of
every stage. When a day's allowance is used up, the stages that call Claude wait until midnight UTC while
the free stages keep running. The webhook announces the end of discovery (with the ranking) and any automatic pause.
A file lock prevents two agents from running on the same data. Items that fail because of their own
content are parked (`mosaico retry` requeues them). Items that fail because of outages, auth or rate
limits stay queued and are retried on a later tick.

## Budget phases

All automatic. Check where you are with `mosaico budget`.

| phase | when | what runs | spend limit |
|---|---|---|---|
| **discovery** | first 7 days | ingest reels, extract, compliance, web research + backtest, then experiments from day 4 | **$12 total**, paced evenly (~$1.70/day). Ends early if spent |
| **maintenance** | after discovery | no new reels or research; only experiment plans, asset writing, tracking | $0.30/day, **$3 per 30 days** |
| **paused** | 30 days into maintenance, if logged revenue < total API spend | nothing that costs money (dashboard and logging still work) | $0 until `mosaico resume` |

Every call is checked against its *worst-case* cost (`max_tokens` bounds the output, thinking included)
before it is sent, so the caps hold even for the last call of a day. Rough real costs: extracting a reel
≈ $0.001, researching one idea ≈ $0.15–0.40 including web searches, an experiment plan ≈ $0.10, and an
asset ≈ $0.05–0.25. One discovery week is enough to backtest roughly 25–40 ideas. All limits are in
`[budget]` in `config.toml`. For another discovery round later, raise `discovery_usd`/`discovery_days`
and delete the `discovery_ended_on` row (`sqlite3 data/mosaico.db "DELETE FROM kv WHERE key='discovery_ended_on'"`).

Two extra safety nets are worth setting: a monthly spend limit in the Anthropic Console, and Apify's free
plan, which simply stops when its monthly credit is used.

## How scoring works

Each opportunity's economic model (reach, conversion, price, ramp time, hours, churn, platform risk,
all as low/likely/high ranges) is simulated 4,000 times over 90 days with your `hours_per_week`.
The score (0–100) is a weighted mix that you can adjust in `[strategy.weights]`:

| component | meaning |
|---|---|
| `ev_per_hour` | expected revenue per hour worked ($15/h ⇒ 0.5) |
| `p_any_revenue` | share of simulated attempts that earn anything |
| `speed` | median days to first dollar |
| `evidence` | quality of independent evidence found |
| `automation` | how much of the work the agent can do |
| `fit` | match with your skills and languages |
| `risk` | saturation, policy caution, and platform shutdown risk |

## Layout

```
mosaico/
  sources/      apify.py · inbox.py (yt-dlp) · files.py · seed.py
  ingest.py     collect + transcribe        extract.py   ideas + clustering
  compliance.py rules + LLM gate            research.py  web research → economic model
  simulate.py   Monte Carlo backtest        scoring.py   0-100 score, hype ratio
  planner.py    experiment design           executor.py  asset generation (sandboxed paths)
  tracker.py    outcomes, verdicts, calibration
  scheduler.py  the always-on loop          report.py    dashboard + digest
  llm.py        Claude API, budget, cost accounting      cli.py
tests/          run with `pytest` (no API key needed)
```

## Notes

- Models: Claude Haiku 5.5 handles bulk reel parsing. Claude Opus 5.5 with web search handles
  research, compliance, planning and asset writing. Server-side refusal fallbacks are on; set
  `models.server_fallbacks = false` if you route through Bedrock, Vertex or Foundry.
- Reel text is treated as untrusted input. Prompts tell the model never to follow instructions found inside it.
- Income from these activities is taxable. In Spain, regular activity generally means registering as
  *autónomo*, so check before you scale.
