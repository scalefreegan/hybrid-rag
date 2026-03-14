"""Configuration loading for pointy-rag."""

import os

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    voyage_api_key: str = ""
    database_url: str = ""


def get_settings() -> Settings:
    return Settings(
        voyage_api_key=os.getenv("VOYAGE_API_KEY", ""),
        database_url=os.getenv("POINTY_DATABASE_URL", ""),
    )
