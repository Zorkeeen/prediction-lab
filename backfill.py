#!/usr/bin/env python3
"""Resumable backfill of ALL resolved Polymarket markets: metadata, final
outcome, and daily price history per market.

State in data/backfill_state.json (offset). Each invocation processes up to
BATCH markets then exits 0, so an Actions schedule can chip away in chunks
until done (state marks done=true, later runs exit immediately).

Output:
  data/backfill/resolved_meta.csv.gz       one row per resolved market
  data/backfill/hist_<offset>.csv.gz       daily candles (id, t, p)
"""
import csv, gzip, io, json, sys, time
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
OUT = BASE / "data" / "backfill"
STATE = BASE / "data" / "backfill_state.json"
UA = "Mozilla/5.0 (compatible; prediction-lab/2.0)"
BATCH = 4000          # markets per invocation (~70-90 min)
# The gamma API errors past offset ~2400, so plain pagination can never reach
# more than ~2k markets. We walk month windows instead (end_date_min/max),
# newest first, each window paginated within the ceiling.
FIRST_MONTH = "2024-01"   # Polymarket retains outcomes+candles from ~2025;
                          # earlier months return ["0","0"] and empty history
OFFSET_CEILING = 2000


def get(url, retries=3):
    for i in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"}),
                         timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == retries - 1:
                print(f"WARN {url.split('?')[0]}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))


def append_gz(path, fieldnames, rows, force_header=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    if force_header or not path.exists():
        w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fieldnames})
    with open(path, "ab") as f:
        f.write(gzip.compress(buf.getvalue().encode()))


def month_windows(first_month):
    """(start, end) ISO date strings, newest month first."""
    y0, m0 = (int(x) for x in first_month.split("-"))
    today = date.today()
    out = []
    y, m = today.year, today.month
    while (y, m) >= (y0, m0):
        start = date(y, m, 1)
        end = date(y + (m == 12), (m % 12) + 1, 1)
        out.append((start.isoformat(), end.isoformat()))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    done_windows = set(state.get("done_windows", []))
    if state.get("done"):
        print("backfill already complete")
        return

    windows = [w for w in month_windows(FIRST_MONTH) if w[0] not in done_windows]
    if not windows:
        state["done"] = True
        STATE.write_text(json.dumps(state))
        print("all month windows complete")
        return

    meta_rows, hist_rows, n = [], [], 0
    finished = []
    for start, end in windows:
        if n >= BATCH:
            break
        offset = 0
        got = 0
        while offset < OFFSET_CEILING and n < BATCH:
            d = get("https://gamma-api.polymarket.com/markets?closed=true"
                    f"&end_date_min={start}&end_date_max={end}"
                    f"&limit=100&offset={offset}")
            time.sleep(0.12)
            if not d:
                break
            for m in d:
                mid = m.get("id")
                meta_rows.append({
                    "id": mid, "slug": (m.get("slug") or "")[:80],
                    "question": (m.get("question") or "")[:160],
                    "created": (m.get("createdAt") or "")[:10],
                    "end_date": (m.get("endDate") or "")[:10],
                    "outcomes": (m.get("outcomes") or "")[:60],
                    "final_prices": (m.get("outcomePrices") or "")[:60],
                    "volume": m.get("volume"),
                })
                try:
                    tok = json.loads(m.get("clobTokenIds") or "[]")[0]
                except (ValueError, IndexError):
                    tok = None
                stale = (m.get("outcomePrices") or "").replace(" ", "") == '["0","0"]'
                if tok and not stale and float(m.get("volume") or 0) > 100:
                    h = get("https://clob.polymarket.com/prices-history"
                            f"?market={tok}&interval=max&fidelity=1440",
                            retries=1)
                    time.sleep(0.08)
                    for pt in (h or {}).get("history", []):
                        hist_rows.append({"id": mid, "t": pt.get("t"),
                                          "p": pt.get("p")})
                n += 1
                got += 1
            if len(d) < 100:
                finished.append(start)
                break
            offset += 100
        else:
            finished.append(start)
        print(f"  {start}: {got} markets")

    tag = (finished[0] if finished else windows[0][0]).replace("-", "")
    append_gz(OUT / "resolved_meta.csv.gz",
              ["id", "slug", "question", "created", "end_date", "outcomes",
               "final_prices", "volume"], meta_rows)
    append_gz(OUT / f"hist_{tag}.csv.gz", ["id", "t", "p"], hist_rows,
              force_header=True)
    state["done_windows"] = sorted(done_windows | set(finished))
    state["done"] = len(state["done_windows"]) >= len(month_windows(FIRST_MONTH))
    STATE.write_text(json.dumps(state))
    print(f"backfill: {n} markets, {len(hist_rows)} history points, "
          f"{len(state['done_windows'])} windows done, done={state['done']}")


if __name__ == "__main__":
    main()
