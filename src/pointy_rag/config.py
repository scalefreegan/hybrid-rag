"""Configuration loading for pointy-rag."""

import os
from functools import lru_cache

from pydantic import BaseModel, Field

DEFAULT_DATABASE_URL = "postgresql://localhost:5432/pointy_rag"


class Settings(BaseModel):
    voyage_api_key: str = ""
    database_url: str = DEFAULT_DATABASE_URL
    kg_enabled: bool = True
    kg_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    kg_max_similar_neighbors: int = Field(default=20, ge=1)
    kg_hierarchy_levels_up: int = Field(default=1, ge=1)
    kg_similar_hops: int = Field(default=1, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    from dotenv import load_dotenv

    load_dotenv()
    return Settings(
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        database_url=os.getenv("POINTY_DATABASE_URL", DEFAULT_DATABASE_URL),
        kg_enabled=os.getenv("POINTY_KG_ENABLED", "true").lower()
        not in ("false", "0", "no"),
        kg_similarity_threshold=float(
            os.getenv("POINTY_KG_SIMILARITY_THRESHOLD", "0.85")
        ),
        kg_max_similar_neighbors=int(os.getenv("POINTY_KG_MAX_NEIGHBORS", "20")),
        kg_hierarchy_levels_up=int(os.getenv("POINTY_KG_HIERARCHY_LEVELS_UP", "1")),
        kg_similar_hops=int(os.getenv("POINTY_KG_SIMILAR_HOPS", "1")),
    )
