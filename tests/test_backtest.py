import json
from pathlib import Path

from indicesbot.backtest.run_backtest import main


def test_backtest_writes_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("INDICES_CALIBRATION_FILE", str(tmp_path / "calibration.json"))
    monkeypatch.setenv("INDICES_DAILY_REVIEW_FILE", str(tmp_path / "review.json"))
    monkeypatch.setenv("INDICES_UNIVERSE", "SPX500")

    code = main(["--days", "5"])

    assert code == 0
    assert (tmp_path / "out" / "summary.json").exists()
    assert (tmp_path / "calibration.json").exists()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert "total_trades" in summary
    assert summary["data_source"] == "synthetic"
    assert summary["symbols"] == ["SPX500"]
    assert summary["calibration_source"] == "none"
    if summary["trades"]:
        assert "exit_reason" in summary["trades"][0]
