# ADR 3: Guarding generated SQL by parsing it

**Status:** accepted

## Context

Text-to-SQL runs code written by a model, so its output has to be treated as untrusted input. The
usual defence is a blacklist of words like `DROP`. That misses `COPY`, `read_csv` and `ATTACH`, and
it blocks harmless queries that mention those words in a string.

## Decision

There are two independent layers.

1. **A guard that parses the SQL with sqlglot.** It must be one read-only statement over allowed
   tables, with no function that can reach files, settings or the network. Only the SQL rebuilt
   from the parse tree runs, with comments removed, and it always has a row limit.
2. **A connection that can't do harm.** It is read-only, with external access disabled and locked,
   plus a memory limit and a timeout. Tests bypass the guard to prove this layer holds alone.

Personal data is handled by access, not by blocking column names: generated SQL can only read
`dim_customer_public`, which has no email columns at all.

## Consequences

**Good:** 92 attack queries are refused, and the checks run in CI at no cost.

**Why the public view exists:** my first guard blocked the columns `email_hash` and `email_masked`
by name. Trying to break it found that `SELECT c FROM marts.dim_customer c` returns whole rows,
emails included, without naming any column. `SELECT COLUMNS('.*')` does the same. That is exactly
the weakness of a blocklist, so the columns were removed from what generated SQL can reach.

**Bad:** an allowlist has to be maintained, and a new mart stays invisible to the copilot until
someone adds it.

## Alternatives

- **Keyword blacklist:** quick, and bypassable.
- **Read-only connection alone:** stops writes, but not reads of personal data.
- **Semantic layer:** the safest option, and far more to build for a project this size.
