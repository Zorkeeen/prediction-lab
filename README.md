# Prediction Lab

2-hourly snapshots of Polymarket + Kalshi via public APIs (no auth):
top ~150 markets by 24h volume per venue, plus everything trading >= 0.90
(sure-thing decay universe). Polymarket rows carry top-5 order book levels
per side; Kalshi rows carry bid/ask/last/volume/open interest.

Data: `data/pm/YYYYMMDD.csv.gz`, `data/kalshi/YYYYMMDD.csv.gz` (per-day
gzip, one member appended per run).

Research targets: longshot bias, near-certainty decay harvesting,
cross-venue arb, coherence vs real markets (e.g. Fed markets vs futures).
