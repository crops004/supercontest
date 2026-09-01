# app/emailer.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from flask import current_app, render_template

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER")

def send_email(subject, recipients, text=None, html=None, sender=None) -> bool:
    """Raises on failure (bad API key, over quota, unverified sender, etc.)
    so callers can record/display the actual reason instead of a bare
    False - historically every failure landed in the DB with a blank error
    message because this used to swallow the exception here."""
    if isinstance(recipients, str):
        recipients = [recipients]
    message = Mail(
        from_email=sender or MAIL_DEFAULT_SENDER,
        to_emails=recipients,
        subject=subject,
        plain_text_content=text or "(no text content)",
        html_content=html
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(message)
    except Exception as e:
        try:
            current_app.logger.exception("SendGrid error: %s", e)
        except Exception:
            print("SendGrid error:", e)
        raise

    if resp.status_code != 202:
        body = getattr(resp, "body", b"")
        body = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        err = f"SendGrid returned {resp.status_code}: {body}"
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
