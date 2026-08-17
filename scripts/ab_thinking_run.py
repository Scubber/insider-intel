"""Run the thinking-on/off A/B over a gold-set manifest (step 2 of the gate).

For each manifest case this calls the configured enrichment provider TWICE —
once with OPENAI_COMPAT_ENABLE_THINKING behavior on, once off — through the
EXISTING machinery: two Settings copies differing only in
``openai_compat_enable_thinking`` are fed to ``shared.llm.get_summarizer_chain``,
whose ``_build_summarizer`` threads the knob into
``OpenAICompatSummarizer(enable_thinking=...)``; the provider's
``_chat_completion`` then adds ``chat_template_kwargs.enable_thinking=false``
for the off arm and sends a byte-identical pre-knob payload for the on arm
(shared/llm/openai_provider.py). Each call mirrors the production enrichment
exactly (per-channel truncation, ITM candidate shortlist, lenient parse,
verbatim stamp — shared/agents/summarize.py:enrich_fields).

Safety: the corpus is only READ (pure JSONL parse, no store object); all
output lands in --out-dir. Safe to run in a refresh-cycle gap. Resumable:
one pair line is appended per completed case; already-recorded links are
skipped on restart.

Keys are never taken on the command line: the API key comes from the normal
settings resolution (OPENAI_COMPAT_API_KEY / OPENAI_API_KEY / custom
provider ``api_key_env``). --base-url/--model exist only to point the run at
the local vLLM without editing .env.

Usage (from the repo root, e.g. on sparky in a cycle gap):
    python -m scripts.ab_thinking_run --manifest data/ab_eval/goldset_manifest.json \
        [--processed-path data/processed/articles.jsonl] \
        [--out-dir data/ab_eval/run] \
        [--base-url http://vllm:8000/v1] [--model auto] [--timeout 900] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts.ab_select_goldset import iter_processed
from shared.agents.summarize import build_itm_candidates
from shared.llm import get_summarizer_chain, reset_provider_cache
from shared.llm.openai_provider import (
    OpenAICompatSummarizer,
    discover_openai_compat_model,
    get_last_usage,
    is_auto_model,
)
from shared.schemas import ProcessedArticle
from shared.schemas.articles import resolve_channel
from shared.schemas.forensics import parse_forensics_json, stamp_quote_verbatim
from shared.settings import Settings

ARM_ON = "thinking_on"
ARM_OFF = "thinking_off"
PAIRS_FILENAME = "ab_pairs.jsonl"
META_FILENAME = "run_meta.json"

# Thinking-on decodes on the Spark run 5-9 min/article; below this the on arm
# would time out and the A/B would measure the timeout, not the model.
TIMEOUT_HINT_SECONDS = 600.0


def build_arm_settings(
    base: Settings,
    *,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[Settings, Settings]:
    """(thinking-on, thinking-off) Settings — identical except the knob.

    --base-url retargets the chain to a bare ``openai`` entry on that endpoint
    (the local vLLM); --model overrides the primary chain entry's model
    (``auto`` probes GET /v1/models via the existing discovery).
    """
    update: dict = {}
    if base_url:
        update["summarizer_llm_provider"] = "openai"
        update["openai_compat_base_url"] = base_url
    if model:
        resolved = model
        if is_auto_model(model):
            probe_base = base_url or base.openai_compat_base_url
            probe_key = base.openai_compat_api_key or base.openai_api_key
            resolved = discover_openai_compat_model(probe_base, probe_key) or ""
            if not resolved:
                raise SystemExit(f"--model auto: probe of {probe_base}/models failed")
        update["summarizer_model"] = resolved
    if timeout:
        update["openai_compat_timeout_seconds"] = float(timeout)
    settings_on = base.model_copy(update={**update, "openai_compat_enable_thinking": True})
    settings_off = base.model_copy(update={**update, "openai_compat_enable_thinking": False})
    return settings_on, settings_off


def build_arm_providers(settings_on: Settings, settings_off: Settings) -> tuple[object, object]:
    """Primary enrichment provider per arm, via the production chain builder.

    The chain cache keys on provider names + model only — NOT the thinking
    knob (shared/llm/__init__.py) — so the cache is reset around each build to
    keep the arms from sharing one provider object. Only the PRIMARY chain
    entry is used: exercising fallbacks would let a slow/failed call silently
    swap models mid-arm and invalidate the comparison.
    """
    reset_provider_cache()
    chain_on = get_summarizer_chain(settings_on)
    reset_provider_cache()
    chain_off = get_summarizer_chain(settings_off)
    reset_provider_cache()
    if not chain_on or not chain_off:
        raise SystemExit(
            "summarizer chain resolved to 0 providers — set SUMMARIZER_LLM_PROVIDER "
            "(or pass --base-url) and the provider's key via the normal env"
        )
    return chain_on[0], chain_off[0]


def _usage_for(provider: object) -> dict | None:
    """Token usage for the provider's most recent call, when observable."""
    if isinstance(provider, OpenAICompatSummarizer):
        return get_last_usage()
    usage = getattr(provider, "last_usage", None)
    return usage if isinstance(usage, dict) else None


def enrich_arm(
    provider: object,
    *,
    arm: str,
    row: ProcessedArticle,
    settings: Settings,
) -> dict:
    """One arm's enrichment of one case, mirroring enrich_fields' call shape."""
    channel = resolve_channel(row.source_id, getattr(row, "channel", None))
    cap = (
        settings.summarizer_filings_max_input_chars
        if channel == "filings"
        else settings.summarizer_max_input_chars
    )
    text = (row.clean_text or row.summary or "")[:cap]
    candidates = build_itm_candidates(text, row.entities.itm_hits)

    error: str | None = None
    raw: dict | None = None
    started = time.perf_counter()
    try:
        raw = provider.extract_case(
            title=row.title, source=row.source_id, text=text, itm_candidates=candidates
        )
    except Exception as exc:  # noqa: BLE001 — a failed arm is a data point, not a crash
        error = f"{type(exc).__name__}: {exc}"
    wall_seconds = time.perf_counter() - started
    usage = _usage_for(provider) or {}

    parse_ok = isinstance(raw, dict)
    forensics_dump: dict | None = None
    ai_summary: str | None = None
    if parse_ok:
        forensics = parse_forensics_json(raw, link=row.link, title=row.title).model_copy(
            update={
                "extracted_at": datetime.now(UTC),
                "model": getattr(provider, "model_name", None),
            }
        )
        # Grounding stamp against the text the model actually saw, exactly as
        # the production enricher stamps it (shared/agents/summarize.py).
        stamp_quote_verbatim(forensics, text)
        ai_summary = (str(raw.get("ai_summary") or "")).strip() or None
        forensics_dump = forensics.model_dump(mode="json")

    return {
        "arm": arm,
        "parse_ok": parse_ok,
        "error": error,
        "wall_seconds": round(wall_seconds, 3),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "model": getattr(provider, "model_name", None),
        "input_chars": len(text),
        "ai_summary": ai_summary,
        "forensics": forensics_dump,
    }


def load_done_links(pairs_path: Path) -> set[str]:
    """Links already recorded with BOTH arms (torn/corrupt lines don't count)."""
    done: set[str] = set()
    if not pairs_path.exists():
        return done
    with pairs_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                pair = json.loads(line)
            except ValueError:
                continue
            if isinstance(pair, dict) and pair.get(ARM_ON) and pair.get(ARM_OFF):
                done.add(str(pair.get("link") or ""))
    done.discard("")
    return done


def _append_pair(pairs_path: Path, pair: dict) -> None:
    """Checkpoint one completed case (heal a torn last line first)."""
    if pairs_path.exists() and pairs_path.stat().st_size:
        with pairs_path.open("rb") as handle:
            handle.seek(-1, 2)
            torn = handle.read(1) != b"\n"
        if torn:
            with pairs_path.open("a", encoding="utf-8") as handle:
                handle.write("\n")
    with pairs_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(pair) + "\n")


def first_arm_for(link: str) -> str:
    """Deterministic per-case arm order (alternates warm-up/cache bias)."""
    import hashlib

    parity = int(hashlib.sha256(link.encode()).hexdigest(), 16) % 2
    return ARM_ON if parity == 0 else ARM_OFF


def run_pairs(
    *,
    manifest: dict,
    corpus_by_link: dict[str, ProcessedArticle],
    provider_on: object,
    provider_off: object,
    settings: Settings,
    pairs_path: Path,
    limit: int | None = None,
    log=print,
) -> dict:
    """Drive both arms over the manifest; checkpoint after each case."""
    pairs_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done_links(pairs_path)
    cases = manifest.get("cases") or []
    ran = skipped_done = missing = 0
    for case in cases:
        if limit is not None and ran >= limit:
            break
        link = str(case.get("link") or "")
        if not link:
            continue
        if link in done:
            skipped_done += 1
            continue
        row = corpus_by_link.get(link)
        if row is None:
            missing += 1
            log(f"[skip] {link} not in corpus")
            continue
        arms = {}
        order = (
            ((ARM_ON, provider_on), (ARM_OFF, provider_off))
            if first_arm_for(link) == ARM_ON
            else ((ARM_OFF, provider_off), (ARM_ON, provider_on))
        )
        for arm, provider in order:
            arms[arm] = enrich_arm(provider, arm=arm, row=row, settings=settings)
        pair = {
            "link": link,
            "title": row.title,
            "first_arm": order[0][0],
            ARM_ON: arms[ARM_ON],
            ARM_OFF: arms[ARM_OFF],
        }
        _append_pair(pairs_path, pair)
        ran += 1
        on, off = arms[ARM_ON], arms[ARM_OFF]
        log(
            f"[{ran}] {link[:60]} on={on['wall_seconds']}s"
            f"(ok={on['parse_ok']}) off={off['wall_seconds']}s(ok={off['parse_ok']})"
        )
    return {
        "cases_total": len(cases),
        "ran": ran,
        "skipped_done": skipped_done,
        "missing_rows": missing,
        "pairs_path": str(pairs_path),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--processed-path", default="data/processed/articles.jsonl")
    ap.add_argument("--out-dir", default="data/ab_eval/run")
    ap.add_argument(
        "--base-url", default=None, help="Local vLLM endpoint (e.g. http://vllm:8000/v1)"
    )
    ap.add_argument("--model", default=None, help="Model override; 'auto' probes /v1/models")
    ap.add_argument("--timeout", type=float, default=None, help="Per-call generation deadline (s)")
    ap.add_argument("--limit", type=int, default=None, help="Cap cases this run (smoke tests)")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    corpus_by_link = {row.link: row for row in iter_processed(args.processed_path)}

    base = Settings()
    settings_on, settings_off = build_arm_settings(
        base, base_url=args.base_url, model=args.model, timeout=args.timeout
    )
    provider_on, provider_off = build_arm_providers(settings_on, settings_off)
    for arm, provider in ((ARM_ON, provider_on), (ARM_OFF, provider_off)):
        if not isinstance(provider, OpenAICompatSummarizer):
            raise SystemExit(
                f"{arm} primary provider is {type(provider).__name__}; the thinking knob "
                "only exists on OpenAI-compatible providers — point the chain at the vLLM"
            )
    if settings_on.openai_compat_timeout_seconds < TIMEOUT_HINT_SECONDS:
        print(
            f"note: timeout {settings_on.openai_compat_timeout_seconds:.0f}s < "
            f"{TIMEOUT_HINT_SECONDS:.0f}s — thinking-on decodes run 5-9 min/article; "
            "consider --timeout 900 or the on arm measures timeouts, not the model"
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    summary = run_pairs(
        manifest=manifest,
        corpus_by_link=corpus_by_link,
        provider_on=provider_on,
        provider_off=provider_off,
        settings=settings_on,  # arms share every knob except enable_thinking
        pairs_path=out_dir / PAIRS_FILENAME,
        limit=args.limit,
    )
    meta = {
        "manifest": str(args.manifest),
        "processed_path": str(args.processed_path),
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url or settings_on.openai_compat_base_url,
        "model_on": getattr(provider_on, "model_name", None),
        "model_off": getattr(provider_off, "model_name", None),
        "timeout_seconds": settings_on.openai_compat_timeout_seconds,
        **summary,
    }
    (out_dir / META_FILENAME).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
