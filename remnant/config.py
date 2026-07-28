from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    remnant_db_url: Optional[str] = None
    remnant_qdrant_url: Optional[str] = None
    remnant_qdrant_api_key: Optional[str] = None
    remnant_neo4j_url: Optional[str] = "bolt://localhost:7687"
    remnant_neo4j_username: Optional[str] = "neo4j"
    remnant_neo4j_password: Optional[str] = "password"
    voyage_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    remnant_project_root: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
