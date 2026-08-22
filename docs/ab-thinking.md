# Thinking on/off A/B — the OPENAI_COMPAT_ENABLE_THINKING gate

`OPENAI_COMPAT_ENABLE_THINKING=false` is merged but inactive. The 2x-decode
numbers below were measured on the retired Qwen3.8 chain; the production
enrichment model is Nemotron 3 Super 120B (no reasoning parser — the knob
measured neutral there), so this gate matters mainly when evaluating a new
model that thinks. The gate decides — mechanically, with no operator
reviews — whether the knob may be activated. The output is an accept/reject
recommendation, not a data review
session.

The harness is three scripts under `scripts/`, all offline-testable
(`tests/test_ab_goldset.py`, `tests/test_ab_runner.py`,
`tests/test_ab_report.py`). None of them ever writes to the processed corpus:
step 1 is read-only + one manifest file, step 2 appends only inside its own
`--out-dir`, step 3 writes the report next to the pairs file. Step 2 is
checkpointed per case and safe to interrupt/resume in a refresh-cycle gap.

## Step 1 — select the gold set (anywhere with the corpus)

```bash
python -m scripts.ab_select_goldset \
  --processed-path data/processed/articles.jsonl \
  --out data/ab_eval/goldset_manifest.json --n 40 --seed 42
```

Picks N (default 40) already-enriched rows, deterministic given the corpus
(sha256(seed:link) ordering — no wall clock, no RNG state). Eligibility:
a forensic record exists and `clean_text` is at or above the enrichment gate
(`--min-text-chars`, default 1500 = `SUMMARIZER_FILING_MIN_TEXT_CHARS`).
Selection round-robins stratification cells of
is_insider_case x method bucket (poor <=1 / mid / rich >=3) x body-length
bucket (short <5k / mid / long >=20k chars) x legal_posture, taking rows
with a **strong baseline** (a `claude-sonnet-5*` generation in
`enrichment_history`) first inside each cell. Every pick's manifest entry
records its cell, rationale, and baseline generation reference.

## Step 2 — run the A/B (on sparky, in a cycle gap)

```bash
python -m scripts.ab_thinking_run \
  --manifest data/ab_eval/goldset_manifest.json \
  --processed-path data/processed/articles.jsonl \
  --out-dir data/ab_eval/run \
  --base-url http://vllm:8000/v1 --model auto --timeout 900
```

Each case is enriched TWICE through the production machinery
(`shared/llm/get_summarizer_chain` -> `OpenAICompatSummarizer`): two Settings
copies differing only in `openai_compat_enable_thinking` build the two arms,
so the off arm sends `chat_template_kwargs.enable_thinking=false` and the on
arm sends the byte-identical pre-knob payload. The call mirrors
`enrich_fields` exactly (per-channel truncation, ITM shortlist, lenient
parse, verbatim stamp). Per arm it records the full forensic record,
wall-clock seconds, completion-token usage when the server returns it, and
parse success. Arm order alternates deterministically per case. One pair
line is appended to `ab_pairs.jsonl` after each case; re-running skips
recorded pairs, so a timeout or Ctrl-C costs at most one case. Keys are
never CLI arguments — the normal settings resolution supplies them.
`--timeout 900` matters: thinking-on decodes outlast the 90s default and an
undersized deadline would measure timeouts, not the model.

## Step 3 — judge + report

```bash
python -m scripts.ab_thinking_report \
  --pairs data/ab_eval/run/ab_pairs.jsonl \
  --manifest data/ab_eval/goldset_manifest.json \
  --processed-path data/processed/articles.jsonl
```

Writes `ab_report.md` + `ab_report.json`. Mechanical metrics only, per arm:
parse-failure rate; verdict agreement between arms and vs the strong
baseline; `evidence_quote_verbatim` rate (replayed from stored `clean_text`,
not trusted from the runner); methods and confidence distributions;
wall-clock (the speed claim, measured); hunt-relevant field presence
(`hunt_terms` feeds hunt synthesis; `hunt_queries` is informational — that
field is dead).

Recommendation logic (constants in `scripts/ab_thinking_report.py`):
**ACTIVATE** iff all four hold, else **KEEP THINKING** with the failing
criteria named —

1. `parse_failure` — off's parse-failure rate <= on's.
2. `baseline_agreement` — off's verdict agreement with the claude-sonnet-5
   baseline >= `AGREEMENT_TOLERANCE` (0.95) x on's agreement (vacuous pass
   if the gold set has no baseline rows).
3. `verbatim_rate` — off's verbatim-quote rate no more than
   `VERBATIM_MAX_DROP_POINTS` (5) percentage points below on's (vacuous pass
   if an arm claimed no quotes).
4. `speedup` — mean wall-clock on/off >= `MIN_SPEEDUP` (1.5x), measured over
   pairs where both arms parsed.

`--judge` adds an OPTIONAL blind LLM preference pass (randomized record
order, the judge never sees arm names) reported in its own section. It is
the same served model grading itself — the model-grades-model circularity is
printed in the report — and it never feeds the ACTIVATE/KEEP THINKING
verdict. Default off.

## Acting on the result

ACTIVATE means: set `OPENAI_COMPAT_ENABLE_THINKING=false` in `.env.spark`
and expect roughly the measured speedup per enrichment plus working hunt
synthesis (thinking no longer eats `SYNTH_MAX_TOKENS`). KEEP THINKING means
the named criterion regressed; the pairs JSONL has the per-case evidence if
a follow-up investigation is warranted.
