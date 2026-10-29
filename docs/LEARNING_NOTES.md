# Learning notes

Plain-language explanations of every major component, written so you can defend each
choice in an interview. One section per milestone, added as the project grows.

---

## M0: Project scaffold and engineering practice

### Why a Python package (`src/zap`) instead of loose scripts?

Loose scripts copy code into each other and are hard to test. A package is one importable
unit: `from zap.config import get_settings` works the same in tests, in the Airflow
container and in the Streamlit app.

The **src layout** (`src/zap/`, not `zap/` at the root) has one job. Tests import the
*installed* package, not whatever happens to sit in the current folder, so "works on my
machine" import bugs show up locally.

### uv and `uv.lock`

`uv` installs Python and the dependencies. `pyproject.toml` declares the *allowed* ranges,
such as `duckdb>=1.1`. `uv.lock` records the *exact* versions that were resolved.

- CI runs `uv sync --locked`, which fails if the lock file is out of date. That way CI tests
  exactly what you tested.
- **Dependency groups** (`dev` now; `dbt`, `ai` and others later) keep heavy libraries
  apart, so a change in one area doesn't pull a conflicting version into another.

*Interview line:* "Ranges in pyproject, exact pins in the lock file, and `--locked` in CI,
so builds are reproducible."

### Configuration (`config.py`)

There's one `Settings` class built on `pydantic-settings`. Each field is read from an
environment variable of the same name (`data_dir` comes from `DATA_DIR`), or from the
`.env` file.

- **Why env vars:** the same code runs locally, in Docker and in CI with different settings
  and no code changes. This is the "config in the environment" rule from the Twelve-Factor App.
- **Validation:** `sample_pct` must be between 0 and 100, and `target` must be `duckdb` or
  `snowflake`. A typo fails at start-up instead of halfway through a pipeline.
- **Secrets** are typed `SecretStr`, which prints as `**********`. `zap info` shows only
  whether a secret is set, never its value.
- `.env` is gitignored. `.env.example` documents every variable with empty values.

### The synthetic fixture (`fixtures.py`)

The real dataset is 1.4 GB and can't go on GitHub, but CI still has to run the pipeline. So
the code generates a small fake dataset (`make fixtures`) with the **same file names,
columns and quirks** as the real one:

- dimension files start with an unnamed pandas index column and use Windows CRLF line endings;
- fact files use LF line endings;
- restaurant fields look like the scraped source (`--` ratings, `₹ 200` costs, `Area,City`),
  and one quoted address contains a newline.

It's **deterministic**: the same seed always produces byte-identical files, and a test
checks this. Deterministic tests don't flake.

It also **injects known defects on purpose**: a duplicate order, a total that doesn't add
up, an order for a restaurant that doesn't exist, reviews dated before their order,
reviews pointing at the wrong restaurant, and so on. Because the fixture knows exactly how
many it injected, later data-quality code can be tested with "did it catch exactly these?"

*Interview line:* "CI never touches real data. It runs on a seeded synthetic fixture that
reproduces the real files' quirks and plants known defects, so the quality checks get a
precise pass/fail test."

### Tests, coverage and the 80% gate

- `pytest` runs the tests. `pytest-cov` measures which lines and branches ran.
- `fail_under = 80` in `pyproject.toml` makes the test run **fail** if coverage drops below
  80%, so the target is enforced, not aspirational.
- Coverage is measured on `src/zap` only. Any exclusions will be listed in METRICS.md.
- An autouse test fixture clears environment variables and changes into a temp folder, so
  your real `.env` can never leak into a test.

### Lint, format and type check

- **ruff** is both the linter and the formatter. It runs extra rule sets: `S` (bandit
  security checks such as SQL built from strings), `B` (likely bugs), `UP` (modern syntax).
- **mypy `--strict`** requires every function to be fully typed. It caught real mistakes in
  M0, for example a variable reused with two different types.
- `make check` runs all three. It's the same command CI runs.

### pre-commit hooks

These are git hooks that run before each commit:

- trailing whitespace, a final newline, valid YAML and TOML, no merge-conflict markers;
- a block on files over 800 KB, so data can't be committed by accident;
- private-key detection;
- **gitleaks**, which scans for API keys and tokens;
- ruff and mypy.

Ruff and mypy run from the project's own venv (`language: system`), so their versions match
`uv.lock` exactly.

### Makefile as a thin wrapper

Each target is one `uv run ...` line, so it behaves the same in PowerShell, Git Bash and on
Linux CI. You can always run the command by hand without `make`. `make help` lists every
target.

### CI (GitHub Actions)

On every push or PR, CI checks out the code with full history (gitleaks needs it),
installs the exact locked versions, runs the generic hooks and the secret scan, then runs
`make check`. Test reports are uploaded as build artifacts.

### Things that went wrong (and what they teach)

- **Windows console encoding:** printing a table with box-drawing characters crashed under
  cp1252. The fix is `PYTHONUTF8=1`, set in the Makefile and in CI.
- **DuckDB 2.0 API change:** `Relation.fetchone()` was removed, because relations now re-run
  on every fetch. The tests use `fetchall()` on relations, or `execute(...).fetchone()` on a
  connection. The lesson: pin versions with a lock file and read changelogs on major upgrades.
