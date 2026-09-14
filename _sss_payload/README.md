# Short Squeeze Screener

Standalone stock screener tuned on the DNA of **GME, SPRT, BBBY, AMC, and WEN** — heavy short interest vs float, tight float, accelerating retail chatter, options gamma, and a spark.

This is a **watchlist / early-warning system**, not a buy-every-signal bot.  
Fuel gets a name onto the board; **ignition is the entry**; the **pre-set exit is the edge**.

---

## Quick start (Windows)

1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/) — check **Add Python to PATH**.
2. Double-click `run.bat`  
   *(or from this folder: `python scan.py`)*
3. Browser opens `dashboard.html` with the ranked board.

**Useful flags**

```text
python scan.py              Full scan (quotes + social + X + options + consensus)
python scan.py --quick      Skip options chains (much faster)
python scan.py --max 25     Smaller SI universe
python scan.py --no-x       Skip Adanos X/FinTwit call (saves free quota)
python scan.py --no-consensus  Skip dual compare vs Claude
python scan.py --consensus-only  Re-compare last scan vs Claude only
python scan.py --backtest   Validate scoring on GME/SPRT/BBBY/WEN/AMC cases
python scan.py --no-browser Console only
```

### X / FinTwit + dual consensus

Each scan pulls **one** Adanos `/v1/compare` batch (≤10 high-fuel tickers) using the key shared with the Claude screener (free tier ~250/mo). Adds buzz, trend, acceleration, and X entry/exit markers.

Then scores Claude’s `snapshot.json` with **their** `screener.py` and writes:

- `consensus.html` — DUAL TRIGGER / WATCH / IGNITION / BOARD  
- Desktop shortcut: **Squeeze Dual Consensus**

Agreement = stronger signal by design (different pipelines).

### Event-study trigger mine

Discover pre-move price/volume features the live score may miss:

```text
python event_study.py
```

Writes `event_study_report.html` + `data/event_study_results.json`.  
Cases live in `data/event_cases.json` (add squeezes/controls over time).  
Promising tape flags (BB squeeze, reclaim 20-MA, near 20d high, higher lows) are also applied live as soft `TAPE …` flags — pair setups can hit the trigger stack. **No composite reweight** until the ledger confirms edge.


## Automatic runs (Task Scheduler)

Unattended runner: `run_auto.bat` (no browser, logs to `data\logs\`).

```powershell
cd C:\Users\alanm\ShortSqueezeScreener
.\schedule_setup.ps1 install          # weekdays 7:36 full + 12:33 quick
.\schedule_setup.ps1 install -PremarketOnly
.\schedule_setup.ps1 status
.\schedule_setup.ps1 remove
```

Custom times (local PC clock):

```powershell
.\schedule_setup.ps1 install -PremarketTime 07:30 -MiddayTime 12:45
```

Tasks registered: `ShortSqueezeScreener-Premarket`, `ShortSqueezeScreener-Midday`.  
PC must be on (and preferably unlocked/logged in) at those times. Open `dashboard.html` anytime after a run.

---

## What it measures

| Pillar | Weight | Markers |
|--------|--------|---------|
| **Mechanics (fuel)** | 50 | Short interest % of float, float tightness, days-to-cover, volume spike |
| **Social** | 50 | WSB rank, mention velocity vs 24h ago, upvote heat, brand/meme DNA, StockTwits early trend |
| **Gamma (bonus)** | +12 | Near-dated (≤14d) call OI vs float, call volume share, call premium vs mcap, ATM IV |

**Tiers**

| Score | Tier | Meaning |
|------:|------|---------|
| ≥70 | RED HOT | Likely already moving |
| 55–69 | WATCH | Live setup — money tier |
| 40–54 | MONITOR | Fuel present, no spark yet |
| &lt;40 | BACKGROUND | Low priority |

**Fuel vs ignition**

- **Fuel** (slow) = SI points + float points — powder keg  
- **Ignition** (fast) = volume + WSB velocity + gamma + pre-catalyst — *what can move this week*

### ENTRY markers (before / as it runs)

Act-now **TRIGGER STACK** (surfaces above the list regardless of tier):

1. WSB velocity ≥5x **and** volume ≥3x  
2. Chatter accelerating **into** a dated catalyst (earnings ≤2 days)  
3. **Gamma ramp** (weekly calls bid hard — the GME/SPRT options tell)  
4. Hard-to-borrow (fee ≥20%) + volume spike + SI ≥20%

Also shown per name (setup quality, not auto-buy):

- Heavy SI (≥30%) / elevated SI (≥20%)  
- Tight float (&lt;30M)  
- Days-to-cover ≥4  
- WSB chatter accelerating  
- Borrow fee elevated  
- Day-over-day: borrow fee rising, borrow avail collapsing, call OI building, velocity accelerating, tier jump  

**Rule of thumb:** do not buy fuel alone. Wait for an ignition transition.

### EXIT markers (when to get out)

Doctrine (SPRT lesson): **sell half at 3–4x, trail the rest.**

Hard exits (any one is enough):

| Signal | Why |
|--------|-----|
| **Dilution filing** (S-1 / S-3 / 424B / EFFECT / 8-K 3.02) | Biggest single red flag — sell first, ask later |
| Social velocity &lt;1x | Crowd leaving |
| Parabolic move on ≥5x volume | Distribution window; squeezes often die in 1–3 sessions |
| Borrow fee normalizing / availability rebounding | Shorts done covering |
| Options flow flips to puts (call share ≤0.45) | Gamma unwinding |
| Price ≥3x entry | Scale out per doctrine |

Paper ledger also uses **−25% stop** and **14-day time stop** on trigger trades so you can measure edge without capital.

Positions in `data/positions.csv` get an **EXIT PRESSURE** panel: GREEN / AMBER / RED.

---

## Files you edit

| File | Purpose |
|------|---------|
| `data/watchlist.csv` | Always-screened tickers |
| `data/short_overrides.csv` | Manual SI%, float, borrow fee (from Finviz / Fintel / shortinteresttracker) — **biggest accuracy upgrade** |
| `data/positions.csv` | Open positions for exit pressure (`ticker,entry,shares,stop,flag_price`) |

Example position row:

```csv
ticker,entry,shares,stop,flag_price
GRPN,26.85,100,22.00,26.85
```

---

## Data sources

| Source | Used for |
|--------|----------|
| Yahoo Finance (`yfinance`) | Price, volume, float, short % (when available), options gamma proxy |
| [ApeWisdom](https://apewisdom.io) | WSB rank / mentions / 24h velocity |
| StockTwits trending API | Early social breadth (pre-WSB) |
| highshortinterest.com | Best-effort &gt;20% SI universe scrape |
| Your CSV overrides | Ground-truth SI / float / borrow |

Free SI is often **bi-weekly and incomplete**. For serious use, paste Finviz/Fintel numbers into `short_overrides.csv`.

---

## Relationship to your Claude Meme Screener

You already have a richer daily pipeline under  
`OneDrive\Documents\Claude Meme Screener` (Adanos X-sentiment, EDGAR dilution, CBOE gamma via Chrome, scheduled Gmail drafts).

This folder is the **standalone, double-click version**: same scoring DNA, live fetch without Claude, runnable anytime.

Thresholds / weights here match the calibrated engine (GME ~79, SPRT ~67, BBBY ~65, WEN ignition ~61, boring control ~10). Do not thrash weights on anecdote — let `data/history.jsonl` + the paper ledger accumulate evidence.

---

## Outputs after each run

| File | What |
|------|------|
| `dashboard.html` | Interactive board (filters, click row for breakdown) |
| `data/last_snapshot.json` | Full scored universe |
| `data/history.jsonl` | Day-over-day series (transition flags) |
| `data/paper_trades.json` | Auto paper trades on triggers |

---

## Suggested daily routine

1. Pre-market: `run.bat` (or `--quick` if short on time)  
2. Read **EXIT PRESSURE** first if you have positions  
3. Scan **TRIGGER STACK** and **TOP IGNITION**  
4. Update `short_overrides.csv` when you see better SI/borrow numbers  
5. Only size real risk after the paper ledger shows edge over many closes  

Happy hunting.
