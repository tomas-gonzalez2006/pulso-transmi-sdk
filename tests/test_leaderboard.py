from pulso_transmi.leaderboard import build_metric
import pytest


def test_build_metric_derives_wape_from_percentage_accuracy():
    metric = build_metric(
        "Tomas",
        {"display_name": "Tomas", "accuracy": 77.8, "coverage": 0.25, "rank": 3},
    )
    assert metric["metric_scope"] == "cumulative"
    assert metric["accuracy"] == 77.8
    assert metric["wape"] == pytest.approx(0.222)
    assert metric["coverage"] == 0.25
    assert metric["metadata"]["rank"] == 3


def test_build_metric_normalizes_fractional_values():
    metric = build_metric(
        "Tomas",
        {"display_name": "Tomas", "accuracy": 0.8, "wape": 20, "coverage": 80},
    )
    assert metric["accuracy"] == 80
    assert metric["wape"] == 0.2
    assert metric["coverage"] == 0.8
