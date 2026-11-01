# ADR 6: A labelled simulation, on a SQLite log

**Status:** accepted

## Context

The platform processed data in batches, and a daily job can't show what's happening now. A live
view needs two things: a stream of events, and somewhere for them to go.

No real stream exists. There is no public feed of Zomato orders, and this dataset ends in June 2026.
The obvious broker is Kafka, or Redpanda on a laptop. It would need another container on a machine
that already runs out of memory with Neo4j and Airflow together, plus client libraries and a CI job.

## Decision

**Replay recent orders as a stream, and say so.** A simulator sends past orders as events on a
moving clock, one simulated minute a second. Every page and document calls it SIMULATED. Outages are
injected and called SYNTHETIC. What's real is the engineering, not the events.

**Use SQLite in WAL mode as the log.** It keeps what matters about a broker: an append-only
sequence, and a consumer offset saved in the same transaction as the counts it produced, so a
restart neither loses nor double-counts an order. WAL lets the dashboard read while the feed writes,
which a DuckDB file doesn't allow across processes. It's free, and it runs without Docker.

**Count events by when they happened, not when they arrived,** and only judge a minute after a
3-minute watermark, so late events land in the right window.

**Test windows with the exact Poisson tail.** The first version used a z-score over 15 minutes and
caught only 8 of 14 total outages, because smaller cities never had enough orders to pass. It also
raised 9 false alarms, all spikes, because a growing business outruns an average of past weeks. The
exact tail works at low counts, a 30-minute window gives smaller cities a chance, and the baseline
now carries its own growth forward. The final scores come from days this design never saw.

## Consequences

**Good:** it costs nothing, starts with one click, and shows offsets, watermarks, late events and
stream alerting working. Everything is measured, including how often it cries wolf.

**Bad:** a single SQLite writer doesn't scale, and it has no partitions, replication or consumer
groups. A real deployment would use Kafka, and the `Store` class is the only part that would change.

**Limits:** the feed proves the pipeline, not the business. It has no real incidents in it, and a
30% loss in one city is still too small to see in 30 minutes.
