# tools/

`tag_cases.py` — derived-layer tagging (stdlib only). Run it wherever the
corpus file is readable (sparky, an Actions runner, a GCS-enabled shell):

```bash
python3 analyses/voya-top10-profiles/tools/tag_cases.py \
    /path/to/processed/articles.jsonl --out /tmp/voya-tagging \
    --industry financial-services --country US
```

It writes `cases-tagged.jsonl` (every gated case, all slices),
`evidence-skeleton.csv` (the ranking slice with the read-layer columns
empty) and `counts.md`. The gate and the normalizers are the corpus' own
(`shared/utils/evidence.py`, `scripts/industry_actor_profiles.py`), so the
funnel matches the `corpus-industry` diagnostic exactly. Hint columns
(`hint_*`) are regex suggestions for the analyst pass and are never
reported as findings.
