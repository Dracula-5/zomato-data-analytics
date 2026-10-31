# ADR 4: Building the graph on menus, not orders

**Status:** accepted

## Context

The natural knowledge graph connects customers to what they buy. But a signal audit before
building showed that order behaviour is random: the median customer orders in 32 cities, and
almost nobody returns to a restaurant. A graph can't find what isn't there.

## Decision

Similarity comes from menus, which look real. `SIMILAR_TO` links restaurants by Jaccard overlap of
their dishes, and Louvain groups them. Restaurant names never enter the algorithm, which makes them
a fair way to judge the groups afterwards.

Every claim about orders then has to survive a test with the right null hypothesis, and a test of
whether that method could have found anything.

## Consequences

**Good:** it finds chains without being given names. It also finds groups with one menu under
several brands, which look like virtual brands from shared kitchens. Matching on names could never
find those, because the names differ by design. That can't be proved from this data alone.

**Three apparent findings failed:**

- *"92% of order lines aren't on the menu."* Only 8.09% of restaurants have menu data, and where it
  exists, every line matches. A missing value had been read as a wrong value.
- *"9,791 significant dish pairs."* The test assumed dishes are independent across all orders, but
  each restaurant sells a small pool of dishes. Tested within each restaurant, none remain.
- *"4,595 cancel-heavy restaurants."* Testing 148,103 restaurants at 5% can produce up to 7,405
  by chance. After false discovery rate correction, none remain.

**Bad:** only restaurants with a menu can be grouped, so most restaurants are left out. Tests of
order behaviour run on the full data in DuckDB, because the 5% sample is too thin to judge them.

## Alternatives

- **Customer-restaurant graph:** the textbook design. PageRank on it correlates 0.98 with plain
  order counts, so it adds almost nothing here.
- **Load all 10 million orders:** more impressive numbers, but no more signal.
