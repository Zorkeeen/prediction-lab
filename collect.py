#!/usr/bin/env python3
"""Snapshot Polymarket + Kalshi: top markets by volume, plus everything
trading >= 0.90 (sure-thing decay study universe).

Per snapshot (run every 2h by Actions):
  data/pm/YYYYMMDD.csv.gz      one row per Polymarket market: metadata,
                               yes-token book top 5 levels each side
  data/kalshi/YYYYMMDD.csv.gz  one row per Kalshi market: bid/ask/last/
                               volume/OI (+ book top level)

Files are per-day gzip; each run appends a gzip member (valid concatenation).
Stdlib only.
"""
import csv, gzip, io, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; prediction-lab/1.0)"
TOP_N = 150
HI_PX = 0.90
NOW = datetime.now(timezone.utc)
TS = NOW.strftime("%Y-%m-%dT%H:%M")
DAY = NOW.strftime("%Y%m%d")


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


def append_gz(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    if not path.exists():
        w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fieldnames})
    with open(path, "ab") as f:
        f.write(gzip.compress(buf.getvalue().encode()))


def levels(side, n=5):
    out = []
    for lvl in (side or [])[:n]:
        if isinstance(lvl, dict):
            out.append(f"{lvl.get('price')}:{lvl.get('size')}")
        else:
            out.append(f"{lvl[0]}:{lvl[1]}")
    return "|".join(out)


def polymarket():
    markets, offset = [], 0
    while offset < 500:
        d = get("https://gamma-api.polymarket.com/markets?active=true"
                f"&closed=false&order=volume24hr&ascending=false"
                f"&limit=100&offset={offset}")
        time.sleep(0.2)
        if not d:
            break
        markets += d
        offset += 100
    picked, seen = [], set()
    for i, m in enumerate(markets):
        if m.get("id") in seen:
            continue
        try:
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
        except (ValueError, TypeError):
            prices = []
        hi = prices and max(prices) >= HI_PX and max(prices) < 1.0 \
            and float(m.get("volume24hr") or 0) > 500
        if i < TOP_N or hi:
            seen.add(m.get("id"))
            picked.append(m)
    rows = []
    for m in picked:
        try:
            tok = json.loads(m.get("clobTokenIds") or "[]")[0]
        except (ValueError, IndexError):
            tok = None
        book = get(f"https://clob.polymarket.com/book?token_id={tok}") if tok else None
        time.sleep(0.12)
        # CLOB books list bids ascending / asks descending; best = last element
        bids = sorted(book.get("bids", []), key=lambda x: -float(x["price"]))[:5] if book else []
        asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))[:5] if book else []
        rows.append({
            "ts": TS, "id": m.get("id"), "slug": (m.get("slug") or "")[:80],
            "question": (m.get("question") or "")[:160],
            "end_date": (m.get("endDate") or "")[:10],
            "outcomes": (m.get("outcomes") or "")[:60],
            "outcome_prices": (m.get("outcomePrices") or "")[:60],
            "vol24h": m.get("volume24hr"), "liquidity": m.get("liquidityNum"),
            "yes_bids": levels(bids), "yes_asks": levels(asks),
        })
    append_gz(BASE / "data" / "pm" / f"{DAY}.csv.gz",
              ["ts", "id", "slug", "question", "end_date", "outcomes",
               "outcome_prices", "vol24h", "liquidity", "yes_bids", "yes_asks"],
              rows)
    print(f"polymarket: {len(rows)} markets")
    return len(rows)


def kalshi():
    markets, cursor = [], ""
    for _ in range(6):
        d = get("https://api.elections.kalshi.com/trade-api/v2/markets"
                f"?limit=1000&status=open&cursor={cursor}")
        time.sleep(0.3)
        if not d or not d.get("markets"):
            break
        markets += d["markets"]
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    markets.sort(key=lambda m: -(m.get("volume_24h") or 0))
    picked = markets[:TOP_N] + [
        m for m in markets[TOP_N:]
        if (m.get("last_price") or 0) >= HI_PX * 100
        and (m.get("volume_24h") or 0) > 100]
    rows = [{
        "ts": TS, "ticker": m.get("ticker"),
        "title": (m.get("title") or "")[:160],
        "close_time": (m.get("close_time") or "")[:16],
        "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
        "no_bid": m.get("no_bid"), "no_ask": m.get("no_ask"),
        "last": m.get("last_price"), "vol24h": m.get("volume_24h"),
        "volume": m.get("volume"), "oi": m.get("open_interest"),
    } for m in picked]
    append_gz(BASE / "data" / "kalshi" / f"{DAY}.csv.gz",
              ["ts", "ticker", "title", "close_time", "yes_bid", "yes_ask",
               "no_bid", "no_ask", "last", "vol24h", "volume", "oi"],
              rows)
    print(f"kalshi: {len(rows)} markets (of {len(markets)} open)")
    return len(rows)


if __name__ == "__main__":
    npm = polymarket()
    nk = kalshi()
    if npm + nk < 50:
        sys.exit(f"too few markets captured ({npm}+{nk}) — treating as failure")
