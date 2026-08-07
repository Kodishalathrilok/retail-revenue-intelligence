"""Typed configuration loaded from .env."""

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

    dunnhumby_raw_dir: Path = Field(
        default=Path("data/raw/dunnhumby"), alias="RRIP_DUNNHUMBY_RAW_DIR"
    )

    @property
    def raw_dir(self) -> Path:
        """Absolute path to the dunnhumby CSVs."""
        d = self.dunnhumby_raw_dir
        return d if d.is_absolute() else PROJECT_ROOT / d

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


settings = Settings()
