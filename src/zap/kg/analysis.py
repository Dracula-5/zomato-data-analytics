"""Run the graph queries, judge the results, and publish them for the dashboard.

Results go to the `kg` schema of the dataset's DuckDB file, so the dashboard works without
Neo4j running, and to `artifacts/graph_metrics.json`, for METRICS.md.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from neo4j import Driver
from scipy.stats import spearmanr

from zap.kg import stats
from zap.kg.full_checks import run_full_checks
from zap.kg.gds import run_gds
from zap.kg.load_neo4j import cypher
from zap.sqlutil import save_frames

QUERY_DIR = Path(__file__).parent / "queries"
MIN_ORDERS_TO_TEST = 20
MIN_TIMES_TOGETHER = 3
MIN_DISH_ORDERS = 5
MIN_COMMUNITY_SIZE = 3
CHAIN_MIN_OUTLETS = 5
CHAIN_MIN_PURITY = 0.8
CHAIN_RECALL_MIN_MENUS = 10
MULTI_BRAND_MIN_OUTLETS = 3
SHUFFLES = 20
SEED = 7
COMMUNITY_COLUMNS = [
    "community",
    "restaurants",
    "top_name",
    "name_purity",
    "top_cuisine",
    "cuisine_purity",
    "cities",
]


def run_query(driver: Driver, name: str, **params: Any) -> pd.DataFrame:
    text = (QUERY_DIR / f"{name}.cypher").read_text(encoding="utf-8")
    with driver.session() as session:
        return pd.DataFrame([record.data() for record in session.run(cypher(text), params)])


def chain_key(name: str | None) -> str:
    """'Domino's Pizza', 'DOMINOS PIZZA!' -> the same key, to compare outlets of a chain."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").replace("'", "").lower()).strip()


def _largest_share(values: pd.Series) -> float:
    return float(values.value_counts().iloc[0] / len(values)) if len(values) else 0.0


def _weighted_purity(frame: pd.DataFrame, labels: pd.Series) -> float:
    """Across communities, the share of restaurants carrying their community's commonest name."""
    grouped = labels.groupby(frame["community"].to_numpy(), sort=False)
    return float(grouped.agg(lambda names: names.value_counts().iloc[0]).sum() / len(frame))


def community_table(members: pd.DataFrame) -> pd.DataFrame:
    """One row per community, largest first, with the names and cuisines that define it."""
    rows = []
    for _, group in members.groupby("community", sort=False):
        keys = group["name"].map(chain_key)
        rows.append(
            {
                "community": int(group["community"].iloc[0]),
                "restaurants": len(group),
                "top_name": str(group["name"].mode().iloc[0]),
                "name_purity": round(_largest_share(keys), 4),
                "top_cuisine": str(group["cuisine"].mode().iloc[0])
                if group["cuisine"].any()
                else "",
                "cuisine_purity": round(_largest_share(group["cuisine"].dropna()), 4),
                "cities": int(group["city"].nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values(["restaurants", "community"], ascending=[False, True])


def _no_communities(restaurants: int = 0, communities: int = 0) -> dict[str, Any]:
    return {
        "restaurants_in_a_community": restaurants,
        "communities": communities,
        "communities_of_3_or_more": 0,
        "restaurants_in_those": 0,
        "name_purity": None,
        "name_purity_if_shuffled": None,
        "chains_found": 0,
    }


def judge_communities(
    members: pd.DataFrame, seed: int = SEED
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Restaurant names never enter the algorithm, so they are a fair check of its groups.
    Shuffling the names over the same groups shows the purity that chance alone gives."""
    if members.empty:
        return pd.DataFrame(columns=COMMUNITY_COLUMNS), _no_communities()
    sizes = members.groupby("community")["id"].transform("size")
    judged = members[sizes >= MIN_COMMUNITY_SIZE].reset_index(drop=True)
    if judged.empty:
        return pd.DataFrame(columns=COMMUNITY_COLUMNS), _no_communities(
            len(members), int(members["community"].nunique())
        )
    table = community_table(judged)
    keys = judged["name"].map(chain_key)
    rng = np.random.default_rng(seed)
    shuffled = [
        _weighted_purity(judged, pd.Series(rng.permutation(keys.to_numpy())))
        for _ in range(SHUFFLES)
    ]
    chains = table[
        (table["restaurants"] >= CHAIN_MIN_OUTLETS) & (table["name_purity"] >= CHAIN_MIN_PURITY)
    ]
    return table, {
        "restaurants_in_a_community": len(members),
        "communities": int(members["community"].nunique()),
        "communities_of_3_or_more": len(table),
        "restaurants_in_those": len(judged),
        "name_purity": round(_weighted_purity(judged, keys), 4),
        "name_purity_if_shuffled": round(float(np.mean(shuffled)), 4),
        "chains_found": len(chains),
    }


def multi_brand_groups(members: pd.DataFrame) -> int:
    """Communities where at least two different names each have several outlets: one menu under
    several brands. Name matching can't link these, because the names differ by design."""
    keys = members.assign(key=members["name"].map(chain_key))
    outlets = keys.groupby(["community", "key"]).size()
    repeated = outlets[outlets >= MULTI_BRAND_MIN_OUTLETS]
    return int((repeated.groupby(level="community").size() >= 2).sum())


def chain_recall(members: pd.DataFrame, with_menus: pd.DataFrame) -> dict[str, Any]:
    """For chains with enough outlets, the share of those with a menu in one community."""
    if members.empty or with_menus.empty:
        return {"chains": 0, "share_in_largest_community": None}
    outlets = with_menus["name"].map(chain_key).value_counts()
    chains = outlets[outlets >= CHAIN_RECALL_MIN_MENUS]
    keyed = members.assign(key=members["name"].map(chain_key))
    keyed = keyed[keyed["key"].isin(chains.index)]
    largest = keyed.groupby(["key", "community"]).size().groupby(level="key").max()
    return {
        "chains": len(chains),
        "share_in_largest_community": round(float(largest.sum() / chains.sum()), 4),
    }


def judge_pagerank(ranked: pd.DataFrame) -> dict[str, Any]:
    """If PageRank just re-sorts by order count, the plain count would do."""
    if ranked["orders"].nunique() < 2:
        return {"restaurants": len(ranked), "spearman_vs_order_count": None}
    correlation = spearmanr(ranked["pagerank"], ranked["orders"]).statistic
    return {"restaurants": len(ranked), "spearman_vs_order_count": round(float(correlation), 4)}


def cancel_tests(driver: Driver) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    base = run_query(driver, "base_cancel_rate").iloc[0]
    rate = float(base["rate"])
    rng = np.random.default_rng(SEED)
    summary: dict[str, Any] = {"base_rate": round(rate, 4), "min_orders": MIN_ORDERS_TO_TEST}
    outliers = {}
    for entity, query in (
        ("restaurants", "cancel_heavy_restaurants"),
        ("users", "cancel_heavy_users"),
    ):
        counts = run_query(driver, query, min_orders=MIN_ORDERS_TO_TEST)
        if counts.empty:  # a small dataset can have nobody with enough orders to test
            counts = pd.DataFrame(columns=["id", "orders", "cancelled"], dtype=int)
            summary[entity] = {"tested": 0, "raw_p_below_alpha": 0, "after_fdr": 0}
            outliers[entity] = counts.assign(p=0.0, q=0.0)
            continue
        scored = stats.rate_outliers(counts, rate)
        summary[entity] = {
            **stats.outlier_summary(scored),
            "planted_power_SYNTHETIC": stats.planted_power(scored["orders"].to_numpy(), rate, rng),
        }
        outliers[entity] = scored[scored["q"] < stats.ALPHA]
    return summary, outliers


def co_ordered(driver: Driver) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Dish pairs, tested two ways. The gap between them is the finding."""
    universe = run_query(driver, "dishes_ordered", min_orders=MIN_DISH_ORDERS).iloc[0]
    dishes, orders = int(universe["dishes"]), int(universe["orders"])
    possible = dishes * (dishes - 1) // 2
    naive = run_query(
        driver, "co_ordered_dishes", min_both=MIN_TIMES_TOGETHER, min_orders=MIN_DISH_ORDERS
    )
    naive_hits = 0
    if not naive.empty:
        both, count_a, count_b = (naive[c].to_numpy() for c in ("both", "orders_a", "orders_b"))
        q = stats.benjamini_hochberg(stats.together_p(both, count_a, count_b, orders), possible)
        naive_hits = int((q < stats.ALPHA).sum())

    within = run_query(driver, "co_ordered_in_restaurant", min_both=MIN_TIMES_TOGETHER)
    tests = int(run_query(driver, "restaurant_dish_pairs")["pairs"].iloc[0] or 0)
    columns = ["restaurant", "name_a", "name_b", "both", "orders_a", "orders_b", "orders_here", "q"]
    significant = pd.DataFrame(columns=columns)
    if not within.empty:
        p = stats.pairs_within_p(
            within["both"].to_numpy(),
            within["orders_a"].to_numpy(),
            within["orders_b"].to_numpy(),
            within["orders_here"].to_numpy(),
        )
        scored = within.assign(q=stats.benjamini_hochberg(p, tests))
        significant = scored[scored["q"] < stats.ALPHA][columns]
    return significant, {
        "naive_pairs_seen_3_or_more_times": len(naive),
        "naive_significant_pairs": naive_hits,
        "pairs_within_a_restaurant_tested": len(within),
        "possible_pairs_within_restaurants": tests,
        "significant_within_a_restaurant": len(significant),
    }


def outlier_table(report: dict[str, Any]) -> pd.DataFrame:
    """Every outlier test on one table: what it claimed, what survived, what it could see."""
    rows = []
    sources = [("5% sample", report["cancellations"], report["co_ordered"])]
    checks = report["full_data_checks"]
    if isinstance(checks, dict):
        sources.append(("Full data", checks["cancellations"], None))
    for data_name, cancels, _ in sources:
        for entity, label in (
            ("restaurants", "Cancel-heavy restaurants"),
            ("users", "Cancel-heavy customers"),
        ):
            result = cancels[entity]
            power = result.get("planted_power_SYNTHETIC", {}).get("5x", {})
            rows.append(
                {
                    "test": label,
                    "data": data_name,
                    "tested": result["tested"],
                    "raw_p_below_5pct": result["raw_p_below_alpha"],
                    "after_fdr": result["after_fdr"],
                    "finds_planted_5x": power.get("recall"),
                }
            )
    if isinstance(checks, dict):
        pairs = checks["dish_pairs_within_a_restaurant"]
        power = pairs.get("planted_power_SYNTHETIC", {}).get("5x", {})
        rows.append(
            {
                "test": "Dish pairs within a restaurant",
                "data": "Full data",
                "tested": pairs["pairs_tested"],
                "raw_p_below_5pct": None,
                "after_fdr": pairs["significant_pairs"],
                "finds_planted_5x": power.get("recall"),
            }
        )
    return pd.DataFrame(rows)


def score_table(report: dict[str, Any]) -> pd.DataFrame:
    """Single numbers for the dashboard. Missing ones are left out, not shown as zero."""
    communities = report["communities"]
    scores = {
        **report["co_ordered"],
        "chains_found": communities["chains_found"],
        "multi_brand_groups": communities["multi_brand_groups"],
        "name_purity": communities["name_purity"],
        "name_purity_if_shuffled": communities["name_purity_if_shuffled"],
        "chain_recall": communities["chain_recall"]["share_in_largest_community"],
        "pagerank_vs_order_count": report["pagerank"]["spearman_vs_order_count"],
    }
    known = {name: float(value) for name, value in scores.items() if value is not None}
    return pd.DataFrame(known.items(), columns=["measure", "value"])


def run_analysis(
    driver: Driver, db_path: Path, artifacts_dir: Path, full_db: Path | None = None
) -> dict[str, Any]:
    gds_result = run_gds(driver)
    nodes = run_query(driver, "nodes_by_label")
    relationships = run_query(driver, "relationships_by_type")

    menu = run_query(driver, "menu_integrity").iloc[0]
    coverage = run_query(driver, "menu_coverage").iloc[0]
    spread = run_query(driver, "user_spread").iloc[0]
    signal = {
        "restaurants": int(coverage["restaurants"]),
        "restaurants_with_a_menu": int(coverage["with_menu"]),
        "share_of_restaurants_with_a_menu": round(
            float(coverage["with_menu"] / coverage["restaurants"]), 4
        ),
        "order_lines": int(menu["order_lines"]),
        "share_of_lines_with_a_menu": round(
            float(menu["lines_with_a_menu"] / menu["order_lines"]), 4
        ),
        "share_of_those_on_the_menu": round(
            float(menu["lines_on_the_menu"] / menu["lines_with_a_menu"]), 4
        )
        if menu["lines_with_a_menu"]
        else 0.0,
        "users": int(spread["users"]),
        "median_cities_per_user": float(spread["median_cities"]),
        "max_cities_per_user": int(spread["max_cities"]),
        "repeat_order_share": round(
            1 - float(spread["user_restaurant_pairs"] / spread["orders"]), 4
        ),
    }

    members = run_query(driver, "restaurant_communities")
    communities, community_scores = judge_communities(members)
    with_menus = run_query(driver, "restaurants_with_menus")
    community_scores["multi_brand_groups"] = multi_brand_groups(members) if len(members) else 0
    community_scores["chain_recall"] = chain_recall(members, with_menus)
    ranked = run_query(driver, "pagerank_restaurants")
    cancels, outliers = cancel_tests(driver)
    pairs, pair_scores = co_ordered(driver)

    user = run_query(driver, "busiest_user")
    example = (
        run_query(driver, "two_hop_recommendations", user=user["id"].iloc[0], limit=10)
        if len(user)
        else pd.DataFrame(columns=["id", "name", "city", "via_restaurants", "score"])
    )

    report: dict[str, Any] = {
        "dataset": db_path.stem,
        "data": "Orders are a SAMPLE of 5% of users. Restaurants, menus and dishes are complete.",
        "nodes": dict(zip(nodes["label"], nodes["nodes"].astype(int), strict=True)),
        "relationships": dict(
            zip(
                relationships["relationship"],
                relationships["relationships"].astype(int),
                strict=True,
            )
        ),
        "signal": signal,
        "gds": gds_result,
        "communities": community_scores,
        "pagerank": judge_pagerank(ranked),
        "cancellations": cancels,
        "co_ordered": pair_scores,
        "full_data_checks": run_full_checks(full_db) if full_db else "not run",
        "ran_at": datetime.now(UTC).isoformat(),
    }
    save_frames(
        db_path,
        "kg",
        {
            "nodes": nodes,
            "relationships": relationships,
            "signal": pd.DataFrame(signal.items(), columns=["measure", "value"]).astype(
                {"value": float}
            ),
            "communities": communities.head(200),
            "top_restaurants": ranked.head(50),
            "cancel_outliers": pd.concat(
                [frame.assign(kind=kind) for kind, frame in outliers.items()], ignore_index=True
            ).reindex(columns=["kind", "id", "orders", "cancelled", "p", "q"]),
            "co_ordered": pairs.head(200).rename(columns={"both": "both_orders"}),
            "scores": score_table(report),
            "outlier_tests": outlier_table(report),
            "recommendations": example,
        },
    )
    path = artifacts_dir / "graph_metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, default=int) + "\n", encoding="utf-8", newline="\n"
    )
    return report
