"""Send a built digest (HTML + text) over SMTP with credentials from the env.

Companion to scripts/traffic_digest.py, run by .github/workflows/traffic-digest.yml.
Everything that identifies the operator — recipient, sender, SMTP login — comes
from environment variables that the workflow maps from repository secrets
(TRAFFIC_DIGEST_*). Nothing in this file, the workflow, or the job log names
the address: the repo is public, so the only sanctioned output lines are the
counts in ``describe()``. tests/test_traffic_digest.py pins that.

Env:
  MAIL_TO            recipient (required; comma-separated for several)
  SMTP_USER          login (required)
  SMTP_PASSWORD      app password (required; Gmail needs an App Password)
  SMTP_HOST          default smtp.gmail.com
  SMTP_PORT          default 465 (implicit TLS); 587 switches to STARTTLS
  MAIL_FROM          default SMTP_USER
  MAIL_SUBJECT       subject line (or --subject-file)

Stdlib only — the bare Actions runner has no third-party packages.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

REQUIRED = ("MAIL_TO", "SMTP_USER", "SMTP_PASSWORD")
# env name → the repository secret the workflow maps it from (for the error line).
SECRET_FOR = {
    "MAIL_TO": "TRAFFIC_DIGEST_TO",
    "SMTP_USER": "TRAFFIC_DIGEST_SMTP_USER",
    "SMTP_PASSWORD": "TRAFFIC_DIGEST_SMTP_PASSWORD",
}


def missing_env(env: dict[str, str]) -> list[str]:
    """Names (never values) of the required settings that are unset or blank."""
    return [k for k in REQUIRED if not (env.get(k) or "").strip()]


def build_message(
    *,
    to: str,
    sender: str,
    subject: str,
    text: str,
    html: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="insider-intel.digest")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for name, payload, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(
            payload, maintype=maintype, subtype=subtype or "octet-stream", filename=name
        )
    return msg


def describe(msg: EmailMessage) -> str:
    """Counts-only description for the public job log. No addresses, ever."""
    n_to = len([a for a in (msg["To"] or "").split(",") if a.strip()])
    n_att = sum(1 for part in msg.iter_attachments())
    return (
        f"email built: {n_to} recipient(s), {n_att} attachment(s), "
        f"subject {len(msg['Subject'] or '')} chars"
    )


def send(msg: EmailMessage, *, host: str, port: int, user: str, password: str) -> None:
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(user, password)
        s.send_message(msg)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Email a built digest over SMTP (env-configured).")
    ap.add_argument("--html", required=True, help="HTML body file")
    ap.add_argument("--text", required=True, help="plain-text body file (Markdown is fine)")
    ap.add_argument("--subject", help="subject line (overrides MAIL_SUBJECT)")
    ap.add_argument("--subject-file", help="file holding the subject line")
    ap.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="PATH[:MIME]",
        help="attach a file (repeatable), e.g. digest.md:text/markdown",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    env = dict(os.environ)
    gaps = missing_env(env)
    if gaps:
        print(
            "email NOT sent — set these repository secrets: "
            + ", ".join(SECRET_FOR[k] for k in gaps),
            file=sys.stderr,
        )
        return 2
    subject = a.subject or (
        Path(a.subject_file).read_text(encoding="utf-8").strip() if a.subject_file else ""
    )
    subject = subject or env.get("MAIL_SUBJECT") or "insider-intel traffic digest"
    attachments = []
    for spec in a.attach:
        path, _, mime = spec.partition(":")
        attachments.append(
            (Path(path).name, Path(path).read_bytes(), mime or "application/octet-stream")
        )
    msg = build_message(
        to=env["MAIL_TO"].strip(),
        sender=(env.get("MAIL_FROM") or env["SMTP_USER"]).strip(),
        subject=subject,
        text=Path(a.text).read_text(encoding="utf-8"),
        html=Path(a.html).read_text(encoding="utf-8"),
        attachments=attachments,
    )
    print(describe(msg))
    send(
        msg,
        host=(env.get("SMTP_HOST") or "smtp.gmail.com").strip(),
        port=int((env.get("SMTP_PORT") or "465").strip()),
        user=env["SMTP_USER"].strip(),
        password=env["SMTP_PASSWORD"],
    )
    print("email sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
