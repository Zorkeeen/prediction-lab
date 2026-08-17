#!/usr/bin/env python3
"""Discord embeds for prediction-lab.

    python3 notify.py backfill      progress + running calibration table
    python3 notify.py digest        daily: best live ROI opportunities
    python3 notify.py fail <url>

Reads DISCORD_WEBHOOK from env; prints instead of posting when unset.
Stdlib only.
"""
import csv, glob, gzip, json, os, sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

DATA = Path(__file__).resolve().parent / "data"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
GREEN, BLUE, RED, PURPLE = 0x2ecc71, 0x3498db, 0xe74c3c, 0x9b59b6


def post(embeds):
    if not WEBHOOK:
        print(json.dumps(embeds, indent=1, ensure_ascii=False)[:3000])
        return
    body = json.dumps({"username": "prediction-lab",
                       "embeds": embeds}).encode()
    with urlopen(Request(WEBHOOK, data=body,
                         headers={"Content-Type": "application/json"}),
                 timeout=15) as r:
        r.read()


def read_gz_rows(pattern):
    rows = []
    for f in sorted(glob.glob(str(DATA / pattern))):
        try:
            with gzip.open(f, "rt") as fh:
                rows += [r for r in csv.DictReader(fh) if r.get("id")]
        except OSError:
            pass
    return rows


def fnum(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def yes_price(s):
    try:
        return float(json.loads(s)[0])
    except Exception:
        return None


def calibration():
    """(table_text, n_markets) from whatever backfill exists."""
    meta = {r["id"]: r for r in read_gz_rows("backfill/resolved_meta.csv.gz")}
    outcome = {}
    for mid, r in meta.items():
        y = yes_price(r.get("final_prices"))
        if y in (0.0, 1.0):
            outcome[mid] = y
    hist = read_gz_rows("backfill/hist_*.csv.gz")
    buckets = [(0, .05), (.05, .10), (.10, .25), (.25, .50),
               (.50, .75), (.75, .90), (.90, .95), (.95, 1.0)]
    agg = {b: [0, 0.0, 0.0, set()] for b in buckets}   # n, sum_p, sum_y, mkts
    for h in hist:
        y = outcome.get(h["id"])
        p = fnum(h.get("p"))
        if y is None or p is None:
            continue
        for b in buckets:
            if b[0] < p <= b[1]:
                a = agg[b]
                a[0] += 1
                a[1] += p
                a[2] += y
                a[3].add(h["id"])
                break
    lines = ["```", f"{'price':>12}{'mkts':>6}{'priced':>8}{'actual':>8}{'gap':>7}"]
    for b in buckets:
        n, sp, sy, mk = agg[b]
        if n < 30:
            continue
        priced, actual = sp / n, sy / n
        lines.append(f"{b[0]:.2f}-{b[1]:.2f}".rjust(12)
                     + f"{len(mk):>6}{priced:>8.3f}{actual:>8.3f}"
                     + f"{(actual - priced) * 100:>+6.1f}p")
    lines.append("```")
    return "\n".join(lines), len(outcome)


def backfill_embed():
    state = {}
    p = DATA / "backfill_state.json"
    if p.exists():
        state = json.loads(p.read_text())
    done = state.get("done_windows", [])
    hist_n = sum(1 for _ in read_gz_rows("backfill/hist_*.csv.gz"))
    table, n_mkts = calibration()
    complete = state.get("done", False)
    fields = [
        {"name": "Months walked", "value": f"{len(done)}", "inline": True},
        {"name": "Resolved markets", "value": f"{n_mkts:,}", "inline": True},
        {"name": "Price points", "value": f"{hist_n:,}", "inline": True},
    ]
    if done:
        fields.append({"name": "Range",
                       "value": f"{min(done)} → {max(done)}", "inline": False})
    desc = ("**Calibration — priced vs realised**\n" + table
            if n_mkts >= 40 else
            "_Not enough resolved markets yet for a calibration read._")
    title = ("Backfill COMPLETE" if complete
             else "Backfill progress")
    return {"title": title, "color": PURPLE if not complete else GREEN,
            "description": desc, "fields": fields,
            "footer": {"text": "gap = realised − priced; positive means the "
                               "market underpriced that outcome"},
            "timestamp": datetime.now(timezone.utc).isoformat()}


def digest_embed():
    """Best live ROI: near-certain outcomes resolving soon, with volume."""
    files = sorted(glob.glob(str(DATA / "pm" / "*.csv.gz")))
    if not files:
        return None
    rows = []
    with gzip.open(files[-1], "rt") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("id")]
    seen, cands = set(), []
    today = date.today()
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        y = yes_price(r.get("outcome_prices"))
        vol = fnum(r.get("vol24h"), 0) or 0
        if y is None or vol < 1000:
            continue
        cost = y if y > 0.5 else 1 - y
        if not 0.90 <= cost < 0.999:
            continue
        try:
            end = date.fromisoformat((r.get("end_date") or "")[:10])
        except ValueError:
            continue
        days = (end - today).days
        if not 0 < days <= 30:
            continue
        ann = (1 - cost) / cost * 100 * 365 / days
        cands.append((ann, cost, days, vol, r.get("question", "")[:44],
                      "YES" if y > 0.5 else "NO"))
    cands.sort(reverse=True)
    if not cands:
        return {"title": f"Live digest · {today}", "color": BLUE,
                "description": "_No liquid near-certain markets resolving "
                               "within 30 days._"}
    lines = ["```", f"{'market':<46}{'side':>5}{'px':>6}{'d':>4}{'ann%':>8}"]
    for ann, cost, days, vol, q, side in cands[:10]:
        lines.append(f"{q:<46}{side:>5}{cost:>6.3f}{days:>4}{ann:>8.0f}")
    lines.append("```")
    return {"title": f"Live digest · {today} — best short-dated ROI",
            "color": BLUE, "description": "\n".join(lines),
            "fields": [{"name": "Universe",
                        "value": f"{len(seen):,} live markets · "
                                 f"{len(cands)} near-certain & liquid "
                                 "resolving ≤30d", "inline": False}],
            "footer": {"text": "ann% assumes correct resolution — "
                               "calibration says whether that holds"}}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    if mode == "fail":
        url = sys.argv[2] if len(sys.argv) > 2 else ""
        post([{"title": "prediction-lab run FAILED", "color": RED,
               "description": f"[Open the run log]({url})" if url else "",
               "timestamp": datetime.now(timezone.utc).isoformat()}])
    elif mode == "digest":
        e = digest_embed()
        if e:
            post([e])
    else:
        post([backfill_embed()])


if __name__ == "__main__":
    main()
