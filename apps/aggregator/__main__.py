"""CLI entrypoint: python -m apps.aggregator [...]"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

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
            "web-keyword ingest, process, and corporate export."
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
        help="Pull CourtListener RECAP dockets for curated insider-legal queries.",
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
        help="RECAP query (repeatable). Defaults to COURTLISTENER_QUERIES / built-ins.",
    )
    _add_verbose(court_p)

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
        help="Run RSS (+ Feedly / CourtListener / web keywords when configured) then process.",
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
    _add_verbose(all_p)

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
    print(
        f"Process done. {result.articles_processed}/{result.articles_read} processed; "
        f"saved={result.articles_saved} skipped={result.articles_skipped} "
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
    result = run_courtlistener_ingestion(
        queries=args.queries,
        page_size=args.page_size,
        max_pages=args.max_pages,
        store_path=args.store_path,
        include_raw=args.include_raw,
    )
    _print_ingest(result)
    return 1 if result.failure_count and not result.success_count else 0


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
    if args.command == "ingest_web_keywords":
        return _cmd_ingest_web_keywords(args)
    if args.command == "ingest_archive":
        return _cmd_ingest_archive(args)
    if args.command == "export":
        return _cmd_export(args)
    return _cmd_ingest(args)


if __name__ == "__main__":
    sys.exit(main())
