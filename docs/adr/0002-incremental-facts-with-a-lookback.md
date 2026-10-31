# ADR 2: Incremental facts with a lookback window

**Status:** accepted

## Context

Rebuilding 10 million orders every day is wasteful, so the facts load incrementally. The common
approach is `order_timestamp > max(order_timestamp)`, but it misses orders that arrive late or
change after they first load, and it does so silently.

## Decision

Each run reprocesses the last three days of order dates and merges on the key. The cutoff is a
literal date computed when the model compiles, so the database can skip old partitions.

A related rule covers late-arriving parents: `fct_reviews` also rechecks reviews whose order falls
in the window, because a review can arrive before its order does.

## Consequences

**Good:** late orders are corrected, and repeated runs give the same result. A fingerprint test
confirms that day-by-day runs match a full rebuild.

**Bad:** the window is a guess at how late data can be, and a three-day merge touches more rows
than a pure append.

**What measuring showed:** "incremental" in the config doesn't mean incremental in practice. The
first version saved 2.3% on DuckDB because predicate pushdown was blocked. After the fix, the
fact models themselves save 91.8% there. The whole project doesn't reliably save anything, because
the marts and quality rules rebuild either way, and it varied more between runs than the saving
was worth. On Snowflake the whole-project saving is small too, because an X-Small warehouse
rebuilds this data quickly anyway. See [METRICS.md](../METRICS.md). The honest lesson is that
incremental models pay off when the rebuild is the slow part, and on this data it isn't.

## Alternatives

- **Full refresh every day:** simplest and always correct, and at this size quite reasonable.
- **Timestamp watermark only:** cheaper, but it silently loses late data.
