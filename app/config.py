from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BOT_TOKEN: str = ""
    WEBHOOK_SECRET: str = ""
    PUBLIC_URL: str = ""
    ADMIN_IDS: str = ""
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"
    INITDATA_MAX_AGE: int = 86400
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000

    @field_validator("DATABASE_URL")
    @classmethod
    def fix_db_url(cls, v: str) -> str:
        # Railway: postgresql:// yoki postgres:// -> asyncpg drayveri
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    @property
    def sync_database_url(self) -> str:
        """Alembic uchun sinxron URL."""
        url = self.DATABASE_URL
        return (
            url.replace("+asyncpg", "+psycopg2")
            .replace("+aiosqlite", "")
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Yagona format modeli: jamoa hajmi faqat shu yerda belgilanadi
FORMATS: dict[str, int] = {
    "1v1": 1,
    "2v2": 2,
    "3v3": 3,
    "5v5": 5,
}


def team_size(fmt: str) -> int:
    if fmt not in FORMATS:
        raise ValueError(f"Noma'lum format: {fmt}")
    return FORMATS[fmt]
