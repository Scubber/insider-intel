"""Product taxonomy for hunt use cases and insider types.

Distinct from shared.itm (vendored Insider Threat Matrix™ data): these are
insider-intel's own cut-and-dry classification buckets.
"""

from shared.taxonomy.use_cases import (
    USE_CASES,
    UseCaseDef,
    get_use_case,
    use_case_ids,
)

__all__ = ["USE_CASES", "UseCaseDef", "get_use_case", "use_case_ids"]
