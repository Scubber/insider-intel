"""Corpus-level hunt synthesis: generalized hunt patterns per technique.

Per-case ``hunt_queries``/``hunt_terms`` answer "how would you catch THAT
case"; the dossier needs "how do you catch this BEHAVIOR anywhere". That is a
synthesis task, so it runs here — on the corpus-refresh job, where all LLM
spend lives — one call per observed technique over its aggregated case
material (entity-filtered terms, method actions, artifact families, seed
queries), producing environment-portable patterns.

Results are cached by an input-material signature in
``data/state/technique_hunts.json`` (job-written under ``state/``, API reads
it — same contract as ``technique_seeds.json``): a technique is only
re-synthesized when its case material changes, so after the initial sweep the
steady-state spend is near zero. Bounded per run by ``HUNT_SYNTH_MAX_PER_RUN``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from apps.aggregator.processed_storage import JsonlProcessedStore
from shared.llm import get_synthesizer_chain
from shared.schemas.hunt_patterns import TechniqueHuntEntry, parse_patterns
from shared.settings import Settings, get_settings
from shared.utils.evidence import build_evidence_ledger

logger = logging.getLogger(__name__)

# A technique needs at least this many enriched cases before synthesis —
# one case's material is the case, not a pattern.
MIN_CASES_FOR_SYNTHESIS = 2


class TechniqueHuntStore:
    """Atomic JSON store for the synthesized-hunts view (tmp + replace)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> dict[str, TechniqueHuntEntry]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            entries = {}
            for tech_id, raw in (payload.get("entries") or {}).items():
                entries[tech_id] = TechniqueHuntEntry.model_validate(raw)
            return entries
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable technique-hunts store %s: %s", self.path, exc)
            return {}

    def write(self, entries: dict[str, TechniqueHuntEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "entries": {t: e.model_dump(mode="json") for t, e in sorted(entries.items())},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)


def technique_material(ledger: dict, tech_id: str) -> dict:
    """One technique's aggregated case material — the synthesis input."""
    counts = ledger.get("technique_counts", {}).get(tech_id) or {}
    families = ledger.get("technique_families", {}).get(tech_id) or {}
    material = {
        "technique_id": tech_id,
        "cases": counts.get("cases", 0),
        "adjudicated_admitted": counts.get("adjudicated_admitted", 0),
        "behaviors": ledger.get("technique_behaviors", {}).get(tech_id, []),
        "generic_indicators": ledger.get("technique_terms", {}).get(tech_id, []),
        "evidence_artifact_families": sorted(families, key=families.get, reverse=True)[:8],
        "case_query_seeds": [
            {"stack": h.get("stack"), "logic": h.get("logic"), "rationale": h.get("rationale")}
            for h in ledger.get("technique_hunts", {}).get(tech_id, [])
        ],
    }
    try:  # Title/description give the model the technique's intent.
        from shared.itm.index import load_itm_index

        tech = next((t for t in load_itm_index().techniques if t.id.upper() == tech_id), None)
        if tech:
            material["technique_title"] = tech.title
            material["technique_description"] = tech.description_text
    except Exception:  # noqa: BLE001 — packaged index optional in some envs
        pass
    return material


def material_signature(material: dict) -> str:
    """Stable hash of the synthesis input; changed input ⇒ stale cache."""
    canon = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


@dataclass
class HuntSynthesisResult:
    eligible: int = 0
    stale: int = 0
    generated: int = 0
    cached: int = 0
    failed: int = 0
    stale_ids: list[str] = field(default_factory=list)


def run_hunt_synthesis(
    processed_path: str | Path | None = None,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
) -> HuntSynthesisResult:
    """Synthesize hunt patterns for stale techniques, bounded by the run cap."""
    settings = settings or get_settings()
    result = HuntSynthesisResult()
    store = TechniqueHuntStore(settings.technique_hunts_path)
    entries = store.read()

    rows = (
        {
            "link": a.link,
            "title": a.title,
            "published": a.published.isoformat() if a.published else "",
            "forensics": a.forensics.model_dump(mode="json") if a.forensics else None,
        }
        for a in JsonlProcessedStore(processed_path or settings.processed_articles_path).load_all()
    )
    ledger = build_evidence_ledger(rows, top=1)

    # Biggest case sets first: the strongest material synthesizes first.
    ranked = sorted(
        ledger.get("technique_counts", {}).items(),
        key=lambda kv: -kv[1].get("cases", 0),
    )
    stale: list[tuple[str, dict, str]] = []
    for tech_id, counts in ranked:
        if counts.get("cases", 0) < MIN_CASES_FOR_SYNTHESIS:
            continue
        result.eligible += 1
        material = technique_material(ledger, tech_id)
        sig = material_signature(material)
        prior = entries.get(tech_id)
        if prior is not None and prior.signature == sig and prior.patterns:
            result.cached += 1
            continue
        stale.append((tech_id, material, sig))
    result.stale = len(stale)
    result.stale_ids = [t for t, _, _ in stale]

    budget = settings.hunt_synth_max_per_run
    if dry_run or budget <= 0 or not stale:
        if not dry_run and stale and budget <= 0:
            logger.info("Hunt synthesis disabled (HUNT_SYNTH_MAX_PER_RUN=0); %d stale", len(stale))
        return result

    chain = get_synthesizer_chain(settings)
    if not chain:
        logger.info("Hunt synthesis skipped: no LLM provider configured; %d stale", len(stale))
        return result

    for tech_id, material, sig in stale[:budget]:
        payload = json.dumps(material, ensure_ascii=False)
        patterns = []
        model_used = ""
        for provider in chain:
            raw = provider.synthesize_hunts(technique_json=payload)
            if raw is None:
                continue
            patterns = parse_patterns(raw)
            model_used = getattr(provider, "model_name", "")
            break
        if not patterns:
            result.failed += 1
            continue
        entries[tech_id] = TechniqueHuntEntry(
            technique_id=tech_id,
            signature=sig,
            generated_at=datetime.now(UTC).isoformat(),
            model=model_used,
            case_count=material["cases"],
            adjudicated_count=material["adjudicated_admitted"],
            patterns=patterns,
        )
        result.generated += 1

    # Best-effort write — a read-only state dir degrades to a stale view.
    try:
        store.write(entries)
    except OSError as exc:
        logger.warning("Could not write technique-hunts view: %s", exc)

    if result.generated:
        logger.info(
            "[OK] hunt synthesis: %d generated, %d cached, %d failed, %d still stale",
            result.generated,
            result.cached,
            result.failed,
            result.stale - result.generated - result.failed,
        )
    elif result.failed:
        # Same tripwire vocabulary as enrichment: attempts with zero output
        # means every provider call failed, not a quiet run.
        logger.error(
            "[FAIL] hunt synthesis: %d LLM attempt(s), 0 pattern sets produced — "
            "check API credits/keys (HUNT_SYNTH_LLM_PROVIDER chain)",
            result.failed,
        )
    return result
