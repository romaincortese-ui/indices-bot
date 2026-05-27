import json
from pathlib import Path
from types import SimpleNamespace

import indicesbot.backtest.run_backtest as runner
from indicesbot.backtest.engine import _simulate_exit
from indicesbot.backtest.run_backtest import main


def test_backtest_writes_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("INDICES_CALIBRATION_FILE", str(tmp_path / "calibration.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")

    code = main(["--days", "5", "--macro-scenario", "risk-off"])

    assert code == 0
    assert (tmp_path / "out" / "summary.json").exists()
    assert (tmp_path / "calibration.json").exists()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert "total_trades" in summary
    assert summary["data_source"] == "synthetic"
    assert summary["macro_scenario"] == "risk-off"
    assert summary["symbols"] == ["SPX500"]
    assert summary["calibration_source"] == "none"
    if summary["trades"]:
        assert "exit_reason" in summary["trades"][0]
        assert "r_multiple" in summary["trades"][0]
        assert "risk_amount" in summary["trades"][0]


def test_30_day_backtest_fails_when_pnl_is_not_positive(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("INDICES_CALIBRATION_FILE", str(tmp_path / "calibration.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")

    def fake_market_candles_by_symbol(*_args, **_kwargs):
        return {"SPX500": [SimpleNamespace(close=100.0)]}, {"SPX500": "synthetic"}

    def fake_run_backtest(*_args, **_kwargs):
        return {
            "total_trades": 1,
            "total_pnl": -1.0,
            "return_pct": -0.0001,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0001,
            "trades": [],
        }

    monkeypatch.setattr(runner, "market_candles_by_symbol", fake_market_candles_by_symbol)
    monkeypatch.setattr(runner, "run_backtest", fake_run_backtest)
    monkeypatch.setattr(runner, "calibration_from_summary", lambda _summary: {})
    calibration_writes = []
    review_writes = []
    monkeypatch.setattr(runner, "write_calibration", lambda *args, **_kwargs: calibration_writes.append(args) or {"generated_at": "test"})
    monkeypatch.setattr(runner, "write_daily_review", lambda *args, **_kwargs: review_writes.append(args) or None)

    code = main(["--days", "30"])

    assert code == 1
    assert calibration_writes == []
    assert review_writes == []


def test_profit_lock_exit_protects_positive_trade() -> None:
    config = SimpleNamespace(profit_lock_enabled=True, profit_lock_trigger_pct=15.0, profit_lock_pullback_pct=2.0)
    opportunity = SimpleNamespace(direction="LONG", entry_price=100.0, stop_price=90.0, take_profit_price=None)
    candles = [
        SimpleNamespace(close=100.0, high=100.0, low=100.0),
        SimpleNamespace(close=102.0, high=102.0, low=100.5),
        SimpleNamespace(close=101.7, high=102.0, low=101.5),
    ]

    exit_price, exit_reason, held_bars = _simulate_exit(candles, 0, opportunity, max_hold_bars=4, config=config)

    assert exit_reason == "peak_pullback_profit_lock"
    assert exit_price > opportunity.entry_price
    assert held_bars == 2


def test_no_progress_exit_cuts_trade_that_never_launches() -> None:
    config = SimpleNamespace(
        profit_lock_enabled=False,
        no_progress_exit_enabled=True,
        no_progress_min_bars=3,
        no_progress_min_peak_r=0.08,
        no_progress_loss_r=0.35,
    )
    opportunity = SimpleNamespace(direction="LONG", entry_price=100.0, stop_price=90.0, take_profit_price=None)
    candles = [
        SimpleNamespace(close=100.0, high=100.0, low=100.0),
        SimpleNamespace(close=99.2, high=100.2, low=99.0),
        SimpleNamespace(close=98.5, high=100.4, low=98.2),
        SimpleNamespace(close=96.4, high=100.5, low=96.2),
    ]

    exit_price, exit_reason, held_bars = _simulate_exit(candles, 0, opportunity, max_hold_bars=4, config=config)

    assert exit_reason == "no_progress_loss_exit"
    assert exit_price == 96.4
    assert held_bars == 3
