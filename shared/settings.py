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
    courtlistener_types: str = Field(
        default="dockets",
        alias="COURTLISTENER_TYPES",
        description="Comma-separated search types: dockets,opinions (or 'all')",
    )
    courtlistener_opinion_queries: str = Field(
        default="",
        alias="COURTLISTENER_OPINION_QUERIES",
        description=(
            "Comma-separated opinion search queries "
            "(empty = COURTLISTENER_QUERIES / built-in defaults)"
        ),
    )
    courtlistener_fetch_opinion_text: bool = Field(
        default=True,
        alias="COURTLISTENER_FETCH_OPINION_TEXT",
        description="Fetch full opinion bodies for ITM scoring (1 extra GET per new opinion)",
    )
    courtlistener_opinion_text_max_chars: int = Field(
        default=20_000,
        alias="COURTLISTENER_OPINION_TEXT_MAX_CHARS",
        ge=500,
        le=200_000,
    )
    courtlistener_recap_text_max_chars: int = Field(
        default=40_000,
        alias="COURTLISTENER_RECAP_TEXT_MAX_CHARS",
        description="Cap on concatenated RECAP filing text per docket (backfill)",
        ge=500,
        le=200_000,
    )
    courtlistener_request_delay_seconds: float = Field(
        default=7.0,
        alias="COURTLISTENER_REQUEST_DELAY_SECONDS",
        description=(
            "Gap between backfill/purchase requests. CourtListener throttles "
            "these endpoints at 10/min; 7s ≈ 8.5/min with margin."
        ),
        ge=0.0,
        le=60.0,
    )
    courtlistener_backfill_max_dockets: int = Field(
        default=25,
        alias="COURTLISTENER_BACKFILL_MAX_DOCKETS",
        description="Full-text backfill attempts per run (0 disables)",
        ge=0,
        le=200,
    )
    # PACER purchasing via CourtListener's RECAP Fetch API (strictly gated:
    # both credentials AND a positive budget/cap required; no creds = no-op).
    # Default budget stays under PACER's $30/quarter fee waiver.
    pacer_username: str | None = Field(default=None, alias="PACER_USERNAME")
    pacer_password: str | None = Field(default=None, alias="PACER_PASSWORD")
    pacer_purchase_max_per_run: int = Field(
        default=5,
        alias="PACER_PURCHASE_MAX_PER_RUN",
        description="Max RECAP Fetch purchases per refresh run (0 disables)",
        ge=0,
        le=50,
    )
    pacer_quarterly_budget_cents: int = Field(
        default=2700,
        alias="PACER_QUARTERLY_BUDGET_CENTS",
        description="Estimated-spend ceiling per quarter ($27 < $30 waiver; 0 disables)",
        ge=0,
        le=100_000,
    )
    # Rolling historical sweep: each refresh also ingests one time window of
    # past insider-crime cases, walking backward until the floor is reached.
    courtlistener_history_floor: str = Field(
        default="2015-01-01",
        alias="COURTLISTENER_HISTORY_FLOOR",
        description="Sweep backward until this filing date (empty disables)",
    )
    courtlistener_history_window_days: int = Field(
        default=90,
        alias="COURTLISTENER_HISTORY_WINDOW_DAYS",
        ge=7,
        le=365,
    )
    courtlistener_history_max_pages: int = Field(
        default=1,
        alias="COURTLISTENER_HISTORY_MAX_PAGES",
        ge=1,
        le=5,
    )
    courtlistener_lookback_days: int = Field(
        default=3,
        alias="COURTLISTENER_LOOKBACK_DAYS",
        description="Overlap window subtracted from the ingest watermark (filed_after)",
        ge=0,
        le=30,
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

    # POST /extract/ttps can spend LLM credits — cap it. <=0 disables the limit.
    extract_rate_per_ip_hour: int = Field(
        default=20,
        alias="EXTRACT_RATE_PER_IP_HOUR",
        le=10_000,
    )
    extract_rate_global_day: int = Field(
        default=200,
        alias="EXTRACT_RATE_GLOBAL_DAY",
        le=100_000,
    )

    # Alert RSS URLs for web keyword discovery (comma-separated feed URLs)
    web_keyword_feed_urls: str = Field(
        default="",
        alias="WEB_KEYWORD_FEED_URLS",
        description="Comma-separated Google Alerts (or similar) RSS feed URLs",
    )

    # DataTheftNews (no public RSS — Supabase blog_posts; anon key is public in their SPA)
    datatheftnews_supabase_url: str = Field(
        default="https://efjoefkaplfsgqwrbseg.supabase.co",
        alias="DATATHEFTNEWS_SUPABASE_URL",
    )
    datatheftnews_anon_key: str | None = Field(
        default=None,
        alias="DATATHEFTNEWS_ANON_KEY",
        description="Optional; when empty, discover from the public SPA bundle",
    )
    datatheftnews_limit: int = Field(
        default=200,
        alias="DATATHEFTNEWS_LIMIT",
        ge=1,
        le=1000,
    )
    datatheftnews_content_max_chars: int = Field(
        default=50_000,
        alias="DATATHEFTNEWS_CONTENT_MAX_CHARS",
        ge=500,
        le=200_000,
    )

    # xAI / Grok — optional LLM fill for POST /extract/ttps
    xai_api_key: str | None = Field(default=None, alias="XAI_API_KEY")
    xai_model: str = Field(default="grok-3-mini", alias="XAI_MODEL")
    # Which LLM enriches the extract report. "auto" picks the first configured
    # key (xAI, then Anthropic); "openai" means any OpenAI-compatible endpoint.
    extract_llm_provider: str = Field(
        default="auto",
        alias="EXTRACT_LLM_PROVIDER",
        description="auto | xai | anthropic | openai | none",
    )

    # Social — Reddit. Public JSON works from residential IPs; cloud IPs get
    # 429'd, so set a free "script" app's credentials for OAuth app auth.
    reddit_client_id: str | None = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str | None = Field(
        default=None,
        alias="REDDIT_CLIENT_SECRET",
    )
    reddit_subreddits: str = Field(
        default="",
        alias="REDDIT_SUBREDDITS",
        description="Comma-separated fallback subreddits when no subscription file",
    )
    reddit_limit: int = Field(default=50, alias="REDDIT_LIMIT", ge=1, le=100)
    reddit_user_agent: str = Field(
        default="insider-intel/0.1 (insider-threat research aggregator)",
        alias="REDDIT_USER_AGENT",
    )
    reddit_delay_seconds: float = Field(
        default=2.0,
        alias="REDDIT_DELAY_SECONDS",
        ge=0.0,
        le=30.0,
    )
    reddit_content_max_chars: int = Field(
        default=20_000,
        alias="REDDIT_CONTENT_MAX_CHARS",
        ge=500,
        le=200_000,
    )

    # Social — X/Twitter API v2. Provide X_BEARER_TOKEN directly, or the
    # app's consumer key/secret and the pipeline mints an app-only bearer.
    # Defaults are sized for the free tier (~100 post-reads/month): 5 posts
    # per handle at most every 48h. Paid tiers raise both via env.
    x_bearer_token: str | None = Field(default=None, alias="X_BEARER_TOKEN")
    x_consumer_key: str | None = Field(default=None, alias="X_CONSUMER_KEY")
    x_consumer_secret: str | None = Field(default=None, alias="X_CONSUMER_SECRET")
    x_handles: str = Field(
        default="",
        alias="X_HANDLES",
        description="Comma-separated fallback handles when no subscription file",
    )
    x_max_results: int = Field(default=5, alias="X_MAX_RESULTS", ge=5, le=100)
    x_ingest_every_hours: int = Field(
        default=48,
        alias="X_INGEST_EVERY_HOURS",
        description="Minimum hours between X pulls (0 = every refresh run)",
        ge=0,
        le=336,
    )

    # Social subscriptions store (user-picked subreddits / X follows)
    social_subscriptions_path: str = Field(
        default="data/config/social_subscriptions.json",
        alias="SOCIAL_SUBSCRIPTIONS_PATH",
    )

    # Use-case / insider-type classifier LLM refiner (heuristics always run)
    classifier_llm_provider: str = Field(
        default="none",
        alias="CLASSIFIER_LLM_PROVIDER",
        description="none | anthropic | openai | gemini (openai = any compatible endpoint)",
    )
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-haiku-4-5", alias="ANTHROPIC_MODEL")
    openai_compat_base_url: str = Field(
        default="http://localhost:11434/v1",
        alias="OPENAI_COMPAT_BASE_URL",
        description="OpenAI-compatible endpoint (default: local Ollama)",
    )
    openai_compat_api_key: str | None = Field(default=None, alias="OPENAI_COMPAT_API_KEY")
    openai_compat_model: str = Field(default="llama3.1:8b", alias="OPENAI_COMPAT_MODEL")
    # Real OpenAI: setting OPENAI_API_KEY (and no OPENAI_COMPAT_* overrides)
    # retargets the openai provider from local Ollama to api.openai.com.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    # Google Gemini (AI Studio key) — served through Gemini's OpenAI-compatible
    # endpoint, so it shares the openai-compat client code.
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    classify_llm_channels: str = Field(
        default="social",
        alias="CLASSIFY_LLM_CHANNELS",
        description="Channels eligible for the LLM refiner (comma list or 'all')",
    )

    # Ingest summarizer LLM: writes ai_summary + case_record on qualifying
    # articles and adjudicates ITM technique candidates. Each article is
    # billed once, ever (results persist in the processed corpus).
    summarizer_llm_provider: str = Field(
        default="none",
        alias="SUMMARIZER_LLM_PROVIDER",
        description="none | anthropic | openai | gemini (openai = any compatible endpoint)",
    )
    summarizer_model: str | None = Field(
        default=None,
        alias="SUMMARIZER_MODEL",
        description="Override model; None uses the provider's classifier default",
    )
    summarizer_max_articles_per_run: int = Field(
        default=15,
        alias="SUMMARIZER_MAX_ARTICLES_PER_RUN",
        ge=0,
        le=500,
        description="LLM-call budget per processing run (0 disables)",
    )
    summarizer_max_input_chars: int = Field(
        default=6000,
        alias="SUMMARIZER_MAX_INPUT_CHARS",
        ge=500,
        le=50_000,
    )
    summarizer_filings_max_input_chars: int = Field(
        default=24_000,
        alias="SUMMARIZER_FILINGS_MAX_INPUT_CHARS",
        description="Bigger prompt budget for court filings (full-document extraction)",
        ge=500,
        le=50_000,
    )

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]

    def feedly_stream_id_list(self) -> list[str]:
        return [part.strip() for part in self.feedly_stream_ids.split(",") if part.strip()]

    def web_keyword_feed_url_list(self) -> list[str]:
        return [part.strip() for part in self.web_keyword_feed_urls.split(",") if part.strip()]

    def reddit_subreddit_list(self) -> list[str]:
        return [part.strip() for part in self.reddit_subreddits.split(",") if part.strip()]

    def x_handle_list(self) -> list[str]:
        return [
            part.strip().lstrip("@")
            for part in self.x_handles.split(",")
            if part.strip().lstrip("@")
        ]

    def classify_llm_channel_list(self) -> list[str]:
        return [
            part.strip().lower() for part in self.classify_llm_channels.split(",") if part.strip()
        ]


def get_settings() -> Settings:
    return Settings()
