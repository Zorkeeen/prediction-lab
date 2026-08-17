#!/usr/bin/env python3
"""Hourly full-universe snapshot of Polymarket + Kalshi (public APIs, no auth).

EVERY active market on both venues, no selection filters — completeness is the
point (no survivorship bias, full price distribution for longshot / near-
certainty studies). Polymarket rows include top-5 order book levels per side
(book fetched when the market has any 24h volume; dust books are empty anyway).

Output (per-day gzip, one member appended per run):
  data/pm/YYYYMMDD.csv.gz
  data/kalshi/YYYYMMDD.csv.gz

Stdlib only. Designed for GitHub Actions on a public repo (unlimited minutes).
"""
import csv, gzip, io, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; prediction-lab/2.0)"
NOW = datetime.now(timezone.utc)
TS = NOW.strftime("%Y-%m-%dT%H:%M")
DAY = NOW.strftime("%Y%m%d")
MAX_MARKETS = 20000


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
    return "|".join(f"{l.get('price')}:{l.get('size')}" for l in (side or [])[:n])


def polymarket():
    markets, offset = [], 0
    while offset < MAX_MARKETS:
        d = get("https://gamma-api.polymarket.com/markets?active=true"
                f"&closed=false&limit=100&offset={offset}")
        time.sleep(0.12)
        if not d:
            break
        markets += d
        if len(d) < 100:
            break
        offset += 100
    seen, rows = set(), []
    for m in markets:
        mid = m.get("id")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        vol24 = float(m.get("volume24hr") or 0)
        bids = asks = []
        if vol24 > 0:
            try:
                tok = json.loads(m.get("clobTokenIds") or "[]")[0]
            except (ValueError, IndexError):
                tok = None
            book = get(f"https://clob.polymarket.com/book?token_id={tok}",
                       retries=1) if tok else None
            time.sleep(0.1)
            if book:
                bids = sorted(book.get("bids", []),
                              key=lambda x: -float(x["price"]))[:5]
                asks = sorted(book.get("asks", []),
                              key=lambda x: float(x["price"]))[:5]
        rows.append({
            "ts": TS, "id": mid, "slug": (m.get("slug") or "")[:80],
            "question": (m.get("question") or "")[:160],
            # full timestamp, not just the date: for 1-day markets the hour
            # decides whether the trade is live and what the ROI actually is
            "end_date": (m.get("endDate") or "")[:19],
            "uma_status": ";".join(json.loads(m.get("umaResolutionStatuses")
                                              or "[]"))
            if (m.get("umaResolutionStatuses") or "").startswith("[") else "",
            "resolution_source": (m.get("resolutionSource") or "")[:80],
            "uma_bond": m.get("umaBond"),
            "outcomes": (m.get("outcomes") or "")[:60],
            "outcome_prices": (m.get("outcomePrices") or "")[:60],
            "vol24h": m.get("volume24hr"), "liquidity": m.get("liquidityNum"),
            "yes_bids": levels(bids), "yes_asks": levels(asks),
        })
    append_gz(BASE / "data" / "pm" / f"{DAY}.csv.gz",
              ["ts", "id", "slug", "question", "end_date", "uma_status",
               "resolution_source", "uma_bond", "outcomes",
               "outcome_prices", "vol24h", "liquidity", "yes_bids", "yes_asks"],
              rows)
    # resolution criteria change rarely - store once per market, not per snapshot
    rules_path = BASE / "data" / "market_rules.csv"
    known = set()
    if rules_path.exists():
        with open(rules_path, newline="", encoding="utf-8") as f:
            known = {r["id"] for r in csv.DictReader(f)}
    new = [m for m in picked if m.get("id") not in known]
    if new:
        write_header = not rules_path.exists()
        with open(rules_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "slug", "end_date",
                                              "resolution_source",
                                              "description"])
            if write_header:
                w.writeheader()
            for m in new:
                w.writerow({"id": m.get("id"), "slug": (m.get("slug") or "")[:80],
                            "end_date": (m.get("endDate") or "")[:19],
                            "resolution_source": (m.get("resolutionSource") or "")[:120],
                            "description": (m.get("description") or "")[:1200]})
        print(f"rules: +{len(new)} markets")
    print(f"polymarket: {len(rows)} markets")
    return len(rows)


def kalshi():
    markets, cursor = [], ""
    for _ in range(25):
        d = get("https://api.elections.kalshi.com/trade-api/v2/markets"
                f"?limit=1000&status=open&cursor={cursor}")
        time.sleep(0.25)
        if not d or not d.get("markets"):
            break
        markets += d["markets"]
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    rows = [{
        "ts": TS, "ticker": m.get("ticker"),
        "title": (m.get("title") or "")[:160],
        "close_time": (m.get("close_time") or "")[:16],
        "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
        "no_bid": m.get("no_bid"), "no_ask": m.get("no_ask"),
        "last": m.get("last_price"), "vol24h": m.get("volume_24h"),
        "volume": m.get("volume"), "oi": m.get("open_interest"),
    } for m in markets]
    append_gz(BASE / "data" / "kalshi" / f"{DAY}.csv.gz",
              ["ts", "ticker", "title", "close_time", "yes_bid", "yes_ask",
               "no_bid", "no_ask", "last", "vol24h", "volume", "oi"],
              rows)
    print(f"kalshi: {len(rows)} markets")
    return len(rows)


if __name__ == "__main__":
    npm = polymarket()
    nk = kalshi()
    if npm + nk < 500:
        sys.exit(f"too few markets captured ({npm}+{nk}) — treating as failure")
