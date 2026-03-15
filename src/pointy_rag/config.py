"""Configuration loading for pointy-rag."""

import os
from functools import lru_cache

from pydantic import BaseModel

DEFAULT_DATABASE_URL = "postgresql://localhost:5432/pointy_rag"


class Settings(BaseModel):
    voyage_api_key: str = ""
    database_url: str = DEFAULT_DATABASE_URL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv()
    return Settings(
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        database_url=os.getenv("POINTY_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
