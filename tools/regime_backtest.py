"""Daily-bar regime backtest for index CFDs — does timing beat carry?

THE QUESTION THIS ANSWERS
-------------------------
A long index CFD pays overnight financing that is calibrated to approximate the
cost of carry — i.e. approximately the drift you would be trying to capture. So
buying and holding an index CFD is close to a wash by construction.

That leaves exactly one way a longer-horizon index bot can work: be long only
when conditions favour it, and flat otherwise. Flat costs nothing. Long costs
financing. The strategy must therefore earn MORE THAN CARRY during the windows
it chooses to be invested.

This script tests that, honestly:
  - real OANDA daily candles for the instruments actually traded
  - spread charged on every round trip
  - financing charged per CALENDAR day held (weekends included — they are)
  - every regime compared against buy-and-hold CFD *and* against cash

DISCIPLINE
----------
No parameter fitting. Every rule below is a textbook constant chosen before
seeing the results (200-day SMA, 50/200 cross, 12-month momentum, median vol).
A sweep would find something; that something would be noise. Results are also
reported split-half so instability is visible rather than averaged away.

Read-only. Fetches candles, computes, prints. Places nothing.
"""
from __future__ import annotations

import json
import os
import statistics as st
import urllib.request
from datetime import datetime, timezone

TOKEN = (os.environ.get("OANDA_API_KEY") or os.environ.get("OANDA_API_TOKEN") or "").strip()
URL = (os.environ.get("OANDA_API_URL") or "https://api-fxtrade.oanda.com").rstrip("/")

INSTRUMENTS = ["DE30_EUR", "SPX500_USD", "NAS100_USD", "UK100_GBP"]

# Cost assumptions. Stated loudly because the whole answer hinges on them.
SPREAD_PCT = 0.0174 / 100      # round-trip, measured from the live bot's own fills
FINANCING_ANNUAL = 0.050       # long CFD funding cost, ~5%/yr. SENSITIVITY TESTED BELOW.


def fetch_daily(instrument: str, count: int = 5000) -> list[dict]:
    url = (f"{URL}/v3/instruments/{instrument}/candles"
           f"?granularity=D&count={count}&price=M")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    out = []
    for c in data.get("candles", []):
        if not c.get("complete"):
            continue
        m = c["mid"]
        out.append({"t": datetime.fromisoformat(c["time"].replace("Z", "+00:00").split(".")[0] + "+00:00"),
                    "o": float(m["o"]), "h": float(m["h"]), "l": float(m["l"]), "c": float(m["c"])})
    return out


def sma(vals: list[float], n: int, i: int) -> float | None:
    return sum(vals[i - n + 1:i + 1]) / n if i >= n - 1 else None


def realised_vol(rets: list[float], n: int, i: int) -> float | None:
    if i < n:
        return None
    w = rets[i - n + 1:i + 1]
    return st.pstdev(w) if len(w) > 1 else None


# --- regime rules. Each returns True = be long. Pre-specified, not fitted. ----

def rule_buy_hold(**_):
    return True


def rule_sma200(*, close, s200, **_):
    return s200 is not None and close > s200


def rule_golden(*, s50, s200, **_):
    return s50 is not None and s200 is not None and s50 > s200


def rule_mom12(*, closes, i, **_):
    return i >= 252 and closes[i] > closes[i - 252]


def rule_sma200_lowvol(*, close, s200, vol, vol_median, **_):
    return (s200 is not None and close > s200
            and vol is not None and vol_median is not None and vol <= vol_median)


def rule_sma200_or_mom(*, close, s200, closes, i, **_):
    a = s200 is not None and close > s200
    b = i >= 252 and closes[i] > closes[i - 252]
    return a and b


RULES = {
    "BUY_AND_HOLD": rule_buy_hold,
    "SMA200": rule_sma200,
    "GOLDEN_50_200": rule_golden,
    "MOM_12M": rule_mom12,
    "SMA200_AND_MOM12": rule_sma200_or_mom,
    "SMA200_LOWVOL": rule_sma200_lowvol,
}


def run(bars: list[dict], rule, *, financing_annual: float = FINANCING_ANNUAL,
        spread: float = SPREAD_PCT) -> dict:
    """Long-or-flat. Returns compounded net-of-cost performance."""
    closes = [b["c"] for b in bars]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    vols = [realised_vol(rets, 20, i) for i in range(len(closes))]
    known = [v for v in vols if v is not None]
    vol_median = st.median(known) if known else None

    equity = 1.0
    peak = 1.0
    maxdd = 0.0
    in_pos = False
    days_in = 0
    trips = 0
    gross = 1.0
    fin_cost = 0.0
    spread_cost = 0.0
    curve = []

    for i in range(1, len(bars)):
        want = rule(close=closes[i - 1], s200=sma(closes, 200, i - 1), s50=sma(closes, 50, i - 1),
                    closes=closes, i=i - 1, vol=vols[i - 1], vol_median=vol_median)
        if want and not in_pos:
            in_pos = True
            trips += 1
            equity *= (1 - spread)
            spread_cost += spread
        elif not want and in_pos:
            in_pos = False

        if in_pos:
            days_held = max(1, (bars[i]["t"] - bars[i - 1]["t"]).days)   # weekends charged
            carry = financing_annual / 365.0 * days_held
            equity *= (1 + rets[i] - carry)
            gross *= (1 + rets[i])
            fin_cost += carry
            days_in += 1
        peak = max(peak, equity)
        maxdd = min(maxdd, equity / peak - 1.0)
        curve.append(equity)

    years = (bars[-1]["t"] - bars[0]["t"]).days / 365.25
    cagr = equity ** (1 / years) - 1 if years > 0 and equity > 0 else -1.0
    return {"net": equity - 1, "cagr": cagr, "maxdd": maxdd, "trips": trips,
            "days_in_pct": 100 * days_in / max(1, len(bars) - 1),
            "gross": gross - 1, "fin": fin_cost, "spread": spread_cost,
            "years": years, "curve": curve}


def main() -> None:
    if not TOKEN:
        print("OANDA token missing")
        return
    for inst in INSTRUMENTS:
        try:
            bars = fetch_daily(inst)
        except Exception as e:
            print(f"\n{inst}: fetch failed — {e}")
            continue
        if len(bars) < 400:
            print(f"\n{inst}: only {len(bars)} daily bars, skipping")
            continue
        print(f"\n{'='*88}")
        print(f"{inst}   {bars[0]['t']:%Y-%m-%d} -> {bars[-1]['t']:%Y-%m-%d}   "
              f"{len(bars)} daily bars ({(bars[-1]['t']-bars[0]['t']).days/365.25:.1f}y)")
        print(f"{'='*88}")
        print(f"  costs: spread {SPREAD_PCT*100:.4f}%/round-trip | financing {FINANCING_ANNUAL*100:.1f}%/yr "
              f"charged per calendar day held")
        print(f"\n  {'rule':20}{'net%':>9}{'CAGR%':>8}{'maxDD%':>9}{'trips':>7}{'in mkt%':>9}"
              f"{'gross%':>9}{'fin%':>8}{'spr%':>7}")
        results = {}
        for name, rule in RULES.items():
            r = run(bars, rule)
            results[name] = r
            print(f"  {name:20}{100*r['net']:>9.1f}{100*r['cagr']:>8.2f}{100*r['maxdd']:>9.1f}"
                  f"{r['trips']:>7}{r['days_in_pct']:>9.1f}{100*r['gross']:>9.1f}"
                  f"{100*r['fin']:>8.1f}{100*r['spread']:>7.2f}")

        bh = results["BUY_AND_HOLD"]
        print(f"\n  vs buy-and-hold (the honest benchmark — it pays financing too):")
        for name, r in results.items():
            if name == "BUY_AND_HOLD":
                continue
            print(f"    {name:20} excess {100*(r['net']-bh['net']):>+8.1f}pp | "
                  f"CAGR {100*(r['cagr']-bh['cagr']):>+6.2f}pp | "
                  f"maxDD {100*(r['maxdd']-bh['maxdd']):>+6.1f}pp")

        # split-half stability: no fitting was done, but instability still matters
        mid = len(bars) // 2
        print(f"\n  split-half CAGR% (first / second) — instability is the tell:")
        for name, rule in RULES.items():
            a = run(bars[:mid], rule)
            b = run(bars[mid:], rule)
            flag = "  <-- SIGN FLIP" if (a["cagr"] > 0) != (b["cagr"] > 0) else ""
            print(f"    {name:20}{100*a['cagr']:>8.2f} /{100*b['cagr']:>8.2f}{flag}")

        # does the answer survive a worse financing assumption?
        print(f"\n  financing sensitivity (net%, best non-BH rule):")
        best = max((n for n in results if n != "BUY_AND_HOLD"),
                   key=lambda n: results[n]["net"])
        for f in (0.03, 0.05, 0.07):
            r = run(bars, RULES[best], financing_annual=f)
            bhf = run(bars, rule_buy_hold, financing_annual=f)
            print(f"    financing {f*100:.0f}%/yr -> {best} {100*r['net']:>+8.1f}% | "
                  f"B&H {100*bhf['net']:>+8.1f}% | excess {100*(r['net']-bhf['net']):>+7.1f}pp")


if __name__ == "__main__":
    main()
