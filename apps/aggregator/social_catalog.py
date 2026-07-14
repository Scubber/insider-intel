"""Curated social discovery catalog, derived from the use-case registry."""

from __future__ import annotations

from apps.aggregator.social_subscriptions import (
    SocialSubscription,
    normalize_handle,
    social_source_id,
)
from shared.schemas import SocialSourceInfo
from shared.taxonomy.use_cases import USE_CASES

# General-interest seeds not tied to one use case.
_GENERAL_SUBREDDITS: tuple[tuple[str, str], ...] = (
    ("cybersecurity_help", "First-person security incidents and mishaps"),
    ("AskHR", "Policy-violation and disclosure dilemmas from employees"),
)


def reddit_url(sub: str) -> str:
    return f"https://www.reddit.com/r/{sub}/"


def x_url(handle: str) -> str:
    return f"https://x.com/{handle}"


def build_catalog() -> list[SocialSourceInfo]:
    """Suggested subreddits / X accounts, grouped-by-use-case via use_cases field."""
    by_key: dict[tuple[str, str], SocialSourceInfo] = {}

    def upsert(platform: str, handle: str, use_case_id: str | None, description: str) -> None:
        normalized = normalize_handle(platform, handle)
        key = (platform, normalized)
        info = by_key.get(key)
        if info is None:
            name = f"r/{normalized}" if platform == "reddit" else f"@{handle.lstrip('@')}"
            url = reddit_url(normalized) if platform == "reddit" else x_url(normalized)
            info = SocialSourceInfo(
                platform=platform,
                id=normalized,
                name=name,
                url=url,
                description=description,
                source_id=social_source_id(platform, normalized),
                origin="catalog",
            )
            by_key[key] = info
        if use_case_id and use_case_id not in info.use_cases:
            info.use_cases.append(use_case_id)

    for uc in USE_CASES:
        for sub in uc.subreddits:
            upsert("reddit", sub, uc.id, f"Suggested for {uc.label}")
        for handle in uc.x_accounts:
            upsert("x", handle, uc.id, f"Suggested for {uc.label}")
    for sub, description in _GENERAL_SUBREDDITS:
        upsert("reddit", sub, None, description)

    return sorted(by_key.values(), key=lambda s: (s.platform, s.id))


def subscription_to_info(sub: SocialSubscription) -> SocialSourceInfo:
    url = reddit_url(sub.id) if sub.platform == "reddit" else x_url(sub.id)
    return SocialSourceInfo(
        platform=sub.platform,
        id=sub.id,
        name=sub.display_name(),
        url=url,
        use_cases=list(sub.use_cases),
        source_id=sub.source_id(),
        subscribed=sub.enabled,
        origin=sub.origin,
    )
