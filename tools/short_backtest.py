"""Short-only index CFD backtest — does the inverted carry rescue it?

WHY THIS IS WORTH TESTING
-------------------------
Long index CFDs pay financing, which is calibrated to remove the drift you are
trying to capture. Shorts sit on the other side of that trade: they RECEIVE the
funding leg. And equity indices fall faster than they rise (leverage effect,
volatility clustering), so downside moves are sharper and shorter.

TWO COSTS THAT CUT THE OTHER WAY, BOTH MODELLED HERE
----------------------------------------------------
1. DIVIDENDS. A short on a PRICE index pays the dividend adjustment. SPX500,
   NAS100 and UK100 are price indices, so shorts bleed the dividend yield. DE30
   is a TOTAL RETURN index — dividends are already inside the price, so there is
   no separate adjustment. Omitting this would flatter shorts, which is the
   opposite of the long-side bias in the earlier backtest.

2. THE FUNDING CREDIT IS NOT CONSTANT. Shorts receive (benchmark - markup). Over
   2007-2026 the benchmark was ~5% in 2007-08 and 2023-26 but ~0% for most of
   2009-2021 — during which a short would have PAID, not received. A single flat
   rate over 19.5y is a real simplification, so the credit is swept from -2.5%
   (short pays) through +2.5% (short receives) rather than assumed.

Long-or-flat became short-or-flat. Flat still costs nothing.
"""
from __future__ import annotations

import statistics as st

from regime_backtest import INSTRUMENTS, SPREAD_PCT, fetch_daily, realised_vol, sma

# Dividend yield a SHORT pays away, by index type.
DIVIDEND_DRAG = {
    "SPX500_USD": 0.018,   # price index
    "NAS100_USD": 0.008,   # price index, low yield
    "UK100_GBP": 0.038,    # price index, high yield — worst instrument to be short
    "DE30_EUR": 0.000,     # TOTAL RETURN index: dividends already in the price
}


def rule_always(**_):
    return True


def rule_below200(*, close, s200, **_):
    return s200 is not None and close < s200


def rule_death_cross(*, s50, s200, **_):
    return s50 is not None and s200 is not None and s50 < s200


def rule_mom_neg(*, closes, i, **_):
    return i >= 252 and closes[i] < closes[i - 252]


def rule_below200_highvol(*, close, s200, vol, vol_median, **_):
    # Crashes cluster in high-vol regimes; this is the theoretically strongest short.
    return (s200 is not None and close < s200
            and vol is not None and vol_median is not None and vol > vol_median)


def rule_below200_and_mom(*, close, s200, closes, i, **_):
    return (s200 is not None and close < s200) and (i >= 252 and closes[i] < closes[i - 252])


RULES = {
    "ALWAYS_SHORT": rule_always,
    "BELOW_SMA200": rule_below200,
    "DEATH_CROSS": rule_death_cross,
    "MOM_12M_NEG": rule_mom_neg,
    "BELOW200_AND_MOM": rule_below200_and_mom,
    "BELOW200_HIGHVOL": rule_below200_highvol,
}


def run_short(bars, rule, *, credit_annual: float, div_annual: float,
              spread: float = SPREAD_PCT) -> dict:
    closes = [b["c"] for b in bars]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    vols = [realised_vol(rets, 20, i) for i in range(len(closes))]
    known = [v for v in vols if v is not None]
    vmed = st.median(known) if known else None

    eq = peak = 1.0
    dd = 0.0
    in_pos = False
    days_in = trips = 0
    for i in range(1, len(bars)):
        want = rule(close=closes[i - 1], s200=sma(closes, 200, i - 1), s50=sma(closes, 50, i - 1),
                    closes=closes, i=i - 1, vol=vols[i - 1], vol_median=vmed)
        if want and not in_pos:
            in_pos, trips = True, trips + 1
            eq *= (1 - spread)
        elif not want and in_pos:
            in_pos = False
        if in_pos:
            d = max(1, (bars[i]["t"] - bars[i - 1]["t"]).days)
            carry = credit_annual / 365.0 * d          # + = received
            div = div_annual / 365.0 * d               # always a cost to a short
            eq *= (1 - rets[i] + carry - div)          # SHORT: -market return
            days_in += 1
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    years = (bars[-1]["t"] - bars[0]["t"]).days / 365.25
    return {"net": eq - 1, "cagr": (eq ** (1 / years) - 1) if eq > 0 else -1.0,
            "dd": dd, "trips": trips, "in_pct": 100 * days_in / max(1, len(bars) - 1)}


def main() -> None:
    for inst in INSTRUMENTS:
        try:
            bars = fetch_daily(inst)
        except Exception as e:
            print(f"{inst}: {e}")
            continue
        if len(bars) < 400:
            continue
        div = DIVIDEND_DRAG.get(inst, 0.02)
        print(f"\n{'='*86}\n{inst}  {bars[0]['t']:%Y-%m-%d} -> {bars[-1]['t']:%Y-%m-%d}  "
              f"({(bars[-1]['t']-bars[0]['t']).days/365.25:.1f}y)")
        print(f"  short pays dividend drag {div*100:.1f}%/yr "
              f"({'TOTAL RETURN index - none' if div == 0 else 'price index'})\n{'='*86}")
        print(f"  {'rule':20}{'net%':>10}{'CAGR%':>8}{'maxDD%':>9}{'trips':>7}{'in mkt%':>9}")
        base = {}
        for name, rule in RULES.items():
            r = run_short(bars, rule, credit_annual=0.025, div_annual=div)
            base[name] = r
            print(f"  {name:20}{100*r['net']:>10.1f}{100*r['cagr']:>8.2f}"
                  f"{100*r['dd']:>9.1f}{r['trips']:>7}{r['in_pct']:>9.1f}")
        best = max(base, key=lambda n: base[n]["net"])
        print(f"\n  funding-credit sensitivity for {best} (rates were ~0% for most of 2009-2021):")
        for c in (-0.025, 0.0, 0.025):
            r = run_short(bars, RULES[best], credit_annual=c, div_annual=div)
            lbl = "short PAYS 2.5%" if c < 0 else ("neutral" if c == 0 else "short RECEIVES 2.5%")
            print(f"    {lbl:22} net {100*r['net']:>+9.1f}%  CAGR {100*r['cagr']:>+6.2f}%")
        print(f"  dividend sensitivity for {best} (if the drag were zero — i.e. flattered):")
        r0 = run_short(bars, RULES[best], credit_annual=0.025, div_annual=0.0)
        print(f"    no dividend drag       net {100*r0['net']:>+9.1f}%  "
              f"CAGR {100*r0['cagr']:>+6.2f}%   <- optimistic bound")
        mid = len(bars) // 2
        print(f"  split-half CAGR% (first / second):")
        for name, rule in RULES.items():
            a = run_short(bars[:mid], rule, credit_annual=0.025, div_annual=div)
            b = run_short(bars[mid:], rule, credit_annual=0.025, div_annual=div)
            flag = "  <-- SIGN FLIP" if (a["cagr"] > 0) != (b["cagr"] > 0) else ""
            print(f"    {name:20}{100*a['cagr']:>8.2f} /{100*b['cagr']:>8.2f}{flag}")


if __name__ == "__main__":
    main()
