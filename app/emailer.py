# app/emailer.py
import os
from email.utils import parseaddr
import requests
from flask import current_app, render_template

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER")

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


def send_email(subject, recipients, text=None, html=None, sender=None) -> bool:
    """Raises on failure (bad API key, over quota, unverified sender, etc.)
    so callers can record/display the actual reason instead of a bare
    False - historically every failure landed in the DB with a blank error
    message because this used to swallow the exception here."""
    if isinstance(recipients, str):
        recipients = [recipients]

    # MAIL_DEFAULT_SENDER is "Display Name <email>" (RFC 5322), which the old
    # SendGrid SDK parsed itself - Brevo wants name/email as separate fields.
    sender_name, sender_email = parseaddr(sender or MAIL_DEFAULT_SENDER or "")
    sender_obj = {"email": sender_email}
    if sender_name:
        sender_obj["name"] = sender_name

    payload = {
        "sender": sender_obj,
        "to": [{"email": r} for r in recipients],
        "subject": subject,
        "htmlContent": html or f"<pre>{text or '(no content)'}</pre>",
    }
    if text:
        payload["textContent"] = text

    try:
        resp = requests.post(
            BREVO_SEND_URL,
            json=payload,
            headers={
                "accept": "application/json",
                "api-key": BREVO_API_KEY or "",
                "content-type": "application/json",
            },
            timeout=15,
        )
    except Exception as e:
        try:
            current_app.logger.exception("Brevo error: %s", e)
        except Exception:
            print("Brevo error:", e)
        raise

    if resp.status_code not in (200, 201):
        err = f"Brevo returned {resp.status_code}: {resp.text}"
        try:
            current_app.logger.error(err)
        except Exception:
            print(err)
        raise RuntimeError(err)

    return True

def send_template(subject, recipients, name, **ctx) -> bool:
    """
    Renders templates/emails/<name>.html and .txt if present.
    Usage: send_template("Subject", "to@example.com", "weekly", week=2, games=[...])
    """
    html = None
    text = None
    # Render if template exists; ignore if missing
    try:
        html = render_template(f"email/{name}.html", **ctx)
    except Exception:
        pass
    try:
        text = render_template(f"email/{name}.txt", **ctx)
    except Exception:
        pass
    return send_email(subject, recipients, text=text, html=html)
