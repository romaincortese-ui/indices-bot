import json
from pathlib import Path
from types import SimpleNamespace

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
