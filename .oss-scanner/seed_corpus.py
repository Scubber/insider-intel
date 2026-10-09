"""Write a small synthetic corpus for the OSS Scanner audit image.

The audit runs with no network and no bucket, so the API would otherwise
serve an empty index. This seeds a handful of FICTIONAL rows through the
real processing graph (``process_article``) and the real store, so every
read endpoint, the EVIDENCE ledger, TOOLING and the UI have something to
render. Nothing here is a real case: companies, roles and facts are made
up, and no person is named — the EVIDENCE product reports roles, never
individuals, and the seed follows the same rule.

Run from the repo root (the Dockerfile does):

    PROCESSED_ARTICLES_PATH=data/processed/articles.jsonl \\
    RAW_ARTICLES_PATH=data/raw/articles.jsonl \\
    python .oss-scanner/seed_corpus.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.aggregator.processed_storage import JsonlProcessedStore  # noqa: E402
from apps.aggregator.storage import JsonlArticleStore  # noqa: E402
from shared.agents import process_article  # noqa: E402
from shared.schemas import RawArticle  # noqa: E402
from shared.settings import get_settings  # noqa: E402

SEED: list[dict] = [
    {
        "title": "Departing engineer copied 4,000 design files to a USB drive",
        "link": "https://example.invalid/news/usb-exfiltration",
        "summary": (
            "A disgruntled departing employee at Example Robotics used removable "
            "media for exfiltration via physical medium after a mass download "
            "from the engineering file share in the week before resignation."
        ),
        "published": datetime(2025, 3, 4, tzinfo=UTC),
        "source_id": "example-news",
        "source_name": "Example Security News",
        "channel": "news",
    },
    {
        "title": "Example Bank v. former analyst: trade-secret complaint",
        "link": "https://example.invalid/filings/example-bank-v-analyst",
        "summary": (
            "Complaint alleges a former analyst emailed client lists to a "
            "personal webmail account and forwarded pricing models to a "
            "competitor before giving notice. Insider threat, data theft, "
            "breach of confidentiality agreement."
        ),
        "published": datetime(2025, 1, 15, tzinfo=UTC),
        "source_id": "courtlistener-recap",
        "source_name": "CourtListener (RECAP)",
        "channel": "filings",
    },
    {
        "title": "Contractor kept VPN credentials after offboarding, accessed payroll",
        "link": "https://example.invalid/news/credential-misuse-contractor",
        "summary": (
            "An IT contractor whose contract ended retained valid VPN credentials "
            "for six weeks and logged in to the payroll system. Credential misuse, "
            "privileged access abuse, failed offboarding."
        ),
        "published": datetime(2025, 5, 20, tzinfo=UTC),
        "source_id": "example-news",
        "source_name": "Example Security News",
        "channel": "news",
    },
    {
        "title": "I work two remote jobs and neither employer knows",
        "link": "https://example.invalid/tips/overemployment-confession",
        "summary": (
            "First-person post: I am overemployed, running two full-time remote "
            "jobs on two laptops with a mouse jiggler so the status stays green. "
            "Moonlighting, concurrent employment."
        ),
        "published": datetime(2025, 6, 2, tzinfo=UTC),
        "source_id": "reddit-overemployed",
        "source_name": "Reddit r/overemployed",
        "channel": "tips",
    },
    {
        "title": "Hospital clerk accessed patient records without a care relationship",
        "link": "https://example.invalid/news/snooping-medical-records",
        "summary": (
            "A records clerk at Example Health viewed medical records of 200 "
            "patients with no treatment relationship over eleven months. "
            "Unauthorized access, privacy breach, insider snooping detected by "
            "an access-log audit."
        ),
        "published": datetime(2024, 11, 8, tzinfo=UTC),
        "source_id": "example-news",
        "source_name": "Example Security News",
        "channel": "news",
    },
    {
        "title": "Sales director uploaded CRM export to a personal drive before joining a rival",
        "link": "https://example.invalid/filings/example-saas-v-director",
        "summary": (
            "Example SaaS Inc. sues a former sales director who exported the "
            "customer database and uploaded it to a personal cloud storage account "
            "two days before resigning to join a competitor. Trade secret "
            "misappropriation, data exfiltration via cloud storage."
        ),
        "published": datetime(2025, 2, 27, tzinfo=UTC),
        "source_id": "courtlistener-opinions",
        "source_name": "CourtListener (opinions)",
        "channel": "filings",
    },
    {
        "title": "Employee installed unapproved AI assistant that synced confidential documents",
        "link": "https://example.invalid/news/shadow-it-ai-assistant",
        "summary": (
            "A finance employee installed an unapproved browser extension that "
            "uploaded confidential board documents to a third-party AI service. "
            "Shadow IT, unintentional insider, data leakage through unsanctioned "
            "software."
        ),
        "published": datetime(2025, 7, 11, tzinfo=UTC),
        "source_id": "example-news",
        "source_name": "Example Security News",
        "channel": "news",
    },
    {
        "title": "Sysadmin planted logic bomb after demotion, court finds",
        "link": "https://example.invalid/filings/example-logistics-sabotage",
        "summary": (
            "A systems administrator demoted after a performance review planted "
            "a scheduled script that deleted server configurations when his "
            "account was disabled. Sabotage, insider threat, malicious code, "
            "convicted of computer intrusion."
        ),
        "published": datetime(2024, 9, 30, tzinfo=UTC),
        "source_id": "courtlistener-recap",
        "source_name": "CourtListener (RECAP)",
        "channel": "filings",
    },
]


def main() -> int:
    settings = get_settings()
    raw_path = Path(settings.raw_articles_path)
    processed_path = Path(settings.processed_articles_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.parent.mkdir(parents=True, exist_ok=True)

    raw = [RawArticle(**row) for row in SEED]
    JsonlArticleStore(raw_path).save(raw)
    processed = [process_article(article) for article in raw]
    JsonlProcessedStore(processed_path).save(processed)
    print(f"seeded {len(processed)} synthetic rows -> {processed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
