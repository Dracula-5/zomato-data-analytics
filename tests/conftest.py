import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import CONTRACTS_DIR
from zap.config import get_settings
from zap.fixtures import FixtureResult, FixtureSpec, generate_fixture
from zap.ingest.contracts import Contract, load_contracts


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Isolate every test from the developer's real `.env` and environment."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "DATA_DIR",
        "LAKE_DIR",
        "WAREHOUSE_DIR",
        "ARTIFACTS_DIR",
        "TARGET",
        "SAMPLE_PCT",
        "CONTRACTS_DIR",
        "S3_BUCKET",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def fixture_data(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, FixtureResult]:
    """A small synthetic dataset shared by the whole test session."""
    out = tmp_path_factory.mktemp("fixture")
    return out, generate_fixture(out, FixtureSpec(n_orders=2_000))


@pytest.fixture(scope="session")
def contracts() -> dict[str, Contract]:
    return load_contracts(CONTRACTS_DIR)


@pytest.fixture
def source_copy(fixture_data: tuple[Path, FixtureResult], tmp_path: Path) -> Path:
    """A private, editable copy of the fixture CSVs for tests that corrupt files."""
    dest = tmp_path / "source"
    shutil.copytree(fixture_data[0], dest)
    return dest
