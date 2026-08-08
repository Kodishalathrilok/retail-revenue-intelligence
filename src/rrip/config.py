"""Typed configuration loaded from .env."""

from datetime import date
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pg_host: str = Field(default="localhost", alias="RRIP_PG_HOST")
    pg_port: int = Field(default=5432, alias="RRIP_PG_PORT")
    pg_user: str = Field(default="postgres", alias="RRIP_PG_USER")
    pg_password: str = Field(default="", alias="RRIP_PG_PASSWORD")
    pg_database: str = Field(default="rrip", alias="RRIP_PG_DATABASE")

    # Calendar anchor for dim_date. dunnhumby publishes no start date, only
    # DAY 1..711. This must be a WEDNESDAY so that day 6 falls on a Monday and
    # derived weeks align with the source WEEK_NO column that causal_data is
    # keyed on -- see docs/methodology-notes.md. The absolute year is arbitrary
    # and carries no meaning; only intervals and month/year boundaries are real.
    day1_date: date = Field(default=date(2015, 1, 7), alias="RRIP_DAY1_DATE")

    # Repo-relative fallback only. Real runs point this outside the repo via
    # .env (see .env.example) -- a machine-specific absolute path does not
    # belong in source, and CI needs a portable default for fixtures.
    dunnhumby_raw_dir: Path = Field(
        default=Path("data/raw/dunnhumby"), alias="RRIP_DUNNHUMBY_RAW_DIR"
    )

    @property
    def raw_dir(self) -> Path:
        """Absolute path to the dunnhumby CSVs.

        Relative values resolve against the repo root, so the setting behaves
        the same whatever directory the CLI is invoked from.
        """
        d = self.dunnhumby_raw_dir
        return d if d.is_absolute() else PROJECT_ROOT / d

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


settings = Settings()
