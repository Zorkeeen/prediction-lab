#!/usr/bin/env python3
"""Fast-poll the markets where the high-ROI edge actually lives.

The hourly full-universe sweep is far too coarse for Tier-1 trades: markets
resolving within ~48h, and markets whose UMA resolution has been PROPOSED but
not finalised (i.e. the outcome is effectively known and the price should be
converging to 0/1). Those windows are measured in minutes, not hours.

This runs inside one hourly Actions job and polls that small watchlist every
POLL_SECONDS for DURATION_MINUTES, capturing full book depth each time. The
result is the dataset that answers the only execution question that matters:
how fast does a stale near-certain price converge, and is there time to get
filled passively?

Output: data/watch/YYYYMMDD.csv.gz (gzip members appended per poll)
"""
import csv, gzip, io, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; prediction-lab/2.1)"
POLL_SECONDS = 300           # 5 minutes
DURATION_MINUTES = 50        # stay inside one hourly Actions slot
HORIZON_HOURS = 48
MIN_VOL = 200

FIELDS = ["ts", "id", "slug", "question", "end_date", "hours_left",
          "uma_status", "vol24h", "best_bid", "best_ask", "spread",
          "bid_depth_usd", "ask_depth_usd", "yes_bids", "yes_asks"]


def get(url, retries=2):
    for i in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"}),
                         timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(1.5)


def append_gz(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    if not path.exists():
        w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    with open(path, "ab") as f:
        f.write(gzip.compress(buf.getvalue().encode()))


def levels(side, n=5):
    return "|".join(f"{l.get('price')}:{l.get('size')}"
                    for l in (side or [])[:n])


def depth_usd(side):
    return round(sum(float(l.get("price", 0)) * float(l.get("size", 0))
                     for l in (side or [])), 2)


def build_watchlist():
    """Markets resolving within HORIZON_HOURS, or with a proposed resolution."""
    now = datetime.now(timezone.utc)
    picked, offset = [], 0
    while offset < 3000:
        d = get("https://gamma-api.polymarket.com/markets?active=true"
                f"&closed=false&limit=100&offset={offset}")
        time.sleep(0.12)
        if not d:
            break
        for m in d:
            if float(m.get("volume24hr") or 0) < MIN_VOL:
                continue
            end = (m.get("endDate") or "")[:19]
            hrs = None
            if end:
                try:
                    hrs = (datetime.fromisoformat(end).replace(
                        tzinfo=timezone.utc) - now).total_seconds() / 3600
                except ValueError:
                    hrs = None
            proposed = "proposed" in (m.get("umaResolutionStatuses") or "")
            if proposed or (hrs is not None and 0 < hrs <= HORIZON_HOURS):
                m["_hours_left"] = round(hrs, 2) if hrs is not None else ""
                picked.append(m)
        if len(d) < 100:
            break
        offset += 100
    return picked


def poll(markets, ts):
    rows = []
    for m in markets:
        try:
            tok = json.loads(m.get("clobTokenIds") or "[]")[0]
        except (ValueError, IndexError):
            continue
        book = get(f"https://clob.polymarket.com/book?token_id={tok}",
                   retries=1)
        time.sleep(0.08)
        if not book:
            continue
        bids = sorted(book.get("bids", []),
                      key=lambda x: -float(x["price"]))[:5]
        asks = sorted(book.get("asks", []),
                      key=lambda x: float(x["price"]))[:5]
        bb = float(bids[0]["price"]) if bids else None
        ba = float(asks[0]["price"]) if asks else None
        rows.append({
            "ts": ts, "id": m.get("id"), "slug": (m.get("slug") or "")[:80],
            "question": (m.get("question") or "")[:120],
            "end_date": (m.get("endDate") or "")[:19],
            "hours_left": m.get("_hours_left", ""),
            "uma_status": "proposed"
            if "proposed" in (m.get("umaResolutionStatuses") or "") else "",
            "vol24h": m.get("volume24hr"),
            "best_bid": bb, "best_ask": ba,
            "spread": round(ba - bb, 4) if (bb and ba) else "",
            "bid_depth_usd": depth_usd(bids), "ask_depth_usd": depth_usd(asks),
            "yes_bids": levels(bids), "yes_asks": levels(asks),
        })
    return rows


def main():
    wl = build_watchlist()
    print(f"watchlist: {len(wl)} markets "
          f"(<= {HORIZON_HOURS}h to resolution or UMA-proposed)")
    if not wl:
        return
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = BASE / "data" / "watch" / f"{day}.csv.gz"
    deadline = time.time() + DURATION_MINUTES * 60
    polls = 0
    while time.time() < deadline:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        rows = poll(wl, ts)
        if rows:
            append_gz(path, rows)
            polls += 1
            print(f"  poll {polls} @ {ts}: {len(rows)} books")
        sleep_for = POLL_SECONDS - (time.time() % POLL_SECONDS)
        if time.time() + sleep_for > deadline:
            break
        time.sleep(sleep_for)
    print(f"done: {polls} polls of {len(wl)} markets")


if __name__ == "__main__":
    main()
