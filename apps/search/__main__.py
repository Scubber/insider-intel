"""CLI: python -m apps.search [serve|query]"""

from __future__ import annotations

import argparse
import logging
import sys

from shared.schemas import SearchMode
from shared.settings import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.search",
        description="Search processed threat-intelligence articles.",
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Start the FastAPI search server.")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--reload", action="store_true")
    serve_p.add_argument("-v", "--verbose", action="store_true")

    query_p = sub.add_parser("query", help="Run a one-shot search from the CLI.")
    query_p.add_argument("query", type=str, help="Search query text")
    query_p.add_argument(
        "--mode",
        choices=[m.value for m in SearchMode],
        default=SearchMode.hybrid.value,
    )
    query_p.add_argument("--limit", type=int, default=10)
    query_p.add_argument("--min-score", type=float, default=0.0)
    query_p.add_argument("--source-id", type=str, default=None)
    query_p.add_argument("--path", type=str, default=None, help="Processed articles JSONL path")
    query_p.add_argument("--json", action="store_true", help="Print full JSON response")
    query_p.add_argument("-v", "--verbose", action="store_true")

    parser.set_defaults(command="serve")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    host = args.host or settings.search_host
    port = args.port or settings.search_port
    uvicorn.run(
        "apps.search.api:app",
        host=host,
        port=port,
        reload=args.reload,
    )
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    from apps.search import service

    result = service.search(
        args.query,
        mode=args.mode,
        limit=args.limit,
        min_score=args.min_score,
        source_id=args.source_id,
        path=args.path,
    )
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    print(f"mode={result.mode.value} indexed={result.total_indexed} hits={result.count}")
    for i, hit in enumerate(result.results, start=1):
        print(f"{i}. [{hit.score:.3f}] {hit.title}")
        print(f"   {hit.link}")
        if hit.cves:
            print(f"   CVEs: {', '.join(hit.cves)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if args.command == "query":
        return _cmd_query(args)
    return _cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())
