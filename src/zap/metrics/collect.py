"""Read every metric from the file a real run wrote, and never from anywhere else.

Nothing here computes or assumes a result. A missing file or key becomes "not run", so the
report can't show a number that no run produced.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NOT_RUN = "not run"
FULL = "Full data (synthetic-looking source)"
SAMPLE = "5% user sample"
PLANTED = "Full data, with SYNTHETIC planted events"
SHUFFLED = "Full data, shuffled as a control"
# A test case that passed has no child element, so its tag closes on the same line.
ATTACK_PASSED = re.compile(r'<testcase [^>]*name="test_attack_is_refused\[[^>]*/>')


@dataclass(frozen=True)
class Metric:
    group: str
    name: str
    value: str
    data: str
    source: str
    ran_at: str = ""


class Sources:
    def __init__(self, artifacts: Path, results: Path) -> None:
        self.artifacts = artifacts
        self.results = results

    def json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def art(self, name: str) -> dict[str, Any] | None:
        return self.json(self.artifacts / name)

    def result(self, name: str) -> dict[str, Any] | None:
        return self.json(self.results / name)


def dig(data: Any, *keys: Any) -> Any:
    for key in keys:
        if not isinstance(data, dict | list):
            return None
        try:
            data = data[key]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def show(value: Any, form: Callable[[Any], str] = str) -> str:
    return NOT_RUN if value is None else form(value)


def count(value: Any) -> str:
    return f"{int(value):,}"


def seconds(value: Any) -> str:
    return f"{float(value):,.1f} s"


def percent(value: Any) -> str:
    return f"{float(value):.1f}%"


def share(value: Any) -> str:
    return f"{float(value) * 100:.1f}%"


def score(value: Any) -> str:
    return f"{float(value):.3f}"


def small(value: Any) -> str:
    return f"{float(value):.5f}"


def money(value: Any) -> str:
    return f"${float(value):.4f}"


def stamp(data: dict[str, Any] | None, *keys: str) -> str:
    for key in keys or ("ran_at",):
        value = dig(data, key)
        if value:
            return str(value)[:10]
    return ""


def pipeline_metrics(src: Sources) -> list[Metric]:
    group = "Pipeline size and speed"
    run = src.art("pipeline/full/latest.json")
    ingest = next((s for s in (dig(run, "steps") or []) if s.get("name") == "ingest"), None)
    when = stamp(run, "started_at")
    source = "artifacts/pipeline/full/latest.json"
    sample = src.art("pipeline/sample_5pct/latest.json")
    fresh = src.art("fresh_clone.json")
    demo = dig(fresh, "demo_seconds") if dig(fresh, "exit_code") == 0 else None
    return [
        Metric(
            group,
            "`make demo` on a fresh clone",
            show(demo, seconds),
            "SYNTHETIC fixture",
            "artifacts/fresh_clone.json",
            stamp(fresh),
        ),
        Metric(
            group,
            "Install on a fresh clone, with packages already cached",
            show(dig(fresh, "install_seconds"), seconds),
            "SYNTHETIC fixture",
            "artifacts/fresh_clone.json",
            stamp(fresh),
        ),
        Metric(
            group,
            "Rows through the pipeline",
            show(dig(ingest, "detail", "rows"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Lake size",
            show(dig(ingest, "detail", "bytes"), lambda b: f"{int(b) / 1024**3:.2f} GB"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "End-to-end `make local-full`",
            show(dig(run, "seconds"), seconds),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "End-to-end `make local`",
            show(dig(sample, "seconds"), seconds),
            SAMPLE,
            "artifacts/pipeline/sample_5pct/latest.json",
            stamp(sample, "started_at"),
        ),
    ]


def cloud_metrics(src: Sources) -> list[Metric]:
    group = "Cloud (AWS and Snowflake)"
    sync = src.art("cloud/s3_sync_full.json")
    load = src.art("cloud/snowflake_load_full.json")
    compare = src.art("cloud/engine_compare_full.json")
    access = src.art("cloud/access_check.json")
    gb = show(dig(sync, "bytes"), lambda b: f"{int(b) / 1024**3:.3f} GB")
    checks = dig(access, "checks")
    passed = sum(c["allowed"] == c["expected_allowed"] for c in checks) if checks else None
    return [
        Metric(group, "Data in S3", gb, FULL, "artifacts/cloud/s3_sync_full.json"),
        Metric(
            group,
            "Files in S3",
            show(dig(sync, "files"), count),
            FULL,
            "artifacts/cloud/s3_sync_full.json",
        ),
        Metric(
            group,
            "Rows loaded into Snowflake",
            show(dig(load, "rows"), count),
            FULL,
            "artifacts/cloud/snowflake_load_full.json",
        ),
        Metric(
            group,
            "Snowflake load time",
            show(dig(load, "seconds"), seconds),
            FULL,
            "artifacts/cloud/snowflake_load_full.json",
        ),
        Metric(
            group,
            "Tables identical, Snowflake vs DuckDB",
            f"{dig(compare, 'tables_identical')} of {dig(compare, 'tables')}"
            if compare
            else NOT_RUN,
            FULL,
            "artifacts/cloud/engine_compare_full.json",
            stamp(compare),
        ),
        Metric(
            group,
            "Measures compared across the two engines",
            show(dig(compare, "measures"), count),
            FULL,
            "artifacts/cloud/engine_compare_full.json",
            stamp(compare),
        ),
        Metric(
            group,
            "Analyst role checks passed",
            f"{passed} of {len(checks)}" if checks else NOT_RUN,
            FULL,
            "artifacts/cloud/access_check.json",
            stamp(access),
        ),
        Metric(
            group,
            "Readable emails for the analyst role",
            show(dig(access, "analyst_unmasked_rows"), count),
            FULL,
            "artifacts/cloud/access_check.json",
            stamp(access),
        ),
    ]


def speed_metrics(src: Sources) -> list[Metric]:
    group = "Incremental vs full refresh"
    rows: list[Metric] = []
    for label, folder in (("DuckDB", "full"), ("Snowflake", "snowflake_full")):
        path = f"bench/{folder}/incremental_vs_full_refresh.json"
        data = src.art(path)
        source = f"artifacts/{path}"
        when = stamp(data)
        runs = dig(data, "raw_runs") or []
        times = [run["incremental_dbt_s"] for run in runs]
        spread = f"{min(times):.1f} to {max(times):.1f} s" if times else NOT_RUN
        rows += [
            Metric(
                group, f"{label}: incremental run, fastest to slowest", spread, FULL, source, when
            ),
            Metric(
                group,
                f"{label}: full refresh of the project",
                show(dig(data, "full_refresh_dbt_s"), seconds),
                FULL,
                source,
                when,
            ),
            Metric(
                group,
                f"{label}: incremental run",
                show(dig(data, "incremental_dbt_s"), seconds),
                FULL,
                source,
                when,
            ),
            Metric(
                group,
                f"{label}: time saved, whole project",
                show(dig(data, "saved_pct"), percent),
                FULL,
                source,
                when,
            ),
            Metric(
                group,
                f"{label}: time saved, fact models",
                show(dig(data, "facts_saved_pct"), percent),
                FULL,
                source,
                when,
            ),
        ]
    return rows


def query_metrics(src: Sources) -> list[Metric]:
    group = "Query time"
    mart = src.art("bench/full/fact_scan_vs_aggregate_mart.json")
    cluster = src.art("bench/snowflake_full/clustering.json")
    source_mart = "artifacts/bench/full/fact_scan_vs_aggregate_mart.json"
    source_cluster = "artifacts/bench/snowflake_full/clustering.json"
    question = "city_gmv_last_30_days"
    partitions = dig(cluster, "sorted_by_date", "partitions")
    return [
        Metric(
            group,
            "Fact table, 30-day city GMV",
            show(dig(mart, question, "fact_median_ms"), lambda v: f"{float(v):.1f} ms"),
            FULL,
            source_mart,
            stamp(mart),
        ),
        Metric(
            group,
            "Aggregate mart, same question",
            show(dig(mart, question, "mart_median_ms"), lambda v: f"{float(v):.1f} ms"),
            FULL,
            source_mart,
            stamp(mart),
        ),
        Metric(
            group,
            "Speed-up from the mart",
            show(dig(mart, question, "speedup_x"), lambda v: f"{float(v):.1f}x"),
            FULL,
            source_mart,
            stamp(mart),
        ),
        Metric(
            group,
            "Snowflake median, as built",
            show(dig(cluster, "as_built_by_dbt", "median_ms"), lambda v: f"{float(v):.0f} ms"),
            FULL,
            source_cluster,
            stamp(cluster),
        ),
        Metric(
            group,
            "Snowflake median, sorted by date",
            show(dig(cluster, "sorted_by_date", "median_ms"), lambda v: f"{float(v):.0f} ms"),
            FULL,
            source_cluster,
            stamp(cluster),
        ),
        Metric(
            group,
            "Partitions scanned, sorted by date",
            f"{partitions['scanned']} of {partitions['total']}" if partitions else NOT_RUN,
            FULL,
            source_cluster,
            stamp(cluster),
        ),
    ]


def quality_metrics(src: Sources) -> list[Metric]:
    group = "Data quality"
    report = src.art("quality/full/dq_report.json")
    source = "artifacts/quality/full/dq_report.json"
    when = stamp(report, "generated_at")
    return [
        Metric(group, "dbt tests", show(dig(report, "dbt", "tests"), count), FULL, source, when),
        Metric(
            group,
            "dbt pass rate",
            show(dig(report, "dbt", "pass_rate_pct"), percent),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Great Expectations checks",
            show(dig(report, "gx", "expectations"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group, "Quality rules", show(dig(report, "rules", "rules"), count), FULL, source, when
        ),
        Metric(
            group,
            "Rules that found issues",
            show(dig(report, "rules", "rules_fired"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Records flagged",
            show(dig(report, "rules", "records_flagged"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Records quarantined",
            show(dig(report, "rules", "records_quarantined"), count),
            FULL,
            source,
            when,
        ),
    ]


def airflow_metrics(src: Sources) -> list[Metric]:
    group = "Orchestration"
    data = src.art("airflow/verification.json")
    source = "artifacts/airflow/verification.json"
    when = stamp(data, "recorded_at")
    return [
        Metric(
            group,
            "Daily runs succeeded",
            show(dig(data, "daily_runs", "succeeded")),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Tables identical after re-running a day",
            show(dig(data, "rerun_one_day_identical_tables")),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Tables identical, day by day vs full rebuild",
            show(dig(data, "vs_full_rebuild_tables_identical")),
            SAMPLE,
            source,
            when,
        ),
    ]


def forecast_metrics(src: Sources) -> list[Metric]:
    group = "Forecasting and anomalies"
    data = src.art("forecast_backtest.json")
    source = "artifacts/forecast_backtest.json"
    when = stamp(data)
    orders = dig(data, "metrics", "orders", "models")
    detector = dig(data, "anomalies", "detectors", "seasonal_robust_z")
    plain = dig(data, "anomalies", "detectors", "plain_rolling_z")
    return [
        Metric(
            group,
            "National orders MAPE, ETS",
            show(dig(orders, "ets", "national_mape"), lambda v: f"{float(v):.2f}%"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "National orders sMAPE, ETS",
            show(dig(orders, "ets", "national_smape"), lambda v: f"{float(v):.2f}%"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "National orders MAPE, LightGBM",
            show(dig(orders, "lightgbm", "national_mape"), lambda v: f"{float(v):.2f}%"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "National orders MAPE, baseline",
            show(dig(orders, "seasonal_naive", "national_mape"), lambda v: f"{float(v):.2f}%"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Noise floor, national orders",
            show(dig(data, "poisson_floor_mape_orders", "National"), lambda v: f"{float(v):.2f}%"),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "ETS gain over baseline, all series",
            show(dig(orders, "ets", "mape_gain_vs_baseline_pct"), percent),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Anomaly recall, same-weekday detector",
            show(dig(detector, "recall"), share),
            PLANTED,
            source,
            when,
        ),
        Metric(
            group,
            "Anomaly precision, same-weekday detector",
            show(dig(detector, "precision"), share),
            PLANTED,
            source,
            when,
        ),
        Metric(
            group,
            "False alarms per 1,000 normal days",
            show(dig(detector, "false_alarms_per_1000_normal_days")),
            PLANTED,
            source,
            when,
        ),
        Metric(
            group,
            "Anomaly recall, plain z-score",
            show(dig(plain, "recall"), share),
            PLANTED,
            source,
            when,
        ),
    ]


def graph_metrics(src: Sources) -> list[Metric]:
    group = "Knowledge graph"
    load = src.art("graph_load.json")
    data = src.art("graph_metrics.json")
    source = "artifacts/graph_metrics.json"
    when = stamp(data)
    nodes = sum(dig(data, "nodes").values()) if dig(data, "nodes") else None
    relations = sum(dig(data, "relationships").values()) if dig(data, "relationships") else None
    full = dig(data, "full_data_checks")
    return [
        Metric(group, "Nodes", show(nodes, count), SAMPLE, source, when),
        Metric(group, "Relationships", show(relations, count), SAMPLE, source, when),
        Metric(
            group,
            "Load time",
            show(dig(load, "seconds"), seconds),
            SAMPLE,
            "artifacts/graph_load.json",
        ),
        Metric(
            group,
            "Chains found",
            show(dig(data, "communities", "chains_found"), count),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Groups with one menu under several brands",
            show(dig(data, "communities", "multi_brand_groups"), count),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Name purity of menu groups",
            show(dig(data, "communities", "name_purity"), share),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Name purity if names are shuffled",
            show(dig(data, "communities", "name_purity_if_shuffled"), share),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Restaurants with a menu",
            show(
                dig(data, "signal", "share_of_restaurants_with_a_menu"),
                lambda v: f"{float(v) * 100:.2f}%",
            ),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Dish pairs, naive test",
            show(dig(data, "co_ordered", "naive_significant_pairs"), count),
            SAMPLE,
            source,
            when,
        ),
        Metric(
            group,
            "Dish pairs, tested within a restaurant",
            show(dig(full, "dish_pairs_within_a_restaurant", "significant_pairs"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Cancel-heavy restaurants after correction",
            show(dig(full, "cancellations", "restaurants", "after_fdr"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Restaurants tested",
            show(dig(full, "cancellations", "restaurants", "tested"), count),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "PageRank vs order count (Spearman)",
            show(dig(data, "pagerank", "spearman_vs_order_count"), lambda v: f"{float(v):.2f}"),
            SAMPLE,
            source,
            when,
        ),
    ]


def recsys_metrics(src: Sources) -> list[Metric]:
    group = "Recommender audit"
    data = src.art("recsys_eval.json")
    source = "artifacts/recsys_eval.json"
    when = stamp(data)
    real = dig(data, "real_orders", "models")
    control = dig(data, "shuffled_control", "models")
    planted = dig(data, "planted_taste_SYNTHETIC", "models")
    measures = dig(data, "signal_audit", "measures")
    return [
        Metric(
            group,
            "Repeat orders, real",
            show(dig(measures, "repeat_share", "real"), share),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Repeat orders, if random",
            show(dig(measures, "repeat_share", "if_random"), share),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, random",
            show(dig(real, "random", "recall_at_10"), small),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, popularity",
            show(dig(real, "popularity", "recall_at_10"), small),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, city popularity",
            show(dig(real, "city_popularity", "recall_at_10"), small),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, matrix factorization",
            show(dig(real, "svd", "recall_at_10"), small),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "NDCG@10, matrix factorization",
            show(dig(real, "svd", "ndcg_at_10"), small),
            FULL,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, matrix factorization on shuffled orders",
            show(dig(control, "svd", "recall_at_10"), small),
            SHUFFLED,
            source,
            when,
        ),
        Metric(
            group,
            "Recall@10, matrix factorization on planted taste",
            show(dig(planted, "svd", "recall_at_10"), small),
            PLANTED,
            source,
            when,
        ),
    ]


def ai_metrics(src: Sources) -> list[Metric]:
    group = "AI evaluation"
    rag = src.result("rag_retrieval.json")
    answers = src.result("rag_answers.json")
    sql = src.result("text2sql.json")
    routing = src.result("agent_routing.json")
    enrich = src.result("enrichment.json")
    docs = "Project documents"
    rag_source = "eval/results/rag_retrieval.json"
    rows = [
        Metric(
            group,
            f"Recall@5, {mode}",
            show(dig(rag, "modes", key, "recall_at_5"), score),
            docs,
            rag_source,
        )
        for mode, key in (
            ("BM25", "bm25"),
            ("dense", "dense"),
            ("hybrid", "hybrid"),
            ("hybrid + rerank", "hybrid_rerank"),
        )
    ]
    rows += [
        Metric(
            group, "MRR, dense", show(dig(rag, "modes", "dense", "mrr"), score), docs, rag_source
        ),
        Metric(
            group, "MRR, hybrid", show(dig(rag, "modes", "hybrid", "mrr"), score), docs, rag_source
        ),
        Metric(
            group,
            "RAG faithfulness",
            show(dig(answers, "faithful_rate"), share),
            docs,
            "eval/results/rag_answers.json",
        ),
        Metric(
            group,
            "Text-to-SQL execution accuracy",
            show(dig(sql, "execution_accuracy"), share),
            FULL,
            "eval/results/text2sql.json",
        ),
        Metric(
            group,
            "Routing accuracy, keyword baseline",
            show(dig(routing, "keyword_router", "accuracy"), share),
            "Golden questions",
            "eval/results/agent_routing.json",
        ),
    ]
    llm = dig(routing, "llm_router", "accuracy")
    latency = src.result("rag_latency.json")
    rows.append(
        Metric(
            group,
            "Time a reranker adds to a search",
            show(dig(latency, "added_seconds"), lambda v: f"{float(v):.1f} s"),
            f"{docs}; varies by run",
            "eval/results/rag_latency.json",
        )
    )
    rows.append(
        Metric(
            group,
            "Routing accuracy, LLM router",
            show(llm, share),
            "Golden questions",
            "eval/results/agent_routing.json",
        )
    )
    rows.append(
        Metric(
            group,
            "Sentiment agreement with ratings",
            show(dig(enrich, "agreement_with_rating_reviews"), share),
            FULL,
            "eval/results/enrichment.json",
        )
    )
    return rows


def llm_cost_metrics(src: Sources, ledger: Path) -> list[Metric]:
    group = "LLM cost"
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    entries = [json.loads(line) for line in lines]
    total = sum(float(e["cost_usd"]) for e in entries)
    enrich = src.result("enrichment.json")
    spent = sum(float(e["cost_usd"]) for e in entries if e["purpose"] == "enrich_reviews")
    per_thousand = spent / dig(enrich, "reviews") * 1000 if enrich and spent else None
    source = "eval/replay/spend.jsonl"
    return [
        Metric(group, "Total spent", money(total), "All paid calls", source),
        Metric(group, "Paid calls", count(len(entries)), "All paid calls", source),
        Metric(group, "Cost per 1,000 enriched reviews", show(per_thousand, money), FULL, source),
    ]


def engineering_metrics(src: Sources, junit: Path) -> list[Metric]:
    group = "Engineering"
    coverage = src.art("coverage.json")
    text = junit.read_text(encoding="utf-8") if junit.exists() else ""
    match = re.search(r'errors="(\d+)" failures="(\d+)" skipped="(\d+)" tests="(\d+)"', text)
    passed = None
    if match:
        errors, failures, skipped, tests = (int(g) for g in match.groups())
        passed = tests - errors - failures - skipped
    attacks = len(ATTACK_PASSED.findall(text)) or None
    return [
        Metric(group, "Tests passing", show(passed, count), "Local run", "artifacts/pytest.xml"),
        Metric(
            group,
            "SQL attacks refused",
            show(attacks or None, count),
            "Local run",
            "artifacts/pytest.xml",
        ),
        Metric(
            group,
            "Line and branch coverage",
            show(dig(coverage, "totals", "percent_covered"), lambda v: f"{float(v):.2f}%"),
            "Local run",
            "artifacts/coverage.json",
        ),
    ]


def collect(artifacts: Path, results: Path, ledger: Path) -> list[Metric]:
    src = Sources(artifacts, results)
    return [
        *pipeline_metrics(src),
        *quality_metrics(src),
        *speed_metrics(src),
        *query_metrics(src),
        *airflow_metrics(src),
        *cloud_metrics(src),
        *forecast_metrics(src),
        *graph_metrics(src),
        *recsys_metrics(src),
        *ai_metrics(src),
        *llm_cost_metrics(src, ledger),
        *engineering_metrics(src, artifacts / "pytest.xml"),
    ]
