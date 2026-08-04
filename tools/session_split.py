"""Where do index returns actually live — overnight or intraday?

The literature says nearly all US index gains accrue OVERNIGHT (close -> next
open), not during the cash session. If true it explains why an intraday index
bot struggles: it is trading the session that carries little of the drift.

This splits 19.5y of daily bars into the two components and charges each the
costs a CFD would actually pay:
  overnight  = buy at close, sell at next open. ~17h held -> financing, 1 spread
  intraday   = buy at open,  sell at close.     ~7h held  -> no financing, 1 spread

Both trade EVERY day, so both pay ~252 round trips a year. That spread bill is
the main obstacle and it is charged in full, not waved away.
"""
from __future__ import annotations

import os

from regime_backtest import FINANCING_ANNUAL, INSTRUMENTS, SPREAD_PCT, fetch_daily


def split(bars: list[dict]) -> None:
    n = len(bars) - 1
    years = (bars[-1]["t"] - bars[0]["t"]).days / 365.25

    on_gross = on_net = 1.0
    id_gross = id_net = 1.0
    on_peak = id_peak = 1.0
    on_dd = id_dd = 0.0
    on_win = id_win = 0

    for i in range(1, len(bars)):
        # overnight: close[i-1] -> open[i]
        r_on = bars[i]["o"] / bars[i - 1]["c"] - 1.0
        days = max(1, (bars[i]["t"] - bars[i - 1]["t"]).days)
        carry = FINANCING_ANNUAL / 365.0 * days
        on_gross *= (1 + r_on)
        on_net *= (1 + r_on - carry - SPREAD_PCT)
        on_win += r_on > 0
        on_peak = max(on_peak, on_net); on_dd = min(on_dd, on_net / on_peak - 1)

        # intraday: open[i] -> close[i]
        r_id = bars[i]["c"] / bars[i]["o"] - 1.0
        id_gross *= (1 + r_id)
        id_net *= (1 + r_id - SPREAD_PCT)
        id_win += r_id > 0
        id_peak = max(id_peak, id_net); id_dd = min(id_dd, id_net / id_peak - 1)

    def cagr(x):
        return (x ** (1 / years) - 1) * 100 if x > 0 else float("nan")

    print(f"  {'component':12}{'gross%':>10}{'grossCAGR':>11}{'net%':>10}{'netCAGR':>9}"
          f"{'maxDD%':>9}{'win%':>7}")
    print(f"  {'OVERNIGHT':12}{100*(on_gross-1):>10.1f}{cagr(on_gross):>11.2f}"
          f"{100*(on_net-1):>10.1f}{cagr(on_net):>9.2f}{100*on_dd:>9.1f}{100*on_win/n:>7.1f}")
    print(f"  {'INTRADAY':12}{100*(id_gross-1):>10.1f}{cagr(id_gross):>11.2f}"
          f"{100*(id_net-1):>10.1f}{cagr(id_net):>9.2f}{100*id_dd:>9.1f}{100*id_win/n:>7.1f}")
    share = 100 * (on_gross - 1) / ((on_gross - 1) + (id_gross - 1)) if (on_gross - 1) + (id_gross - 1) else float("nan")
    print(f"  -> overnight is {share:.0f}% of total gross drift | "
          f"annual cost of daily trading: spread {252*SPREAD_PCT*100:.1f}%/yr")


def main() -> None:
    for inst in INSTRUMENTS:
        try:
            bars = fetch_daily(inst)
        except Exception as e:
            print(f"{inst}: {e}")
            continue
        if len(bars) < 400:
            continue
        print(f"\n{'='*84}\n{inst}  {bars[0]['t']:%Y-%m-%d} -> {bars[-1]['t']:%Y-%m-%d}  "
              f"({len(bars)} bars)\n{'='*84}")
        split(bars)


if __name__ == "__main__":
    main()
