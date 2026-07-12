"""Application settings (env-backed)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    raw_articles_path: str = Field(default="data/raw/articles.jsonl", alias="RAW_ARTICLES_PATH")
    processed_articles_path: str = Field(
        default="data/processed/articles.jsonl",
        alias="PROCESSED_ARTICLES_PATH",
    )
    search_host: str = Field(default="127.0.0.1", alias="SEARCH_HOST")
    search_port: int = Field(default=8000, alias="SEARCH_PORT")
    # Comma-separated browser origins allowed to call the API (local + future Pages)
    cors_origins: str = Field(
        default="http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:3000,http://localhost:3000,null",
        alias="CORS_ORIGINS",
    )
    # Reserved for Postgres/pgvector cutover
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # Feedly Cloud API (optional) — pull boards / AI Feeds like
    # "Insider Threats x Top Stories" or "ITM-Hunt"
    feedly_access_token: str | None = Field(default=None, alias="FEEDLY_ACCESS_TOKEN")
    feedly_stream_ids: str = Field(
        default="",
        alias="FEEDLY_STREAM_IDS",
        description="Comma-separated Feedly streamIds (boards / folders / AI feeds)",
    )
    feedly_count: int = Field(default=50, alias="FEEDLY_COUNT", ge=1, le=100)
    feedly_max_pages: int = Field(default=2, alias="FEEDLY_MAX_PAGES", ge=1, le=10)

    # CourtListener RECAP search (optional token; anonymous works with lower limits)
    courtlistener_api_token: str | None = Field(
        default=None,
        alias="COURTLISTENER_API_TOKEN",
    )
    courtlistener_queries: str = Field(
        default="",
        alias="COURTLISTENER_QUERIES",
        description="Comma-separated RECAP search queries (empty = built-in defaults)",
    )
    courtlistener_page_size: int = Field(
        default=20,
        alias="COURTLISTENER_PAGE_SIZE",
        ge=1,
        le=100,
    )
    courtlistener_max_pages: int = Field(
        default=1,
        alias="COURTLISTENER_MAX_PAGES",
        ge=1,
        le=5,
    )

    # Drop weak articles on process; UI should match this default
    process_min_score: float = Field(
        default=0.15,
        alias="PROCESS_MIN_SCORE",
        ge=0.0,
        le=1.0,
    )

    # One-way corporate pull — bearer token for GET /export/articles
    export_api_token: str | None = Field(default=None, alias="EXPORT_API_TOKEN")

    # Alert RSS URLs for web keyword discovery (comma-separated feed URLs)
    web_keyword_feed_urls: str = Field(
        default="",
        alias="WEB_KEYWORD_FEED_URLS",
        description="Comma-separated Google Alerts (or similar) RSS feed URLs",
    )

    # xAI / Grok — optional LLM fill for POST /extract/ttps
    xai_api_key: str | None = Field(default=None, alias="XAI_API_KEY")
    xai_model: str = Field(default="grok-3-mini", alias="XAI_MODEL")

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]

    def feedly_stream_id_list(self) -> list[str]:
        return [part.strip() for part in self.feedly_stream_ids.split(",") if part.strip()]

    def web_keyword_feed_url_list(self) -> list[str]:
        return [
            part.strip()
            for part in self.web_keyword_feed_urls.split(",")
            if part.strip()
        ]


def get_settings() -> Settings:
    return Settings()
