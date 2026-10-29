"""Central configuration, read from environment variables and an optional `.env` file.

Every path and switch the pipeline uses lives here, so a run is fully described by its
environment. Secrets are typed as `SecretStr` so they never appear in logs or `zap info`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Target = Literal["duckdb", "snowflake"]

# The repo checkout (src/zap/config.py -> repo root). Contracts and the dbt project live
# here, so commands work from any working directory.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings. Field `data_dir` is read from env var `DATA_DIR`, and so on."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Where things live
    data_dir: Path = Field(default=Path("data"), description="Folder with the source CSVs")
    lake_dir: Path = Field(default=Path("lake"), description="Local stand-in for the S3 lake")
    warehouse_dir: Path = Field(default=Path("warehouse"), description="DuckDB files")
    artifacts_dir: Path = Field(default=Path("artifacts"), description="Run outputs and metrics")
    contracts_dir: Path = Field(
        default=REPO_ROOT / "contracts", description="Schema contract YAMLs"
    )
    dbt_project_dir: Path = Field(default=REPO_ROOT / "dbt" / "zomato", description="dbt project")

    # How to run
    target: Target = "duckdb"
    sample_pct: float = Field(default=5.0, gt=0, le=100)

    # Cloud and LLM (optional until the milestones that need them)
    s3_bucket: str | None = None
    aws_region: str = "ap-south-1"
    openai_api_key: SecretStr | None = None

    def duckdb_path(self, dataset: str) -> Path:
        """Warehouse file for a dataset label, e.g. `warehouse/sample_5pct.duckdb`."""
        return self.warehouse_dir / f"{dataset}.duckdb"

    def redacted(self) -> dict[str, str]:
        """Settings as display strings, with secrets masked."""
        out: dict[str, str] = {}
        for name, value in self.model_dump().items():
            if isinstance(value, SecretStr):
                out[name] = "***set***" if value.get_secret_value() else "(empty)"
            elif value is None:
                out[name] = "(not set)"
            else:
                out[name] = str(value)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings instance (cached; call `get_settings.cache_clear()` in tests)."""
    return Settings()
