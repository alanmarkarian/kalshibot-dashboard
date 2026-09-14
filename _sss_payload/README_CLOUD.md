# ShortSqueezeScreener (cloud)

Private repo. Premarket + Midday GitHub Actions. Publishes **only** `squeeze.html` to `alanmarkarian/kalshibot-dashboard`.

## Secrets (repo Settings → Secrets → Actions)
- `ADANOS_API_KEY` — X/Adanos only (from MarkarianPC Adanos config; not KalshiBot trading keys)
- `PAGES_GITHUB_TOKEN` — PAT with `contents:write` on `alanmarkarian/kalshibot-dashboard` (same role as `C:\KalshiBot\github_token.txt` for Pages push; do not rotate Kalshi trading keys)

## Book
Commit `data/positions.csv` in this repo (or sync). Live HTZ fills go here.

## Local PC
MarkarianPC remains a backup. Windows Task Scheduler can stay or be disabled after Actions are proven.
