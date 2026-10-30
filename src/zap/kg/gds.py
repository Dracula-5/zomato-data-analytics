"""Graph Data Science: menu similarity, communities over it, and PageRank over ordering.

Order behaviour in this data is near random, so similarity comes from menus, which are real.
Settings are fixed in advance. Each run first drops what the last one wrote, because
`nodeSimilarity.write` adds new relationships rather than replacing old ones.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from zap.kg.load_neo4j import cypher

CONCURRENCY = 1  # parallel runs of these algorithms give slightly different results each time
TOP_K = 10
SIMILARITY_CUTOFF = 0.5  # the two menus share at least half of all their dishes together
MIN_MENU_SIZE = 5  # below this, a shared dish or two would look like a strong match
PAGERANK_DAMPING = 0.85
PAGERANK_MAX_ITERATIONS = 100  # the default of 20 stopped before converging on this graph
PROJECTIONS = ("menus", "similar", "ordering")


def _single(driver: Driver, text: str, **params: Any) -> dict[str, Any]:
    with driver.session() as session:
        return session.run(cypher(text), params).single(strict=True).data()


def _clean_up(driver: Driver) -> None:
    with driver.session() as session:
        for name in PROJECTIONS:
            session.run(
                cypher("CALL gds.graph.drop($name, false) YIELD graphName"), name=name
            ).consume()
        session.run(
            cypher(
                "MATCH ()-[r:SIMILAR_TO]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS"
            )
        ).consume()


def menu_similarity(driver: Driver) -> dict[str, Any]:
    """SIMILAR_TO: Jaccard overlap of menus, keeping each restaurant's closest matches."""
    _single(
        driver,
        "CALL gds.graph.project('menus', ['Restaurant', 'FoodItem'], 'SERVES') "
        "YIELD nodeCount RETURN nodeCount",
    )
    return _single(
        driver,
        """CALL gds.nodeSimilarity.write('menus', {
             writeRelationshipType: 'SIMILAR_TO', writeProperty: 'jaccard',
             topK: $top_k, similarityCutoff: $cutoff, degreeCutoff: $min_menu,
             concurrency: $concurrency})
           YIELD nodesCompared, relationshipsWritten
           RETURN nodesCompared, relationshipsWritten""",
        top_k=TOP_K,
        cutoff=SIMILARITY_CUTOFF,
        min_menu=MIN_MENU_SIZE,
        concurrency=CONCURRENCY,
    )


def communities(driver: Driver) -> dict[str, Any]:
    """Louvain over SIMILAR_TO, stored on each restaurant as `community`."""
    _single(
        driver,
        """MATCH (a:Restaurant)-[s:SIMILAR_TO]->(b:Restaurant)
           WITH gds.graph.project('similar', a, b,
                  {relationshipProperties: {weight: s.jaccard}},
                  {undirectedRelationshipTypes: ['*']}) AS graph
           RETURN graph.nodeCount AS nodeCount""",
    )
    return _single(
        driver,
        """CALL gds.louvain.write('similar', {
             writeProperty: 'community', relationshipWeightProperty: 'weight',
             concurrency: $concurrency})
           YIELD communityCount, modularity, nodePropertiesWritten
           RETURN communityCount, modularity, nodePropertiesWritten""",
        concurrency=CONCURRENCY,
    )


def pagerank(driver: Driver) -> dict[str, Any]:
    """PageRank over customers and the restaurants they ordered from."""
    _single(
        driver,
        """MATCH (u:User)-[:ORDERED]->(:Order)-[:FROM]->(r:Restaurant)
           WITH u, r, count(*) AS orders
           WITH gds.graph.project('ordering', u, r,
                  {relationshipProperties: {orders: orders}},
                  {undirectedRelationshipTypes: ['*']}) AS graph
           RETURN graph.nodeCount AS nodeCount""",
    )
    return _single(
        driver,
        """CALL gds.pageRank.write('ordering', {
             writeProperty: 'pagerank', relationshipWeightProperty: 'orders',
             dampingFactor: $damping, maxIterations: $iterations, concurrency: $concurrency})
           YIELD ranIterations, didConverge, nodePropertiesWritten
           RETURN ranIterations, didConverge, nodePropertiesWritten""",
        damping=PAGERANK_DAMPING,
        iterations=PAGERANK_MAX_ITERATIONS,
        concurrency=CONCURRENCY,
    )


def run_gds(driver: Driver) -> dict[str, Any]:
    _clean_up(driver)
    try:
        similarity = menu_similarity(driver)
        # Louvain needs at least one edge, and a small dataset may have no similar menus.
        matched = similarity["relationshipsWritten"] > 0
        return {
            "similarity": similarity,
            "communities": communities(driver) if matched else {"communityCount": 0},
            "pagerank": pagerank(driver),
            "settings": {
                "top_k": TOP_K,
                "similarity_cutoff": SIMILARITY_CUTOFF,
                "min_menu_size": MIN_MENU_SIZE,
                "pagerank_damping": PAGERANK_DAMPING,
            },
        }
    finally:
        # Projections live in the heap, and the results are already written to the graph.
        with driver.session() as session:
            for name in PROJECTIONS:
                session.run(
                    cypher("CALL gds.graph.drop($name, false) YIELD graphName"), name=name
                ).consume()
