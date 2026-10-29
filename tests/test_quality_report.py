from zap.quality.report import dbt_test_rows, summarize_dbt, summarize_rules

RUN_RESULTS = {
    "results": [
        {"unique_id": "model.zomato.fct_orders", "status": "success"},
        {
            "unique_id": "test.zomato.unique_x",
            "status": "pass",
            "failures": 0,
            "execution_time": 0.1,
        },
        {"unique_id": "test.zomato.not_null_y", "status": "fail", "failures": 3},
        {"unique_id": "unit_test.zomato.m.t", "status": "pass", "execution_time": 0.2},
        {"unique_id": "test.zomato.warned", "status": "warn", "failures": 1},
    ]
}


def test_only_tests_are_collected() -> None:
    rows = dbt_test_rows(RUN_RESULTS)
    assert [r["kind"] for r in rows] == ["data", "data", "unit", "data"]
    assert rows[1]["failures"] == 3


def test_summary_counts_and_pass_rate() -> None:
    s = summarize_dbt(dbt_test_rows(RUN_RESULTS))
    assert (s["tests"], s["data_tests"], s["unit_tests"]) == (4, 3, 1)
    assert (s["passed"], s["failed"], s["warned"]) == (2, 1, 1)
    assert s["pass_rate_pct"] == 50.0


def test_empty_summary() -> None:
    assert summarize_dbt([])["pass_rate_pct"] is None


def test_rule_summary() -> None:
    rules = [{"rows_failed": 0}, {"rows_failed": 5}, {"rows_failed": 2}]
    assert summarize_rules(rules, quarantined=4) == {
        "rules": 3,
        "rules_fired": 2,
        "records_flagged": 7,
        "records_quarantined": 4,
    }
