"""TOOLING rankings: tool categories scored against what real cases show.

The curated taxonomy (shared/data/tooling_map.json — categories, never vendor
brands, each mapped to the ITM DT*/PV* ids it implements) is checked in and
stable across sweeps. The RANKING is not: it is recomputed per call from the
verdict-gated evidence ledger's per-technique case counts, so a corpus sweep +
/reload changes the numbers on the next request with no redeploy — the same
sweep-freshness contract the matrix data-source tests pin.

Ranking formula (exact, per category c over observed techniques t — an
observed technique is a catalog technique with >= 1 verdict-true case with
extracted methods; volume unit is technique-case observations, the same unit
the EVIDENCE page reports):

    V                = sum(cases(t)) over all observed t
    detect_volume(c) = sum(cases(t)) for t where catalog_detections(t)
                       intersects c.detections
    detection_coverage_pct(c)  = round(100 * detect_volume(c) / V)
    prevent_volume(c) = sum(cases(t)) for t where catalog_preventions(t)
                        intersects c.preventions
    prevention_coverage_pct(c) = round(100 * prevent_volume(c) / V)

Corroboration reuses the ledger's conservative record-class crosswalk
(shared.utils.evidence.EVIDENCE_DT_CROSSWALK): a detected_by family names a
category when any of its crosswalked DT ids is mapped to that category;
corroborated_cases is the MAX of the naming families' distinct-case counts —
a floor on distinct corroborating cases (summing would double-count cases
that touch two families).

Small-n law: percentages are suppressed (None) when the contributing-case
base is under the ledger's SMALL_N_FLOOR; volumes are always reported.
Sort: detect_volume desc, prevent_volume desc, corroborated_cases desc, label.

Vendor ``examples`` are a display-only passthrough: each category's curated
list is carried verbatim onto its row (like label/rationale) and NEVER enters
the ranking math above — tests pin that stripping every examples array from
the map leaves the ranking output byte-identical. The mention-ranked vendor
rows the payload also carries (documented case-mention counts) are attached
AFTER ranking by apps/search/vendor_mentions.py under the same never-an-input
contract.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from shared.utils.evidence import EVIDENCE_DT_CROSSWALK

TOOLING_MAP_PATH = Path(__file__).resolve().parents[2] / "shared" / "data" / "tooling_map.json"

TOP_TECHNIQUES_PER_CATEGORY = 6


@lru_cache(maxsize=1)
def load_tooling_map(path: str | None = None) -> dict:
    """The checked-in category → DT/PV taxonomy (authored, sweep-stable)."""
    with open(path or TOOLING_MAP_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def rank_tool_categories(
    categories: list[dict],
    technique_counts: dict[str, dict],
    catalog: dict[str, dict],
    detected_by: list[dict],
    *,
    suppress_pct: bool = False,
) -> dict:
    """Pure ranking core (deterministic; unit-tested with synthetic fixtures).

    ``categories``       — tooling_map.json category entries.
    ``technique_counts`` — ledger technique_counts: {tech_id: {"cases": n, ...}}
                           (verdict-true cases with methods, per technique).
    ``catalog``          — {tech_id: {"title", "detections": [ids],
                           "preventions": [ids]}} from the packaged ITM index.
    ``detected_by``      — ledger detected_by rows ({"artifact", "cases", ...}).
    ``suppress_pct``     — small-n law: True nulls every percentage.
    """
    observed = {
        tid: int((counts or {}).get("cases") or 0)
        for tid, counts in technique_counts.items()
        if tid in catalog and int((counts or {}).get("cases") or 0) > 0
    }
    volume = sum(observed.values())
    family_cases = {
        str(row.get("artifact") or ""): int(row.get("cases") or 0) for row in detected_by
    }

    def pct(part: int) -> int | None:
        if suppress_pct or not volume:
            return None
        return round(100 * part / volume)

    rows = []
    for cat in categories:
        dt_ids = {str(x).upper() for x in cat.get("detections") or []}
        pv_ids = {str(x).upper() for x in cat.get("preventions") or []}

        det_techs = {t for t in observed if dt_ids & set(catalog[t].get("detections") or ())}
        prev_techs = {t for t in observed if pv_ids & set(catalog[t].get("preventions") or ())}
        detect_volume = sum(observed[t] for t in det_techs)
        prevent_volume = sum(observed[t] for t in prev_techs)

        naming = [
            (fam, family_cases[fam])
            for fam, fam_dts in EVIDENCE_DT_CROSSWALK.items()
            if fam in family_cases and dt_ids & set(fam_dts)
        ]
        naming.sort(key=lambda kv: (-kv[1], kv[0]))
        corroborated_cases = max((n for _, n in naming), default=0)

        top = sorted(det_techs | prev_techs, key=lambda t: (-observed[t], t))
        rows.append(
            {
                "id": cat.get("id"),
                "label": cat.get("label"),
                "kind": cat.get("kind"),
                "rationale": cat.get("rationale"),
                # Display-only vendor illustrations, carried verbatim — never
                # an input to any volume/percentage/sort computation below.
                "examples": list(cat.get("examples") or []),
                "detections": sorted(dt_ids),
                "preventions": sorted(pv_ids),
                "detect_volume": detect_volume,
                "prevent_volume": prevent_volume,
                "detection_coverage_pct": pct(detect_volume),
                "prevention_coverage_pct": pct(prevent_volume),
                "corroborated_cases": corroborated_cases,
                "corroborated_via": [fam for fam, _ in naming],
                "top_techniques": [
                    {
                        "id": t,
                        "title": catalog[t].get("title") or t,
                        "cases": observed[t],
                        "covers": (
                            "both"
                            if t in det_techs and t in prev_techs
                            else ("detect" if t in det_techs else "prevent")
                        ),
                    }
                    for t in top[:TOP_TECHNIQUES_PER_CATEGORY]
                ],
            }
        )

    rows.sort(
        key=lambda r: (
            -r["detect_volume"],
            -r["prevent_volume"],
            -r["corroborated_cases"],
            str(r["label"]),
        )
    )
    return {
        "observed_techniques": len(observed),
        "technique_case_volume": volume,
        "categories": rows,
    }
