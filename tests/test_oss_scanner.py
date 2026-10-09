"""Contract for the OSS Scanner enrolment directory (.oss-scanner/).

anthropics/oss-scanner builds `.oss-scanner/Dockerfile` with this repo as
the context and reads `.oss-scanner/threat_model.md` before it audits. The
live enrolment file lives in THEIR repo; the draft here must keep passing
the rules their tools/validate.py enforces, so an enrolment PR copied from
it is never bounced, and the paths it names must exist here.

The checks below re-state the validator's rules (regexes copied from it)
rather than vendoring it: the file is flat YAML, so a line parser is enough
and keeps pyyaml out of the dependency set.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / ".oss-scanner"

# From tools/validate.py in anthropics/oss-scanner.
ALLOWED_KEYS = {
    "repo",
    "primary_contact",
    "auto_ccs",
    "homepage",
    "pgp",
    "disabled",
    "dockerfile",
    "threat_model",
}
REQUIRED_KEYS = {"repo", "primary_contact"}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
REPO_PATH_RE = re.compile(r"(?!/)(?!.*\.\.)[A-Za-z0-9._/-]+")
FRAGMENT_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
MAX_FILE_BYTES = 64 * 1024

# The house voice bar (CLAUDE.md "Voice") — same list test_research_briefings pins.
BANNED = ("delve", "leverage", "robust", "comprehensive", "seamless", "holistic", "utilize")


def _flat_yaml(text: str) -> dict[str, str]:
    """Parse `key: value` lines of a flat YAML file; comments and blanks skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert not line.startswith((" ", "\t", "-")), f"draft must stay flat: {line!r}"
        key, sep, value = line.partition(":")
        assert sep, f"not a key: value line: {line!r}"
        out[key.strip()] = value.strip()
    return out


def _project() -> dict[str, str]:
    return _flat_yaml((SCAN / "project.yaml").read_text(encoding="utf-8"))


def test_directory_is_complete() -> None:
    for name in ("Dockerfile", "threat_model.md", "project.yaml", "seed_corpus.py", "README.md"):
        assert (SCAN / name).is_file(), name
        assert not (SCAN / name).is_symlink(), name


def test_project_yaml_passes_the_validators_rules() -> None:
    project = _project()
    assert REQUIRED_KEYS <= project.keys()
    assert project.keys() <= ALLOWED_KEYS, project.keys() - ALLOWED_KEYS

    repo, _, fragment = project["repo"].partition("#")
    assert repo == "https://github.com/Scubber/insider-intel"
    assert fragment == "" or FRAGMENT_RE.fullmatch(fragment)

    assert EMAIL_RE.fullmatch(project["primary_contact"])
    assert project.get("homepage", "https://x.y").startswith("https://")
    assert project.get("disabled", "false") in {"true", "false"}
    assert "pgp" not in project or "auto_ccs" not in project

    for key in ("dockerfile", "threat_model"):
        path = project[key]
        assert REPO_PATH_RE.fullmatch(path), path
        target = ROOT / path
        assert target.is_file(), f"{key}: {path} is not in this repo"
        assert target.resolve().parent == SCAN.resolve(), f"{key} must live in .oss-scanner/"
        assert target.stat().st_size <= MAX_FILE_BYTES, key
        target.read_text(encoding="utf-8")  # must be UTF-8 text


def test_no_operator_address_in_the_draft() -> None:
    """Addresses in project.yaml are public once merged over there; the
    operator's own never enters this repo (same rule as the digest lane)."""
    project = _project()
    contact = project["primary_contact"]
    assert contact.endswith("@example.invalid"), (
        "fill the real contact in the enrolment PR in anthropics/oss-scanner, not here"
    )
    text = (SCAN / "project.yaml").read_text(encoding="utf-8")
    for address in EMAIL_RE.findall(text):
        assert address.split("@")[1] == "example.invalid", address


def test_dockerfile_meets_the_scanner_contract() -> None:
    text = (SCAN / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . /src" in text, "the scanner expects the checkout at /src"
    assert re.search(r"^WORKDIR /src$", text, re.M)
    # The scanner's tools layer apt-installs curl/git/bash/procps on top and
    # runs as root; keep the base an apt distro and leave the user alone.
    assert re.search(r"^FROM python:3\.12-slim-bookworm", text, re.M)
    assert not re.search(r"^USER ", text, re.M)
    # Offline audit: the suite and the browser floor are fetched at build time.
    assert 'pip install -e ".[dev]"' in text
    assert "playwright install" in text
    assert "python .oss-scanner/seed_corpus.py" in text
    assert "pytest" in text


def test_threat_model_covers_the_template_sections_in_house_voice() -> None:
    text = (SCAN / "threat_model.md").read_text(encoding="utf-8")
    for heading in (
        "## What this project does and where untrusted input enters",
        "## Components that matter most / least",
        "## How to exercise it",
        "## How you rate severity",
        "## Anything to leave alone",
    ):
        assert heading in text, heading
    lowered = text.lower()
    for word in BANNED:
        assert not re.search(rf"\b{word}\b", lowered), word
    # Severity guidance names the tiers the scanner asks for.
    for tier in ("**Critical**", "**High**", "**Medium**", "**Low**"):
        assert tier in text, tier
    # The gates it tells the auditor to respect must still exist by name.
    api = (ROOT / "apps" / "search" / "api.py").read_text(encoding="utf-8")
    for symbol in ("_require_admin_token", "_require_export_token"):
        assert symbol in text and symbol in api, symbol


def test_seed_corpus_is_fictional_and_names_no_one() -> None:
    """The seed rides into every audit image: fictional hosts only, no
    person names — the EVIDENCE product reports roles, never individuals."""
    text = (SCAN / "seed_corpus.py").read_text(encoding="utf-8")
    links = re.findall(r'"link":\s*"([^"]+)"', text)
    assert links and all(link.startswith("https://example.invalid/") for link in links), links
    assert len(links) == len(set(links))
    assert not re.search(r"\b(Mr|Mrs|Ms|Dr)\.? [A-Z]", text)
