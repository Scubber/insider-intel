"""CLI entrypoint: python -m apps.aggregator [...]"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from apps.aggregator.archive_pipeline import run_archive_ingestion
from apps.aggregator.config import load_feeds_from_file
from apps.aggregator.courtlistener_pipeline import run_courtlistener_ingestion
from apps.aggregator.export import DEFAULT_EXPORT_DIR, write_export_package
from apps.aggregator.feedly_pipeline import run_feedly_ingestion
from apps.aggregator.pipeline import DEFAULT_STORE_PATH, run_ingestion
from apps.aggregator.process_pipeline import DEFAULT_PROCESSED_PATH, run_processing
from apps.aggregator.run_all import run_full_pipeline
from apps.aggregator.web_keywords import run_web_keyword_ingestion
from shared.itm.index import DEFAULT_INDEX_PATH
from shared.itm.refresh import DEFAULT_SOURCE_URL, refresh_itm_index
from shared.settings import get_settings


def _add_verbose(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.aggregator",
        description=(
            "Insider-threat OSINT: RSS, sitemap archive, Feedly, CourtListener, "
            "DataTheftNews, web-keyword ingest, process, and corporate export."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=False)

    ingest_p = sub.add_parser("ingest", help="Fetch RSS feeds and store raw articles.")
    ingest_p.add_argument("--feeds-file", type=str, default=None)
    ingest_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    ingest_p.add_argument("--include-raw", action="store_true")
    _add_verbose(ingest_p)

    archive_p = sub.add_parser(
        "ingest_archive",
        help=(
            "Keyword-filtered sitemap archive backfill (New Source checklist step 2). "
            "Default sources: HR Dive + Proskauer."
        ),
    )
    archive_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    archive_p.add_argument("--include-raw", action="store_true")
    archive_p.add_argument(
        "--source",
        action="append",
        dest="source_ids",
        default=None,
        help="Archive source id (repeatable), e.g. hrdive. Defaults to all configured.",
    )
    archive_p.add_argument(
        "--keyword",
        action="append",
        dest="keywords",
        default=None,
        help="Override keyword allowlist (repeatable). Defaults to IF038-class terms.",
    )
    archive_p.add_argument(
        "--max-urls",
        type=int,
        default=200,
        help="Max HTML pages to fetch per source (default 200).",
    )
    archive_p.add_argument(
        "--max-sitemaps",
        type=int,
        default=40,
        help="Max sitemap documents to walk per source (default 40).",
    )
    archive_p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between HTML fetches (default 1.0).",
    )
    _add_verbose(archive_p)

    feedly_p = sub.add_parser(
        "ingest_feedly",
        help="Pull Feedly boards / AI Feeds (FEEDLY_ACCESS_TOKEN + FEEDLY_STREAM_IDS).",
    )
    feedly_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    feedly_p.add_argument("--include-raw", action="store_true")
    feedly_p.add_argument(
        "--stream-id",
        action="append",
        dest="stream_ids",
        default=None,
        help="Feedly streamId (repeatable). Defaults to FEEDLY_STREAM_IDS.",
    )
    feedly_p.add_argument("--count", type=int, default=None)
    feedly_p.add_argument("--max-pages", type=int, default=None)
    _add_verbose(feedly_p)

    court_p = sub.add_parser(
        "ingest_courtlistener",
        help=(
            "Pull CourtListener RECAP dockets and/or case law opinions "
            "for curated insider-legal queries."
        ),
    )
    court_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    court_p.add_argument("--include-raw", action="store_true")
    court_p.add_argument("--page-size", type=int, default=None)
    court_p.add_argument("--max-pages", type=int, default=None)
    court_p.add_argument(
        "--query",
        action="append",
        dest="queries",
        default=None,
        help="Search query (repeatable). Defaults to COURTLISTENER_QUERIES / built-ins.",
    )
    court_p.add_argument(
        "--type",
        action="append",
        dest="types",
        default=None,
        help=(
            "Search type (repeatable): dockets | opinions | all. "
            "Defaults to COURTLISTENER_TYPES (dockets)."
        ),
    )
    court_p.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only results filed on/after this date (YYYY-MM-DD); overrides the watermark.",
    )
    court_p.add_argument(
        "--no-watermark",
        action="store_true",
        help="Ignore and do not update the persisted filed_after watermark.",
    )
    court_p.add_argument(
        "--no-opinion-text",
        action="store_const",
        const=False,
        default=None,
        dest="fetch_opinion_text",
        help="Skip fetching full opinion bodies (COURTLISTENER_FETCH_OPINION_TEXT).",
    )
    _add_verbose(court_p)

    court_text_p = sub.add_parser(
        "backfill_courtlistener_text",
        help=(
            "Pull full document text (free RECAP archive / opinion bodies) "
            "for stored court cases that only carry search metadata."
        ),
    )
    court_text_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    court_text_p.add_argument(
        "--processed-path",
        type=str,
        default=None,
        help="Processed store whose LLM fields reset for enriched cases.",
    )
    court_text_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max fetch attempts this run (COURTLISTENER_BACKFILL_MAX_DOCKETS).",
    )
    _add_verbose(court_text_p)

    history_p = sub.add_parser(
        "sweep_courtlistener_history",
        help=(
            "Ingest historical insider-crime case windows (walks backward to "
            "COURTLISTENER_HISTORY_FLOOR; one window per call by default)."
        ),
    )
    history_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    history_p.add_argument(
        "--windows",
        type=int,
        default=1,
        help="Number of consecutive windows to sweep in this invocation.",
    )
    _add_verbose(history_p)

    pacer_p = sub.add_parser(
        "purchase_pacer",
        help=(
            "Buy missing lead documents for qualifying cases via RECAP Fetch "
            "(needs PACER_USERNAME/PASSWORD + COURTLISTENER_API_TOKEN; "
            "budget-capped under the $30/quarter PACER fee waiver)."
        ),
    )
    pacer_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    pacer_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max purchases this run (PACER_PURCHASE_MAX_PER_RUN).",
    )
    pacer_p.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be bought without spending anything.",
    )
    _add_verbose(pacer_p)

    dtn_p = sub.add_parser(
        "ingest_datatheftnews",
        help=(
            "Pull DataTheftNews published posts via public Supabase API "
            "(no RSS; trade-secret / insider-theft beat)."
        ),
    )
    dtn_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    dtn_p.add_argument("--include-raw", action="store_true")
    dtn_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max published posts to fetch (default: DATATHEFTNEWS_LIMIT).",
    )
    _add_verbose(dtn_p)

    social_p = sub.add_parser(
        "ingest_social",
        help=(
            "Pull subscribed social sources (Reddit public JSON; X when "
            "X_BEARER_TOKEN is set) into the raw store, channel=social."
        ),
    )
    social_p.add_argument(
        "--platform",
        type=str,
        choices=("reddit", "x", "all"),
        default="all",
        help="Which social platform(s) to pull (default all).",
    )
    social_p.add_argument(
        "--subreddit",
        action="append",
        dest="subreddits",
        default=None,
        help="Subreddit override (repeatable). Defaults to subscriptions.",
    )
    social_p.add_argument(
        "--handle",
        action="append",
        dest="handles",
        default=None,
        help="X handle override (repeatable). Defaults to subscriptions.",
    )
    social_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    social_p.add_argument("--include-raw", action="store_true")
    social_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max posts per source (default: REDDIT_LIMIT / X_MAX_RESULTS).",
    )
    _add_verbose(social_p)

    social_url_p = sub.add_parser(
        "ingest_social_url",
        help="Flag one social post by URL (Reddit post or /s/ share link) for ingest.",
    )
    social_url_p.add_argument("url", type=str, help="Reddit post URL")
    social_url_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    _add_verbose(social_url_p)

    pubs_p = sub.add_parser(
        "ingest_publications",
        help="Sweep the curated publications catalog (guides/whitepapers + PDFs).",
    )
    pubs_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    pubs_p.add_argument("--include-raw", action="store_true")
    pubs_p.add_argument(
        "--source-ids",
        type=str,
        default=None,
        help="Comma-separated publication source ids (default: all enabled).",
    )
    _add_verbose(pubs_p)

    pub_url_p = sub.add_parser(
        "ingest_publication_url",
        help="Flag one publication by URL (landing page or direct PDF) for ingest.",
    )
    pub_url_p.add_argument("url", type=str, help="Publication landing page or PDF URL")
    pub_url_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    _add_verbose(pub_url_p)

    social_mgmt_p = sub.add_parser(
        "social",
        help="Manage social subscriptions (list | suggest | add | remove).",
    )
    social_sub = social_mgmt_p.add_subparsers(dest="social_command", required=True)
    social_sub.add_parser("list", help="List subscribed social sources.")
    social_sub.add_parser("suggest", help="Show the curated discovery catalog.")
    add_p = social_sub.add_parser("add", help="Subscribe to a subreddit / X handle.")
    add_p.add_argument("platform", choices=("reddit", "x"))
    add_p.add_argument("handle", type=str, help="Subreddit name or X handle")
    remove_p = social_sub.add_parser("remove", help="Unsubscribe a social source.")
    remove_p.add_argument("platform", choices=("reddit", "x"))
    remove_p.add_argument("handle", type=str)
    _add_verbose(social_mgmt_p)

    web_p = sub.add_parser(
        "ingest_web_keywords",
        help="Pull Google Alerts-style RSS URLs (WEB_KEYWORD_FEED_URLS).",
    )
    web_p.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    web_p.add_argument("--include-raw", action="store_true")
    web_p.add_argument(
        "--feed-url",
        action="append",
        dest="feed_urls",
        default=None,
        help="Alert RSS URL (repeatable). Defaults to WEB_KEYWORD_FEED_URLS.",
    )
    _add_verbose(web_p)

    process_p = sub.add_parser("process", help="Run LangGraph processing on raw articles.")
    process_p.add_argument("--raw-path", type=str, default=DEFAULT_STORE_PATH)
    process_p.add_argument("--processed-path", type=str, default=DEFAULT_PROCESSED_PATH)
    process_p.add_argument("--force", action="store_true")
    process_p.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Skip articles below this score (default: PROCESS_MIN_SCORE).",
    )
    _add_verbose(process_p)

    export_p = sub.add_parser(
        "export",
        help="Write one-way corporate export package (NDJSON + manifest).",
    )
    export_p.add_argument("--out", type=str, default=DEFAULT_EXPORT_DIR)
    export_p.add_argument("--processed-path", type=str, default=DEFAULT_PROCESSED_PATH)
    export_p.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Only export articles at/above this score (default: PROCESS_MIN_SCORE).",
    )
    export_p.add_argument(
        "--itm-alignment",
        type=str,
        default="insider",
        help="Export filter: insider (default) | weak | all",
    )
    export_p.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO datetime; only articles published/processed on or after this.",
    )
    _add_verbose(export_p)

    all_p = sub.add_parser(
        "all",
        help=(
            "Run RSS (+ Feedly / CourtListener / DataTheftNews / web keywords "
            "when configured) then process."
        ),
    )
    all_p.add_argument("--feeds-file", type=str, default=None)
    all_p.add_argument("--raw-path", type=str, default=DEFAULT_STORE_PATH)
    all_p.add_argument("--processed-path", type=str, default=DEFAULT_PROCESSED_PATH)
    all_p.add_argument("--include-raw", action="store_true")
    all_p.add_argument("--force", action="store_true")
    all_p.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Process min score (default: PROCESS_MIN_SCORE).",
    )
    all_p.add_argument("--skip-feedly", action="store_true")
    all_p.add_argument("--skip-courtlistener", action="store_true")
    all_p.add_argument("--skip-web-keywords", action="store_true")
    all_p.add_argument("--skip-datatheftnews", action="store_true")
    all_p.add_argument("--skip-social", action="store_true")
    all_p.add_argument("--skip-publications", action="store_true")
    _add_verbose(all_p)

    reenrich_p = sub.add_parser(
        "reenrich_missed",
        help=(
            "Clear LLM fields on filings whose forensics came from a non-target "
            "model, so the next processing sweep re-enriches them. --dry-run counts."
        ),
    )
    reenrich_p.add_argument("--processed-path", type=str, default=DEFAULT_PROCESSED_PATH)
    reenrich_p.add_argument(
        "--model",
        type=str,
        default=None,
        help="Target model; default = resolved summarizer model (SUMMARIZER/ANTHROPIC_MODEL).",
    )
    reenrich_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Clear at most this many missed filings (newest-filed first).",
    )
    reenrich_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count and list missed filings without clearing anything.",
    )
    _add_verbose(reenrich_p)

    itm_p = sub.add_parser(
        "refresh_itm",
        help="Download Insider Threat Matrix™ JSON and write slim itm_index.json.",
    )
    itm_p.add_argument("--url", type=str, default=DEFAULT_SOURCE_URL)
    itm_p.add_argument("--from-file", dest="source_path", type=str, default=None)
    itm_p.add_argument("--output", type=str, default=str(DEFAULT_INDEX_PATH))
    _add_verbose(itm_p)

    # Backward compatible: bare flags default to ingest
    parser.set_defaults(command="ingest")
    _add_verbose(parser)
    parser.add_argument("--feeds-file", type=str, default=None)
    parser.add_argument("--store-path", type=str, default=DEFAULT_STORE_PATH)
    parser.add_argument("--include-raw", action="store_true")
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _print_ingest(result) -> None:
    if not result.sources:
        print("\nIngest done. No sources ran (check config / optional env).")
        return
    for source in result.sources:
        status = "OK" if source.success else "FAIL"
        detail = (
            f"fetched={source.articles_fetched} saved={source.articles_saved}"
            if source.success
            else source.error
        )
        print(f"[{status}] {source.source_id}: {detail}")
    print(
        f"\nIngest done. Saved {result.total_articles_saved} article(s). "
        f"Sources: {result.success_count} ok, {result.failure_count} failed."
    )


def _print_process(result) -> None:
    reenrich = f" reenrich_cleared={result.reenrich_cleared}" if result.reenrich_cleared else ""
    print(
        f"Process done. {result.articles_processed}/{result.articles_read} processed; "
        f"saved={result.articles_saved} skipped={result.articles_skipped}{reenrich} "
        f"errors={len(result.errors)}"
    )
    for err in result.errors[:10]:
        print(f"  error: {err}")


def _try_reload_api() -> None:
    """Best-effort POST /reload so Sources + stream pick up new ingest."""
    import urllib.error
    import urllib.request

    settings = get_settings()
    url = f"http://{settings.search_host}:{settings.search_port}/reload"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        print(f"API reloaded ({url}): {body}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"Note: could not reload API at {url} ({exc}). "
            "If the UI is open, POST /reload or restart launch_local.py "
            "so Sources updates."
        )


def _resolve_min_score(value: float | None) -> float:
    if value is not None:
        return value
    return get_settings().process_min_score


def _cmd_ingest(args: argparse.Namespace) -> int:
    sources = load_feeds_from_file(args.feeds_file) if args.feeds_file else None
    result = run_ingestion(
        sources=sources,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_feedly(args: argparse.Namespace) -> int:
    result = run_feedly_ingestion(
        stream_ids=args.stream_ids,
        count=args.count,
        max_pages=args.max_pages,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    if not result.sources:
        print(
            "Hint: set FEEDLY_ACCESS_TOKEN and FEEDLY_STREAM_IDS in .env "
            "(board streamIds for Insider Threats x Top Stories / ITM-Hunt)."
        )
        return 0
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_courtlistener(args: argparse.Namespace) -> int:
    if args.since is not None:
        try:
            date.fromisoformat(args.since)
        except ValueError:
            print(f"error: --since must be YYYY-MM-DD, got {args.since!r}", file=sys.stderr)
            return 2
    try:
        result = run_courtlistener_ingestion(
            queries=args.queries,
            types=args.types,
            page_size=args.page_size,
            max_pages=args.max_pages,
            since=args.since,
            use_watermark=not args.no_watermark,
            fetch_opinion_text=args.fetch_opinion_text,
            store_path=args.store_path,
            include_raw=args.include_raw,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_backfill_courtlistener_text(args: argparse.Namespace) -> int:
    from apps.aggregator.courtlistener_pipeline import run_courtlistener_text_backfill

    result = run_courtlistener_text_backfill(
        store_path=args.store_path,
        processed_path=args.processed_path,
        limit=args.limit,
    )
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_sweep_courtlistener_history(args: argparse.Namespace) -> int:
    from apps.aggregator.courtlistener_pipeline import run_courtlistener_history_sweep

    exit_code = 0
    for _ in range(max(1, args.windows)):
        result = run_courtlistener_history_sweep(store_path=args.store_path)
        if not result.sources:
            print("History sweep disabled or already at the floor.")
            break
        _print_ingest(result)
        if result.failure_count and not result.success_count:
            exit_code = 1
            break
    return exit_code


def _cmd_purchase_pacer(args: argparse.Namespace) -> int:
    from apps.aggregator.pacer_purchase import run_pacer_purchases

    result, plan = run_pacer_purchases(
        store_path=args.store_path,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        if not plan.purchases:
            print("Nothing to buy: no qualifying cases missing documents (or feature disabled).")
        for p in plan.purchases:
            print(f"WOULD BUY [{p.stage}] ~${p.estimated_cents / 100:.2f}  {p.title}  {p.link}")
        return 0
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_datatheftnews(args: argparse.Namespace) -> int:
    from apps.aggregator.datatheftnews_pipeline import run_datatheftnews_ingestion

    result = run_datatheftnews_ingestion(
        store_path=args.store_path,
        include_raw=args.include_raw,
        limit=args.limit,
    )
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_social(args: argparse.Namespace) -> int:
    from apps.aggregator.reddit_pipeline import run_reddit_ingestion
    from apps.aggregator.run_all import _merge_ingestion
    from apps.aggregator.x_pipeline import run_x_ingestion

    results = []
    if args.platform in ("reddit", "all"):
        results.append(
            run_reddit_ingestion(
                subreddits=args.subreddits,
                store_path=args.store_path,
                include_raw=args.include_raw,
                limit=args.limit,
            )
        )
    if args.platform in ("x", "all"):
        results.append(
            run_x_ingestion(
                handles=args.handles,
                store_path=args.store_path,
                include_raw=args.include_raw,
                max_results=args.limit,
            )
        )
    result = _merge_ingestion(*results)
    _print_ingest(result)
    if not result.sources:
        print(
            "Hint: subscribe sources first — python -m apps.aggregator social suggest, "
            "then social add reddit overemployed (or set REDDIT_SUBREDDITS / X_HANDLES)."
        )
        return 0
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_publications(args: argparse.Namespace) -> int:
    from apps.aggregator.publications_pipeline import run_publications_ingestion

    source_ids = (
        [s for s in args.source_ids.split(",") if s.strip()] if args.source_ids else None
    )
    result = run_publications_ingestion(
        source_ids=source_ids,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_publication_url(args: argparse.Namespace) -> int:
    from apps.aggregator.publication_extract import PublicationFetchError
    from apps.aggregator.publications_pipeline import ingest_publication_url

    try:
        article = ingest_publication_url(args.url, store_path=args.store_path)
    except PublicationFetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if article is None:
        print(f"error: no publication found at {args.url}", file=sys.stderr)
        return 1
    chars = len(article.content or "")
    print(f"Ingested: {article.title} ({article.link}) -> channel={article.channel}")
    print(f"Extracted {chars} chars of text")
    print("Run: python -m apps.aggregator process   # to classify + index it")
    return 0


def _cmd_ingest_social_url(args: argparse.Namespace) -> int:
    from apps.aggregator.reddit_pipeline import ingest_reddit_post_url

    article = ingest_reddit_post_url(args.url, store_path=args.store_path)
    if article is None:
        print(f"error: no post found at {args.url}", file=sys.stderr)
        return 1
    print(f"Ingested: {article.title} ({article.link}) -> channel={article.channel}")
    print("Run: python -m apps.aggregator process   # to classify + index it")
    return 0


def _cmd_social(args: argparse.Namespace) -> int:
    from apps.aggregator.social_catalog import build_catalog
    from apps.aggregator.social_subscriptions import SocialSubscriptionStore

    store = SocialSubscriptionStore(get_settings().social_subscriptions_path)
    if args.social_command == "list":
        subscriptions = store.list()
        if not subscriptions:
            print("No social subscriptions. Try: python -m apps.aggregator social suggest")
            return 0
        for sub in subscriptions:
            state = "on " if sub.enabled else "off"
            tags = f" [{', '.join(sub.use_cases)}]" if sub.use_cases else ""
            print(f"[{state}] {sub.platform}: {sub.display_name()}{tags}")
        return 0
    if args.social_command == "suggest":
        subscribed = {(s.platform, s.id) for s in store.list() if s.enabled}
        for info in build_catalog():
            mark = "*" if (info.platform, info.id) in subscribed else " "
            tags = f" [{', '.join(info.use_cases)}]" if info.use_cases else ""
            print(f"[{mark}] {info.platform}: {info.name}{tags} — {info.description}")
        print("\n(*) already subscribed. Add with: social add <platform> <handle>")
        return 0
    if args.social_command == "add":
        entry = store.add(args.platform, args.handle)
        print(f"Subscribed: {entry.platform} {entry.display_name()}")
        return 0
    if args.social_command == "remove":
        removed = store.remove(args.platform, args.handle)
        print("Removed." if removed else "Not found.")
        return 0 if removed else 1
    return 2


def _cmd_ingest_web_keywords(args: argparse.Namespace) -> int:
    result = run_web_keyword_ingestion(
        feed_urls=args.feed_urls,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    if not result.sources:
        print("Hint: set WEB_KEYWORD_FEED_URLS (Google Alerts RSS URLs) in .env.")
        return 0
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_ingest_archive(args: argparse.Namespace) -> int:
    result = run_archive_ingestion(
        source_ids=args.source_ids,
        keywords=args.keywords,
        max_urls=args.max_urls,
        max_sitemaps=args.max_sitemaps,
        delay_seconds=args.delay,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    if not result.sources:
        print("Hint: configure sources in apps/aggregator/archive_sources.py.")
        return 0
    return 1 if result.failure_count and not result.success_count else 0


def _cmd_process(args: argparse.Namespace) -> int:
    result = run_processing(
        raw_path=args.raw_path,
        processed_path=args.processed_path,
        force=args.force,
        min_score=_resolve_min_score(args.min_score),
    )
    _print_process(result)
    _try_reload_api()
    return 1 if result.errors and result.articles_saved == 0 else 0


def _cmd_reenrich_missed(args: argparse.Namespace) -> int:
    from apps.aggregator.reenrich import clear_missed_filings, select_missed_filings
    from shared.settings import get_settings

    settings = get_settings()
    target = args.model or settings.summarizer_model or settings.anthropic_model
    if args.dry_run:
        links = select_missed_filings(
            args.processed_path, target_model=target, limit=args.limit
        )
        print(f"Missed filings not on {target!r}: {len(links)}")
        for link in links[:20]:
            print(f"  {link}")
        if len(links) > 20:
            print(f"  … and {len(links) - 20} more")
        return 0
    cleared = clear_missed_filings(args.processed_path, target_model=target, limit=args.limit)
    print(f"Cleared {cleared} missed filing(s) not on {target!r} — next sweep re-enriches them.")
    _try_reload_api()
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
    manifest = write_export_package(
        out_dir=args.out,
        processed_path=args.processed_path,
        min_score=_resolve_min_score(args.min_score),
        since=since,
        itm_alignment=args.itm_alignment,
    )
    print(
        f"Export written -> {args.out} "
        f"({manifest['article_count']} article(s), "
        f"schema={manifest['schema_version']})"
    )
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    result = run_full_pipeline(
        feeds_file=args.feeds_file,
        raw_path=args.raw_path,
        processed_path=args.processed_path,
        include_raw=args.include_raw,
        force_process=args.force,
        min_score=args.min_score,
        skip_feedly=args.skip_feedly,
        skip_courtlistener=args.skip_courtlistener,
        skip_web_keywords=args.skip_web_keywords,
        skip_datatheftnews=args.skip_datatheftnews,
        skip_social=args.skip_social,
        skip_publications=args.skip_publications,
    )
    _print_ingest(result.ingestion)
    _print_process(result.processing)
    print(f"\nArtifacts: raw={result.raw_path} processed={result.processed_path}")
    print('Search with: python -m apps.search query "exfiltration"')
    print("Corporate export: python -m apps.aggregator export")
    _try_reload_api()
    ingest_fail = result.ingestion.failure_count and not result.ingestion.success_count
    process_fail = result.processing.errors and result.processing.articles_saved == 0
    return 1 if ingest_fail or process_fail else 0


def _cmd_refresh_itm(args: argparse.Namespace) -> int:
    path = refresh_itm_index(
        source_url=args.url,
        output_path=args.output,
        source_path=args.source_path,
    )
    print(f"ITM index refreshed -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))

    if args.command == "process":
        return _cmd_process(args)
    if args.command == "all":
        return _cmd_all(args)
    if args.command == "refresh_itm":
        return _cmd_refresh_itm(args)
    if args.command == "ingest_feedly":
        return _cmd_ingest_feedly(args)
    if args.command == "ingest_courtlistener":
        return _cmd_ingest_courtlistener(args)
    if args.command == "backfill_courtlistener_text":
        return _cmd_backfill_courtlistener_text(args)
    if args.command == "purchase_pacer":
        return _cmd_purchase_pacer(args)
    if args.command == "sweep_courtlistener_history":
        return _cmd_sweep_courtlistener_history(args)
    if args.command == "ingest_datatheftnews":
        return _cmd_ingest_datatheftnews(args)
    if args.command == "ingest_social":
        return _cmd_ingest_social(args)
    if args.command == "ingest_social_url":
        return _cmd_ingest_social_url(args)
    if args.command == "ingest_publications":
        return _cmd_ingest_publications(args)
    if args.command == "ingest_publication_url":
        return _cmd_ingest_publication_url(args)
    if args.command == "social":
        return _cmd_social(args)
    if args.command == "ingest_web_keywords":
        return _cmd_ingest_web_keywords(args)
    if args.command == "ingest_archive":
        return _cmd_ingest_archive(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "reenrich_missed":
        return _cmd_reenrich_missed(args)
    return _cmd_ingest(args)


if __name__ == "__main__":
    sys.exit(main())
