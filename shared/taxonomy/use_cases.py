"""Hunt use-case registry: classifier keywords, ITM links, discovery seeds.

Single source of truth for the heuristic classifier, the /usecases API,
and the social discovery catalog. Keywords are lowercase; multi-word
phrases match as substrings, short tokens with word boundaries (same
convention as shared.utils.entities).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UseCaseDef:
    id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    itm_ids: tuple[str, ...] = ()
    subreddits: tuple[str, ...] = ()
    x_accounts: tuple[str, ...] = ()
    # keywords generic enough to need >=2 distinct keyword hits when no ITM
    # technique corroborates (limits false positives on career-advice chatter)
    weak_keywords: tuple[str, ...] = field(default=())


USE_CASES: tuple[UseCaseDef, ...] = (
    UseCaseDef(
        id="overemployment",
        label="Overemployment",
        description="Undisclosed concurrent employment / secretly working multiple jobs",
        # IF038 carries the big curated alias set; keep only social-native
        # phrasing here that the ITM aliases miss.
        keywords=(
            "oe life",
            "j1 and j2",
            "j2 offer",
            "j3",
            "both jobs",
            "juggling two jobs",
            "two remote jobs",
            "2 remote jobs",
            "working 2 jobs",
            "double dipping",
            "mouse jiggler",
            "overlapping meetings",
        ),
        itm_ids=("IF038",),
        subreddits=("overemployed", "jobsearchhacks", "remotework", "antiwork"),
        x_accounts=("Overemployed_",),
        weak_keywords=("second job", "two jobs", "side hustle"),
    ),
    UseCaseDef(
        id="data-exfiltration",
        label="Data Exfiltration",
        description="Taking or leaking company data, files, or trade secrets",
        keywords=(
            "exfiltrat",
            "took files",
            "take files",
            "copied files",
            "downloaded customer list",
            "customer list",
            "uploaded to personal",
            "personal google drive",
            "personal dropbox",
            "personal cloud",
            "forwarded to personal email",
            "emailed to himself",
            "emailed to herself",
            "usb drive",
            "thumb drive",
            "trade secret",
            "stole data",
            "stolen data",
            "took the source code",
            "downloaded the database",
        ),
        itm_ids=("IF002", "IF001"),
        subreddits=("cybersecurity", "sysadmin"),
        x_accounts=(),
    ),
    UseCaseDef(
        id="credential-misuse",
        label="Credential Misuse",
        description="Sharing, borrowing, or abusing logins, badges, and privileged access",
        keywords=(
            "shared password",
            "shared his password",
            "shared her password",
            "shared credentials",
            "sharing credentials",
            "borrowed account",
            "borrowed login",
            "used his login",
            "used her login",
            "someone else's account",
            "badge sharing",
            "let me use their badge",
            "still had access",
            "never revoked",
            "old credentials still",
            "admin access abuse",
            "abused privileged access",
            "snooped",
        ),
        itm_ids=("ME021", "ME024", "ME027", "IF039"),
        subreddits=("sysadmin", "ITCareerQuestions"),
        x_accounts=(),
    ),
    UseCaseDef(
        id="shadow-it",
        label="Shadow IT",
        description="Unsanctioned apps, devices, or AI tools used for work",
        keywords=(
            "shadow it",
            "shadow ai",
            "unsanctioned",
            "unapproved app",
            "unapproved software",
            "unauthorized software",
            "personal chatgpt",
            "pasted into chatgpt",
            "put it in chatgpt",
            "company data into chatgpt",
            "rogue saas",
            "personal device for work",
            "personal laptop for work",
            "unapproved ai",
            "bypassed it approval",
        ),
        itm_ids=("ME030", "ME003"),
        subreddits=("shadowIT", "msp", "sysadmin"),
        x_accounts=(),
    ),
)

_BY_ID = {uc.id: uc for uc in USE_CASES}


def use_case_ids() -> tuple[str, ...]:
    return tuple(uc.id for uc in USE_CASES)


def get_use_case(use_case_id: str) -> UseCaseDef | None:
    return _BY_ID.get((use_case_id or "").strip().lower())
