# Prediction Lab

Hourly full-universe snapshots of Polymarket + Kalshi via public APIs (no auth):
EVERY active market on both venues (no filters), plus resumable backfill of trading >= 0.90
(sure-thing decay universe). Polymarket rows carry top-5 order book levels
per side; Kalshi rows carry bid/ask/last/volume/open interest.

Data: `data/pm/YYYYMMDD.csv.gz`, `data/kalshi/YYYYMMDD.csv.gz` (per-day
gzip, one member appended per run).

Research targets: longshot bias, near-certainty decay harvesting,
cross-venue arb, coherence vs real markets (e.g. Fed markets vs futures).

## Execution layer (added 2026-08-17)

The hourly sweep is too coarse for the edges that matter (they resolve in
hours). Added:

- `watchlist.py` — every hour, builds the set of markets resolving within 48h
  or with a **UMA resolution already proposed**, then polls their full books
  every 5 minutes for 50 minutes. Captures best bid/ask, spread, and depth in
  USD on both sides. This is the dataset that answers: how fast does a stale
  near-certain price converge, and is there time to be filled passively?
  Output: `data/watch/YYYYMMDD.csv.gz`
- `collect.py` now stores the **full end timestamp** (not just the date — for
  a 1-day market the hour changes annualised ROI by 5x), `uma_status`,
  `resolution_source` and `uma_bond`.
- `data/market_rules.csv` — resolution criteria text per market, written once.
  The biggest tail risk is UMA resolving on a wording technicality, and the
  wording lives here.
