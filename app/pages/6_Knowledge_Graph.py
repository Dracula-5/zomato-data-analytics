"""Knowledge graph: what the Neo4j analysis found, read from the warehouse (no server needed)."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard import data
from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

chosen = page_setup("Knowledge Graph")
if chosen is not None:
    db = data.graph_warehouse(chosen)
    if db is None:
        st.info(
            "No graph results for this dataset. Start Neo4j with `make up-neo4j`, then run "
            "`make graph`, which uses the 5% sample."
        )
        st.stop()

    mode = theme_mode()
    signal = load("graph_signal", db)
    st.caption(
        "Orders are a SAMPLE of 5% of users. Restaurants, menus and dishes are complete. "
        "These results were exported from Neo4j and GDS, so the page runs without a server."
    )

    nodes = load("graph_nodes", db)
    relationships = load("graph_relationships", db)
    size = st.columns(2)
    size[0].metric("Nodes", f"{int(nodes['nodes'].sum()):,}")
    size[1].metric("Relationships", f"{int(relationships['relationships'].sum()):,}")
    table_view(nodes, "Nodes by label")
    table_view(relationships, "Relationships by type")

    st.subheader("Is there behaviour here to find?")
    cells = st.columns(3)
    cells[0].metric(
        "Restaurants with any menu data",
        f"{signal['share_of_restaurants_with_a_menu']:.1%}",
        help=f"Where a menu exists, {signal['share_of_those_on_the_menu']:.1%} of ordered lines "
        "are on it. So the gap is missing menus, not wrong orders.",
    )
    cells[1].metric(
        "Cities per customer, median",
        f"{signal['median_cities_per_user']:.0f}",
        help=f"The most for one customer is {signal['max_cities_per_user']:.0f}.",
    )
    cells[2].metric(
        "Orders repeating a customer-restaurant pair", f"{signal['repeat_order_share']:.1%}"
    )
    st.info(
        "Real customers order from a few places in one or two cities. These don't, so ordering "
        "in this SYNTHETIC dataset is close to random. The useful structure is in the menus, "
        "which look real."
    )

    st.subheader("Chains and shared kitchens, found from menus")
    st.caption(
        "Louvain over menu overlap. Restaurant names never enter the algorithm, so they are a "
        "fair check of the groups. A group with several names is often more interesting than "
        "a group with one: the same menu under different brands, usually in one or two cities."
    )
    scores = load("graph_scores", db)
    if "name_purity" in scores:
        tiles = st.columns(4)
        tiles[0].metric("Chains found", f"{scores['chains_found']:.0f}")
        tiles[1].metric(
            "Same menu, several brands",
            f"{scores['multi_brand_groups']:.0f}",
            help="Groups where two or more names each have several outlets. This looks like "
            "virtual brands run from shared kitchens, though the data can't prove ownership.",
        )
        tiles[2].metric(
            "Group shares one name",
            f"{scores['name_purity']:.0%}",
            help=f"With names shuffled at random: {scores['name_purity_if_shuffled']:.0%}.",
        )
        tiles[3].metric(
            "A chain's menus in one group",
            f"{scores['chain_recall']:.0%}",
            help="For chains with 10+ outlets that have a menu.",
        )
    communities = load("graph_communities", db)
    top = communities.head(12).iloc[::-1]
    bars = go.Figure(
        go.Bar(
            x=top["restaurants"],
            y=top["top_name"],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            text=[f"{v:,}" for v in top["restaurants"]],
            textposition="outside",
            customdata=top[["name_purity", "cities"]],
            hovertemplate=(
                "%{y}<br>%{x:,} restaurants in %{customdata[1]} cities<br>"
                "Same name: %{customdata[0]:.0%}<extra></extra>"
            ),
        )
    )
    bars.update_layout(title="Largest menu communities, by commonest name")
    st.plotly_chart(style(bars, mode, height=420), width="stretch")
    table_view(communities, "Communities as a table")

    st.subheader("Dishes bought together")
    st.write(
        f"Tested across all orders, **{scores['naive_significant_pairs']:,.0f}** pairs look "
        f"linked. Tested within each restaurant: "
        f"**{scores['significant_within_a_restaurant']:,.0f}**. "
        "Each restaurant here sells a small set of dishes, so any two of them share orders across "
        "the whole dataset. That is the restaurant, not a buying habit."
    )
    pairs = load("graph_pairs", db)
    if len(pairs):
        table_view(pairs, "Dish pairs as a table")

    st.subheader("Cancel-heavy restaurants and customers")
    st.write(
        "A list ranked by raw cancel rate would look alarming, but its extremes are what chance "
        "produces. After correcting for testing thousands at once, nothing survives. The last "
        "column shows that this means something: on SYNTHETIC outliers planted at 5 times the "
        "normal rate, it is the share the same test finds. The sample is too thin to judge "
        "restaurants, which is why the full data was tested too."
    )
    st.dataframe(
        load("graph_outlier_tests", db),
        hide_index=True,
        width="stretch",
        column_config={
            "test": "Test",
            "data": "Data",
            "tested": st.column_config.NumberColumn("Tested", format="localized"),
            "raw_p_below_5pct": st.column_config.NumberColumn("p below 5%", format="localized"),
            "after_fdr": st.column_config.NumberColumn("After correction"),
            "finds_planted_5x": st.column_config.NumberColumn(
                "Finds planted 5x outliers", format="percent"
            ),
        },
    )
    outliers = load("graph_outliers", db)
    if len(outliers):
        table_view(outliers, "Outliers as a table")

    st.subheader("Two-hop recommendations")
    st.caption(
        "An example for one customer: restaurants with menus like ones they used, that they "
        "haven't tried. M10 audits whether any recommender can work on this data."
    )
    st.dataframe(load("graph_recommendations", db), hide_index=True, width="stretch")

    st.subheader("Most central restaurants (PageRank)")
    if "pagerank_vs_order_count" in scores:
        st.write(
            f"PageRank correlates **{scores['pagerank_vs_order_count']:.2f}** with plain order "
            "count, so on this data it adds little beyond counting."
        )
    table_view(load("graph_top_restaurants", db), "Top 50 as a table")
