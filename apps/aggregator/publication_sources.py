"""Curated catalog of long-form reference publications (guides, whitepapers).

Code-defined like archive_sources.py: low-churn, PR-reviewed, and no new
bucket-write surface. source_id convention is ``pub-<slug>`` — the prefix is
what resolve_channel maps to the ``publications`` channel.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationSource:
    """One long-form publication: an HTML landing page and/or a PDF."""

    id: str  # must start with "pub-"
    name: str
    url: str  # landing page (or direct PDF) URL
    pdf_url: str | None = None  # explicit PDF override; else discovered on page
    enabled: bool = True


DEFAULT_PUBLICATION_SOURCES: list[PublicationSource] = [
    PublicationSource(
        id="pub-sei-common-sense-guide-7e",
        name="SEI Common Sense Guide to Mitigating Insider Threats (7th ed.)",
        url=(
            "https://www.sei.cmu.edu/library/"
            "common-sense-guide-to-mitigating-insider-threats-seventh-edition/"
        ),
    ),
    PublicationSource(
        id="pub-cisa-insider-threat-mitigation-guide",
        name="CISA Insider Threat Mitigation Guide",
        url="https://www.cisa.gov/resources-tools/resources/insider-threat-mitigation-guide",
        pdf_url=(
            "https://www.cisa.gov/sites/default/files/publications/"
            "Insider%20Threat%20Mitigation%20Guide_Final_508.pdf"
        ),
    ),
    PublicationSource(
        id="pub-nittf-insider-threat-guide-2017",
        name="NITTF Insider Threat Guide (2017)",
        url=("https://www.dni.gov/files/NCSC/documents/nittf/NITTF-Insider-Threat-Guide-2017.pdf"),
    ),
]


def get_publication_sources(
    source_ids: list[str] | None = None,
) -> list[PublicationSource]:
    """Return enabled publication sources, optionally filtered by id."""
    enabled = [s for s in DEFAULT_PUBLICATION_SOURCES if s.enabled]
    if not source_ids:
        return enabled
    wanted = {s.strip().lower() for s in source_ids if s and s.strip()}
    return [s for s in enabled if s.id.lower() in wanted]
