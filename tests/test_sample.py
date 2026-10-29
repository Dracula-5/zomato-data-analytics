import duckdb
import pytest

from zap.ingest import sample


def test_sql_bucket_equals_python_bucket() -> None:
    sql = f"select u, {sample.bucket_sql('u')} from range(1, 5001) t(u) order by u"
    rows = duckdb.connect().execute(sql).fetchall()
    assert all(b == sample.bucket(u) for u, b in rows)


@pytest.mark.parametrize("pct", [1.0, 5.0, 20.0])
def test_sample_fraction_is_close_to_target(pct: float) -> None:
    users = range(1, 100_001)
    share = sum(sample.in_sample(u, pct) for u in users) / len(users) * 100
    assert abs(share - pct) < 0.3


def test_samples_are_nested() -> None:
    assert all(sample.in_sample(u, 5) for u in range(1, 20_001) if sample.in_sample(u, 1))


def test_full_sample_keeps_everyone() -> None:
    assert all(sample.in_sample(u, 100) for u in range(1, 1001))


@pytest.mark.parametrize("bad", [0, -1, 100.5])
def test_threshold_rejects_out_of_range(bad: float) -> None:
    with pytest.raises(ValueError, match="sample percent"):
        sample.threshold(bad)


@pytest.mark.parametrize(
    ("pct", "label"), [(100, "full"), (5, "sample_5pct"), (0.5, "sample_0p5pct")]
)
def test_label(pct: float, label: str) -> None:
    assert sample.label(pct) == label
