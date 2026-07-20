"""RSS feed source configuration.

Add new sources by appending a FeedSource to DEFAULT_FEEDS, or by
loading a JSON file via load_feeds_from_file().

Feedly board parity (Insider Threats x Top Stories / ITM-Hunt):
  1. Best: FEEDLY_ACCESS_TOKEN + FEEDLY_STREAM_IDS → ingest_feedly
  2. Fallback: these DEFAULT_FEEDS (publishers seen on that board style)
  3. Or --feeds-file apps/aggregator/feeds.insider_board.example.json

Tip / social-adjacent (Reddit RSS, channel=tips):
  - Included in DEFAULT_FEEDS (reddit-*)
  - Or --feeds-file apps/aggregator/feeds.tips.example.json

Multi-domain HR/legal (see docs/sourcing.md):
  - DEFAULT_FEEDS includes hrdive + proskauer-workplace
  - Or --feeds-file apps/aggregator/feeds.multi_domain.example.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.schemas import FeedSource

logger = logging.getLogger(__name__)

# Publishers that commonly appear on insider-threat Feedly boards
# (Insider Threats x Top Stories / ITM-Hunt style OSINT).
DEFAULT_FEEDS: list[FeedSource] = [
    # Core security OSINT
    FeedSource(
        id="krebsonsecurity",
        name="Krebs on Security",
        url="https://krebsonsecurity.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="darkreading",
        name="Dark Reading",
        url="https://www.darkreading.com/rss.xml",
        category="insider-osint",
    ),
    FeedSource(
        id="bleepingcomputer",
        name="BleepingComputer",
        url="https://www.bleepingcomputer.com/feed/",
        category="osint",
    ),
    FeedSource(
        id="thehackernews",
        name="The Hacker News",
        url="https://feeds.feedburner.com/TheHackersNews",
        category="osint",
    ),
    FeedSource(
        id="securityaffairs",
        name="Security Affairs",
        url="https://securityaffairs.com/feed",
        category="insider-osint",
    ),
    FeedSource(
        id="helpnetsecurity",
        name="Help Net Security",
        url="https://www.helpnetsecurity.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="securityboulevard",
        name="Security Boulevard",
        url="https://securityboulevard.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="cybersecuritydive",
        name="Cybersecurity Dive",
        url="https://www.cybersecuritydive.com/feeds/news/",
        category="insider-osint",
    ),
    FeedSource(
        id="cyberscoop",
        name="CyberScoop",
        url="https://www.cyberscoop.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="infosecurity-magazine",
        name="Infosecurity Magazine",
        url="https://www.infosecurity-magazine.com/rss/news/",
        category="insider-osint",
    ),
    FeedSource(
        id="theregister-security",
        name="The Register — Security",
        url="https://www.theregister.com/security/headlines.atom",
        category="insider-osint",
    ),
    FeedSource(
        id="therecord",
        name="The Record",
        url="https://therecord.media/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="technadu",
        name="TechNadu",
        url="https://www.technadu.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="cybersecuritynews",
        name="Cyber Security News",
        url="https://cybersecuritynews.com/feed/",
        category="insider-osint",
    ),
    # Healthcare / privacy / insider breach reporting
    FeedSource(
        id="hipaajournal",
        name="The HIPAA Journal",
        url="https://www.hipaajournal.com/feed/",
        category="insider-healthcare",
    ),
    FeedSource(
        id="hipaajournal-healthcare-cyber",
        name="HIPAA Journal — Healthcare Cybersecurity",
        url="https://www.hipaajournal.com/category/healthcare-cybersecurity/feed/",
        category="insider-healthcare",
    ),
    # Legal / gov / national security (espionage, sentencing, clearances)
    FeedSource(
        id="doj-press",
        name="U.S. DOJ Press Releases",
        url="https://www.justice.gov/news/rss?type=press_release&subtype=press_release",
        category="insider-legal",
    ),
    FeedSource(
        id="hstoday",
        name="HSToday",
        url="https://www.hstoday.us/feed/",
        category="insider-national-security",
        enabled=False,  # HTTP 403 from datacenter/CI IPs
    ),
    # Vendor / research blogs that surface insider / DPRK IT worker TTPs
    FeedSource(
        id="group-ib",
        name="Group-IB Blog",
        url="https://www.group-ib.com/blog/feed/",
        category="threat-research",
        enabled=False,  # feed 404
    ),
    FeedSource(
        id="darktrace",
        name="Darktrace Blog",
        url="https://www.darktrace.com/blog/rss.xml",
        category="threat-research",
    ),
    FeedSource(
        id="upguard",
        name="UpGuard Blog",
        url="https://www.upguard.com/blog/rss.xml",
        category="threat-research",
    ),
    # Crypto / fintech (insider / rogue employee / KYC leak stories)
    FeedSource(
        id="coincentral",
        name="CoinCentral",
        url="https://coincentral.com/feed/",
        category="insider-crypto",
        enabled=False,  # noisy for insider signal; re-enable if needed
    ),
    FeedSource(
        id="cointelegraph",
        name="Cointelegraph",
        url="https://cointelegraph.com/rss",
        category="insider-crypto",
        enabled=False,
    ),
    FeedSource(
        id="dailyhodl",
        name="The Daily Hodl",
        url="https://dailyhodl.com/feed/",
        category="insider-crypto",
        enabled=False,
    ),
    # Legal / gov — SEC litigation (stable RSS)
    FeedSource(
        id="sec-litigation",
        name="SEC Litigation Releases",
        url="https://www.sec.gov/enforcement-litigation-releases.rss",
        category="insider-legal",
    ),
    FeedSource(
        id="sec-press",
        name="SEC Press Releases",
        url="https://www.sec.gov/news/pressreleases.rss",
        category="insider-legal",
    ),
    # Hunt signal / advisories
    FeedSource(
        id="sans-isc",
        name="SANS Internet Storm Center",
        url="https://isc.sans.edu/rssfeed_full.xml",
        category="hunt-signal",
    ),
    FeedSource(
        id="cisa-advisories",
        name="CISA Cybersecurity Advisories",
        url="https://www.cisa.gov/cybersecurity-advisories/all.xml",
        category="advisory",
        enabled=False,  # often 403 without browser UA; re-enable when fixed
    ),
    # Additional volume — breach / insider / legal / research
    FeedSource(
        id="databreaches",
        name="DataBreaches.net",
        url="https://www.databreaches.net/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="bankinfosecurity",
        name="BankInfoSecurity",
        url="https://www.bankinfosecurity.com/rss-feeds",
        category="insider-osint",
    ),
    FeedSource(
        id="govinfosecurity",
        name="GovInfoSecurity",
        url="https://www.govinfosecurity.com/rss-feeds",
        category="insider-national-security",
    ),
    FeedSource(
        id="healthcareinfosec",
        name="HealthcareInfoSecurity",
        url="https://www.healthcareinfosecurity.com/rss-feeds",
        category="insider-healthcare",
    ),
    FeedSource(
        id="csoonline",
        name="CSO Online",
        url="https://www.csoonline.com/feed",
        category="insider-osint",
    ),
    FeedSource(
        id="scmagazine",
        name="SC Media",
        url="https://www.scworld.com/feed",
        category="insider-osint",
    ),
    FeedSource(
        id="securityweek",
        name="SecurityWeek",
        url="https://www.securityweek.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="zdnet-security",
        name="ZDNET Security",
        url="https://www.zdnet.com/topic/security/rss.xml",
        category="osint",
    ),
    FeedSource(
        id="ars-security",
        name="Ars Technica — Security",
        url="https://arstechnica.com/security/feed/",
        category="osint",
    ),
    FeedSource(
        id="wired-security",
        name="WIRED Security",
        url="https://www.wired.com/feed/category/security/latest/rss",
        category="osint",
    ),
    FeedSource(
        id="federalnewsnetwork",
        name="Federal News Network",
        url="https://federalnewsnetwork.com/feed/",
        category="insider-national-security",
    ),
    FeedSource(
        id="nextgov",
        name="Nextgov/FCW",
        url="https://www.nextgov.com/rss/all/",
        category="insider-national-security",
    ),
    FeedSource(
        id="lawfare",
        name="Lawfare",
        url="https://www.lawfaremedia.org/feed/articles",
        category="insider-legal",
        enabled=False,  # HTTP 403
    ),
    FeedSource(
        id="mandiant",
        name="Google Cloud Security Blog",
        url="https://cloud.google.com/blog/topics/security/rss",
        category="threat-research",
    ),
    FeedSource(
        id="unit42",
        name="Palo Alto Unit 42",
        url="https://unit42.paloaltonetworks.com/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="msrc-blog",
        name="Microsoft Security Blog",
        url="https://www.microsoft.com/en-us/security/blog/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="crowdstrike",
        name="CrowdStrike Blog",
        url="https://www.crowdstrike.com/blog/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="ic3",
        name="FBI IC3 News",
        url="https://www.ic3.gov/PSA/RSS",
        category="insider-legal",
    ),
    # Multi-domain insider risk (HR / employment law) — not infosec-only
    FeedSource(
        id="hrdive",
        name="HR Dive",
        url="https://www.hrdive.com/feeds/news/",
        category="hr",
        channel="news",
    ),
    FeedSource(
        id="proskauer-workplace",
        name="Proskauer Law and the Workplace",
        url="https://www.lawandtheworkplace.com/feed/",
        category="legal",
        channel="news",
    ),
    # Trade-secret / insider-theft beat — no public RSS; use ingest_datatheftnews
    FeedSource(
        id="datatheftnews",
        name="DataTheftNews",
        url="https://www.datatheftnews.com/blog",
        category="insider-osint",
        channel="news",
        enabled=False,
    ),
    # Tip / social-adjacent via Reddit RSS (no API key). Same RawArticle plane.
    FeedSource(
        id="reddit-netsec",
        name="Reddit r/netsec",
        url="https://www.reddit.com/r/netsec/.rss",
        category="tips-reddit",
        channel="tips",
    ),
    FeedSource(
        id="reddit-malware",
        name="Reddit r/Malware",
        url="https://www.reddit.com/r/Malware/.rss",
        category="tips-reddit",
        channel="tips",
    ),
    FeedSource(
        id="reddit-cybersecurity",
        name="Reddit r/cybersecurity",
        url="https://www.reddit.com/r/cybersecurity/.rss",
        category="tips-reddit",
        channel="tips",
    ),
    FeedSource(
        id="reddit-blueteamsec",
        name="Reddit r/blueteamsec",
        url="https://www.reddit.com/r/blueteamsec/.rss",
        category="tips-reddit",
        channel="tips",
    ),
    FeedSource(
        id="reddit-dfir",
        name="Reddit r/DFIR",
        url="https://www.reddit.com/r/DFIR/.rss",
        category="tips-reddit",
        channel="tips",
    ),
    # Insider-focused research + Scattered-Spider / attribution-forensics sources
    FeedSource(
        id="sei-insider-threat",
        name="CMU SEI Blog",
        # insights.sei.cmu.edu feed 404s since the domain move; the topic feed
        # is scoped to insider threat only (better signal than all SEI posts).
        url="https://www.sei.cmu.edu/blog/feeds/topic/insider-threat/atom/",
        category="insider-osint",
    ),
    FeedSource(
        id="404media",
        name="404 Media",
        url="https://www.404media.co/rss/",
        category="insider-osint",
    ),
    FeedSource(
        id="cybernews",
        name="Cybernews",
        url="https://cybernews.com/feed/",
        category="insider-osint",
    ),
    FeedSource(
        id="dtex-i3",
        name="DTEX i3 Blog",
        url="https://www.dtexsystems.com/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="google-threat-intel",
        name="Google Threat Intelligence",
        url="https://cloudblog.withgoogle.com/topics/threat-intelligence/rss/",
        category="threat-research",
    ),
    FeedSource(
        id="talos",
        name="Cisco Talos",
        url="https://blog.talosintelligence.com/rss/",
        category="threat-research",
    ),
    FeedSource(
        id="microsoft-security",
        name="Microsoft Security Blog",
        url="https://www.microsoft.com/en-us/security/blog/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="darkatlas",
        name="DarkAtlas",
        url="https://darkatlas.io/blog/rss.xml",
        category="threat-research",
    ),
    FeedSource(
        id="dfir-report",
        name="The DFIR Report",
        url="https://thedfirreport.com/feed/",
        category="threat-research",
    ),
    FeedSource(
        id="securityonline",
        name="SecurityOnline",
        url="https://securityonline.info/feed/",
        category="threat-research",
    ),
]


def get_enabled_feeds(feeds: list[FeedSource] | None = None) -> list[FeedSource]:
    """Return only enabled feed sources."""
    sources = feeds if feeds is not None else DEFAULT_FEEDS
    return [f for f in sources if f.enabled]


def load_feeds_from_file(path: str | Path) -> list[FeedSource]:
    """Load feed sources from a JSON file.

    Expected format::

        [
          {
            "id": "example",
            "name": "Example Feed",
            "url": "https://example.com/feed.xml",
            "enabled": true,
            "category": "insider-osint"
          }
        ]
    """
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("Feed config file not found: %s", file_path)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in feed config %s: %s", file_path, exc)
        raise

    if not isinstance(raw, list):
        raise ValueError(f"Feed config must be a JSON array, got {type(raw).__name__}")

    feeds = [FeedSource.model_validate(item) for item in raw]
    logger.info("Loaded %d feed source(s) from %s", len(feeds), file_path)
    return feeds
