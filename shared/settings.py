"""Application settings (env-backed)."""

from __future__ import annotations

import json

from pydantic import Field, field_validator
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
    courtlistener_company_watchlist: str = Field(
        default="Voya, Voya India",
        alias="COURTLISTENER_COMPANY_WATCHLIST",
        description=(
            "Comma-separated company names to watch. Each expands into a scoped "
            "insider query and a catch-all name query, appended to the RECAP and "
            "opinion query lists. Empty disables. Note: CourtListener indexes US "
            "courts only, so a non-US entity is matched by US filings that name it."
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
    docket_follow_max_per_run: int = Field(
        default=0,
        alias="DOCKET_FOLLOW_MAX_PER_RUN",
        description=(
            "Docket-status polls per run for verdict-true filings lacking an "
            "outcome (0 disables; set only in .env.spark — the GCP rollback "
            "job runs the same entrypoint and must not double-write)"
        ),
        ge=0,
        le=500,
    )
    docket_follow_repoll_days: float = Field(
        default=7.0,
        alias="DOCKET_FOLLOW_REPOLL_DAYS",
        description="Minimum days between status polls of the same docket",
        ge=0.0,
        le=90.0,
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
    courtlistener_history_queries_per_window: int = Field(
        default=4,
        alias="COURTLISTENER_HISTORY_QUERIES_PER_WINDOW",
        ge=0,
        le=100,
        description=(
            "Rotation queries the historical sweep fires per run (0 = all). The "
            "sweep now cycles the FULL forward query set, not a hand-picked "
            "subset, and fully covers each date window (all rotation slices) "
            "before advancing the cursor — so social-engineering / sim-swap / "
            "device-identifier cases get swept historically too. Keep this small "
            "under the anonymous 10/min throttle; raise it once "
            "COURTLISTENER_API_TOKEN lifts the limit (0 = all queries per window)."
        ),
    )
    courtlistener_lookback_days: int = Field(
        default=3,
        alias="COURTLISTENER_LOOKBACK_DAYS",
        description="Overlap window subtracted from the ingest watermark (filed_after)",
        ge=0,
        le=30,
    )

    # Indian High Court judgments — the CC BY 4.0 eCourts open dataset on the
    # AWS Open Data Registry (vanga/indian-high-court-judgments; updated
    # daily, AWS-sponsored egress). $0 lane: parquet metadata → per-PDF fetch
    # → local text extraction → local insider-lexicon scan; only matches enter
    # the corpus, with their full text attached. Disabled by default — the
    # heavy work belongs on the Spark tenant, not the GCP rollback job.
    indiacourts_enabled: bool = Field(
        default=False,
        alias="INDIACOURTS_ENABLED",
        description="Run the Indian High Court dataset lane during refreshes",
    )
    indiacourts_base_url: str = Field(
        default="https://indian-high-court-judgments.s3.amazonaws.com",
        alias="INDIACOURTS_BASE_URL",
        description="Dataset bucket base URL (tests point this at a fake)",
    )
    indiacourts_courts: str = Field(
        default="",
        alias="INDIACOURTS_COURTS",
        description=(
            "Comma-separated court path codes (e.g. '7_26,27_1') restricting "
            "the lane's scope; empty = all 25 High Courts (operator decision "
            "2026-08-22: no exclusions)"
        ),
    )
    indiacourts_court_order: str = Field(
        default="7_26,27_1,29_3,33_10,36_29,3_22,24_17,19_16,9_13",
        alias="INDIACOURTS_COURT_ORDER",
        description=(
            "Courts processed first (hub-first walk order): Delhi, Bombay, "
            "Karnataka, Madras, Telangana, Punjab & Haryana, Gujarat, "
            "Calcutta, Allahabad; remaining courts follow alphabetically. "
            "Path form of the dataset's court codes ('7_26' for '7~26')."
        ),
    )
    indiacourts_history_floor: str = Field(
        default="2000-01-01",
        alias="INDIACOURTS_HISTORY_FLOOR",
        description=(
            "Sweep backward until this decision year (empty disables the "
            "sweep). 2000 = the IT Act era; dataset volume starts ~2004, so "
            "this is effectively full coverage."
        ),
    )
    indiacourts_max_pdfs_per_run: int = Field(
        default=200,
        alias="INDIACOURTS_MAX_PDFS_PER_RUN",
        description="Forward-ingest PDF fetch+scan attempts per run (0 disables)",
        ge=0,
        le=5000,
    )
    indiacourts_history_max_pdfs_per_run: int = Field(
        default=300,
        alias="INDIACOURTS_HISTORY_MAX_PDFS_PER_RUN",
        description="History-sweep PDF fetch+scan attempts per run (0 disables)",
        ge=0,
        le=5000,
    )
    indiacourts_extract_max_per_run: int = Field(
        default=50,
        alias="INDIACOURTS_EXTRACT_MAX_PER_RUN",
        description="Pending-queue (failed/OCR) retry attempts per run (0 disables)",
        ge=0,
        le=1000,
    )
    indiacourts_pdf_max_bytes: int = Field(
        default=15_000_000,
        alias="INDIACOURTS_PDF_MAX_BYTES",
        description="Skip judgment PDFs larger than this (routed to pending)",
        ge=100_000,
        le=100_000_000,
    )
    indiacourts_text_max_chars: int = Field(
        default=40_000,
        alias="INDIACOURTS_TEXT_MAX_CHARS",
        description=(
            "Stored judgment text cap — long judgments keep head AND tail "
            "(facts open, disposition closes) with an omission marker"
        ),
        ge=500,
        le=200_000,
    )
    indiacourts_min_text_chars: int = Field(
        default=200,
        alias="INDIACOURTS_MIN_TEXT_CHARS",
        description="Text-layer yield below this = scanned PDF → OCR/pending route",
        ge=0,
        le=5_000,
    )
    indiacourts_retry_days: float = Field(
        default=7.0,
        alias="INDIACOURTS_RETRY_DAYS",
        description="Minimum days between retry attempts of a pending PDF",
        ge=0.0,
        le=90.0,
    )
    indiacourts_request_delay_seconds: float = Field(
        default=0.0,
        alias="INDIACOURTS_REQUEST_DELAY_SECONDS",
        description="Pacing between dataset requests (S3 needs none; knob kept for courtesy)",
        ge=0.0,
        le=10.0,
    )
    indiacourts_ocr_command: str | None = Field(
        default=None,
        alias="INDIACOURTS_OCR_COMMAND",
        description=(
            "Command template for scanned PDFs, run as '<command> <pdf-path>' "
            "with extracted text expected on stdout (e.g. an olmOCR/ocrmypdf "
            "wrapper on the Spark tenant). Unset = scanned PDFs wait in the "
            "pending queue."
        ),
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
    admin_api_token: str | None = Field(
        default=None,
        alias="ADMIN_API_TOKEN",
        description=(
            "Bearer token required on the API's write/ops endpoints (/reload, "
            "subscription writes, ingest_url) when set. Unset = open, for local "
            "dev; prod sets it via Secret Manager on both the service (to "
            "verify) and the corpus-refresh job (to call /reload)."
        ),
    )

    # POST /extract/ttps assembles stored forensics in code — no LLM at read
    # time — so the limiter is now only a CPU/abuse guard, not a spend cap.
    # <=0 disables the limit.
    extract_rate_per_ip_hour: int = Field(
        default=30,
        alias="EXTRACT_RATE_PER_IP_HOUR",
        le=10_000,
    )
    extract_rate_global_day: int = Field(
        default=2000,
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

    # Scheduled Reddit/X social pulls. Parked 2026-08-16 (operator decision):
    # no OAuth app and no plans for one — archive dumps may return later — and
    # without credentials every refresh cycle just logged Reddit 403/429
    # tracebacks. Flip to true (SOCIAL_INGEST_ENABLED=1) to resume; the manual
    # ingest_social / ingest_social_url commands and the subscriptions API
    # keep working regardless of this switch.
    social_ingest_enabled: bool = Field(
        default=False,
        alias="SOCIAL_INGEST_ENABLED",
        description="Master switch for the scheduled Reddit/X ingest lanes",
    )
    # Social — Reddit. Public JSON works from residential IPs; cloud IPs get
    # 429'd, so set a free "script" app's credentials for OAuth app auth.
    reddit_client_id: str | None = Field(default=None, alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str | None = Field(
        default=None,
        alias="REDDIT_CLIENT_SECRET",
    )
    # --- Publications (long-form reference docs: landing page + PDF) ---
    publications_content_max_chars: int = Field(
        default=150_000,
        alias="PUBLICATIONS_CONTENT_MAX_CHARS",
        ge=500,
        le=500_000,
    )
    publications_pdf_max_bytes: int = Field(
        default=25_000_000,
        alias="PUBLICATIONS_PDF_MAX_BYTES",
        ge=100_000,
        le=100_000_000,
    )
    # Publisher sites (e.g. sei.cmu.edu) 403 non-browser agents from cloud IPs.
    publications_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        alias="PUBLICATIONS_USER_AGENT",
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
    technique_seeds_path: str = Field(
        default="data/state/technique_seeds.json",
        alias="TECHNIQUE_SEEDS_PATH",
        description="Novel-candidate view (job-written under state/, API reads it)",
    )
    lane_health_path: str = Field(
        default="data/state/lane_health.json",
        alias="LANE_HEALTH_PATH",
        description="Per-lane ingest health (job-written under state/, API reads it)",
    )

    # Use-case / insider-type classifier LLM refiner (heuristics always run)
    classifier_llm_provider: str = Field(
        default="none",
        alias="CLASSIFIER_LLM_PROVIDER",
        description="none | anthropic | openai | gemini | xai (openai = any compatible endpoint)",
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
    # A self-hosted model generating the full 12k-token enrichment JSON can run
    # past the 90s default; raise this on such a box instead of letting every
    # long generation fall through the chain to a funded provider. Applies to
    # all openai-compat chain entries (openai/gemini/xai/custom) in the
    # summarizer, discoverer, and hunt-synthesis builders.
    openai_compat_guided_json: bool = Field(
        default=True,
        alias="OPENAI_COMPAT_GUIDED_JSON",
        description=(
            "Grammar-enforce the enrichment reply via response_format "
            "json_schema (vLLM structured output). Off = plain JSON mode "
            "(pre-v3 behavior) for servers whose grammar backend misbehaves."
        ),
    )
    openai_compat_timeout_seconds: float = Field(
        default=90.0,
        ge=1.0,
        alias="OPENAI_COMPAT_TIMEOUT_SECONDS",
        description="HTTP timeout (seconds) for OpenAI-compatible chat calls",
    )
    # Qwen3-family reasoning models default to a heavy thinking pass whose
    # tokens bill against max_tokens on the same clock as the JSON — on the
    # Spark that inflated enrichments toward the 12k cap (~10.8k observed) and
    # starved the 3k-cap synthesizer to zero output. False sends vLLM
    # chat_template_kwargs.enable_thinking=false; the default True sends
    # NOTHING, keeping payloads to non-vLLM servers byte-identical.
    openai_compat_enable_thinking: bool = Field(
        default=True,
        alias="OPENAI_COMPAT_ENABLE_THINKING",
        description="Set false to disable reasoning/thinking on OpenAI-compatible servers (vLLM)",
    )
    # Real OpenAI: setting OPENAI_API_KEY (and no OPENAI_COMPAT_* overrides)
    # retargets the openai provider from local Ollama to api.openai.com.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    # Google Gemini (AI Studio key) — served through Gemini's OpenAI-compatible
    # endpoint, so it shares the openai-compat client code.
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    # xAI Grok — served through xAI's OpenAI-compatible API, so it shares the
    # openai-compat client. Set XAI_MODEL to the exact Grok model you want.
    xai_api_key: str | None = Field(default=None, alias="XAI_API_KEY")
    xai_model: str = Field(default="grok-4", alias="XAI_MODEL")
    # Extra OpenAI-compatible providers usable by name in any *_LLM_PROVIDER chain
    # (e.g. a third-party model like SOL, or a local vLLM named "sparky"). JSON
    # map, one entry per provider:
    #   {"sol": {"base_url": "https://…/v1", "model": "sol-5.6", "api_key_env": "SOL_API_KEY"}}
    # ``model`` may be "auto" (or omitted): the client probes GET /v1/models.
    # forensics.model is stamped from the completion response, not this string.
    # The key is read from the named env var (kept a secret), never inlined here.
    llm_custom_providers: str = Field(
        default="",
        alias="LLM_CUSTOM_PROVIDERS",
        description="JSON map of custom OpenAI-compatible providers, keyed by chain name",
    )
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
        description=(
            "Ordered fallback chain, comma-separated — each is tried until one "
            "succeeds (e.g. 'openai,gemini,anthropic'). Names: anthropic | openai "
            "| gemini | xai | any key in LLM_CUSTOM_PROVIDERS. none/empty disables. A "
            "provider with no key is skipped, so an unfunded entry is harmless."
        ),
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
        le=5000,
        description="LLM-call budget per processing run (0 disables)",
    )
    summarizer_reenrich_missed_limit: int = Field(
        default=0,
        alias="SUMMARIZER_REENRICH_MISSED_LIMIT",
        ge=0,
        le=50_000,
        description=(
            "One-off recovery: before the backfill sweep, clear the paid-for LLM "
            "fields on up to N 'missed' filings (forensic record produced by a "
            "model other than the target) so they re-enrich on the current model. "
            "0 disables. Set for a big-batch re-enrich run, then revert. Idempotent "
            "— rows already on the target model are never touched."
        ),
    )
    summarizer_reenrich_model: str = Field(
        default="",
        alias="SUMMARIZER_REENRICH_MODEL",
        description=(
            "Target model for the missed-filings check; empty uses the resolved "
            "summarizer model (SUMMARIZER_MODEL or ANTHROPIC_MODEL)."
        ),
    )
    summarizer_backfill_reserve: int = Field(
        default=6,
        alias="SUMMARIZER_BACKFILL_RESERVE",
        ge=0,
        le=500,
        description=(
            "Slice of the per-run budget the fresh-ingest batch may NOT spend, "
            "guaranteed to the corpus backfill sweep (which converts court "
            "filings first). Without it a heavy news day starves the backlog "
            "forever. Clamped to the per-run budget."
        ),
    )
    summarizer_max_input_chars: int = Field(
        default=8000,
        alias="SUMMARIZER_MAX_INPUT_CHARS",
        ge=500,
        le=200_000,
    )
    summarizer_filings_max_input_chars: int = Field(
        default=36_000,
        alias="SUMMARIZER_FILINGS_MAX_INPUT_CHARS",
        description="Bigger prompt budget for court filings (full-document extraction)",
        ge=500,
        le=200_000,
    )
    summarizer_upgrade_legacy: bool = Field(
        default=True,
        alias="SUMMARIZER_UPGRADE_LEGACY",
        description=(
            "Re-bill legacy rows that have a case_record but no forensics, once, "
            "to add the forensic record (budget-bounded). Disable to leave them "
            "until their source text changes."
        ),
    )
    summarizer_filing_min_text_chars: int = Field(
        default=1_500,
        alias="SUMMARIZER_FILING_MIN_TEXT_CHARS",
        description=(
            "Court filings are pre-filtered as insider-relevant by the ingestion "
            "query, so once their full document body is present (clean_text at or "
            "above this length, not just a docket-entry stub) they qualify for "
            "enrichment even without a lexical ITM/use-case hit — that is where an "
            "analyst summary is most valuable. Set 0 to enrich all filings."
        ),
        ge=0,
        le=200_000,
    )
    # --- Novel-technique discovery (second LLM pass over the forensic record) ---
    discoverer_llm_provider: str = Field(
        default="",
        alias="DISCOVERER_LLM_PROVIDER",
        description=(
            "Ordered fallback chain for the discovery pass (same syntax as "
            "SUMMARIZER_LLM_PROVIDER). Empty inherits the summarizer chain so ops "
            "sets one place. This is a SECOND LLM call per qualifying case on top "
            "of enrichment — it roughly doubles ingest LLM spend; bound it with "
            "DISCOVERER_MAX_ARTICLES_PER_RUN."
        ),
    )
    discoverer_model: str | None = Field(
        default=None,
        alias="DISCOVERER_MODEL",
        description="Discovery-pass model; falls back to SUMMARIZER_MODEL / provider default",
    )
    discoverer_max_articles_per_run: int = Field(
        default=15,
        alias="DISCOVERER_MAX_ARTICLES_PER_RUN",
        description="Per-run cap on discovery LLM calls (0 disables the pass entirely)",
        ge=0,
        le=500,
    )

    # Corpus-level hunt synthesis (one call per observed technique, cached by
    # input signature — near-zero steady-state spend after the first sweep)
    hunt_synth_llm_provider: str = Field(
        default="",
        alias="HUNT_SYNTH_LLM_PROVIDER",
        description=(
            "Ordered fallback chain for technique hunt synthesis (same syntax as "
            "SUMMARIZER_LLM_PROVIDER). Empty inherits the summarizer chain."
        ),
    )
    hunt_synth_model: str | None = Field(
        default=None,
        alias="HUNT_SYNTH_MODEL",
        description="Hunt-synthesis model; falls back to SUMMARIZER_MODEL / provider default",
    )
    hunt_synth_max_per_run: int = Field(
        default=10,
        alias="HUNT_SYNTH_MAX_PER_RUN",
        description="Per-run cap on hunt-synthesis LLM calls (0 disables the pass)",
        ge=0,
        le=500,
    )
    technique_hunts_path: str = Field(
        default="data/state/technique_hunts.json",
        alias="TECHNIQUE_HUNTS_PATH",
        description="Synthesized hunt patterns (job-written under state/, API reads it)",
    )

    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]

    @staticmethod
    def _provider_chain(value: str) -> list[str]:
        """Parse a comma-separated provider chain, dropping none/empty entries."""
        out: list[str] = []
        for part in (value or "").split(","):
            name = part.strip().lower()
            if name and name != "none" and name not in out:
                out.append(name)
        return out

    def summarizer_provider_chain(self) -> list[str]:
        return self._provider_chain(self.summarizer_llm_provider)

    def discoverer_provider_chain(self) -> list[str]:
        """Discovery chain; inherits the summarizer chain when unset."""
        chain = self._provider_chain(self.discoverer_llm_provider)
        return chain or self.summarizer_provider_chain()

    def synthesizer_provider_chain(self) -> list[str]:
        """Hunt-synthesis chain; inherits the summarizer chain when unset."""
        chain = self._provider_chain(self.hunt_synth_llm_provider)
        return chain or self.summarizer_provider_chain()

    @field_validator("admin_api_token", "export_api_token", mode="after")
    @classmethod
    def _strip_token(cls, v: str | None) -> str | None:
        """Secret Manager values often carry a trailing newline; a raw token in
        an Authorization header is a ValueError in urllib — sanitize once here
        so every consumer (job reload call, API verify) agrees."""
        return v.strip() if isinstance(v, str) else v

    def custom_llm_provider_map(self) -> dict[str, dict]:
        """Named custom OpenAI-compatible providers from LLM_CUSTOM_PROVIDERS JSON.

        Malformed JSON degrades to {} rather than raising — a bad config disables
        the custom providers, it doesn't sink the process.
        """
        raw = (self.llm_custom_providers or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k).strip().lower(): v for k, v in data.items() if isinstance(v, dict)}

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
