"""மின்னஞ்சல் அனுப்புதல் — Brevo HTTP API (port 443; Render இலவசத் திட்டத்தில் SMTP தடுக்கப்படும்).
Render environment: BREVO_API_KEY, MAIL_FROM (Brevo-ல் verify செய்த sender), MAIL_FROM_NAME"""
import base64
import json
import os
import re
import urllib.error
import urllib.request

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")


def valid_emails(value):
    """'a@x.com, b@y.com' -> சரியான முகவரிகள் (வரிசை மாறாமல், மீண்டும் வராமல்)."""
    out = []
    for e in str(value or "").replace(";", ",").replace("\n", ",").split(","):
        e = e.strip().lower()
        if e and _EMAIL_RE.match(e) and e not in out:
            out.append(e)
    return out


def mask_email(e):
    """abcdef@gmail.com -> ab****@gmail.com"""
    local, _, dom = str(e).partition("@")
    return (local[:2] + "*" * max(2, len(local) - 2)) + "@" + dom


def configured():
    return bool(os.environ.get("BREVO_API_KEY") and os.environ.get("MAIL_FROM"))


def send_email(to_list, subject, html_body, attachments=None):
    """attachments: [(filename, bytes)]. பிழை எனில் RuntimeError (தமிழ்ச் செய்தியுடன்)."""
    key, sender = os.environ.get("BREVO_API_KEY"), os.environ.get("MAIL_FROM")
    if not key or not sender:
        raise RuntimeError("மின்னஞ்சல் அமைப்பு (BREVO_API_KEY / MAIL_FROM) Render-ல் சேர்க்கப்படவில்லை.")
    payload = {"sender": {"email": sender, "name": os.environ.get("MAIL_FROM_NAME", "மாவட்ட நூலக அலுவலகம், திண்டுக்கல்")},
               "to": [{"email": e} for e in to_list], "subject": subject, "htmlContent": html_body}
    if attachments:
        payload["attachment"] = [{"name": n, "content": base64.b64encode(b).decode("ascii")} for n, b in attachments]
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode("utf-8"),
                                 headers={"api-key": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("Brevo பிழை (%s): %s" % (e.code, e.read().decode("utf-8", "replace")[:300])) from e
    except urllib.error.URLError as e:
        raise RuntimeError("Brevo-ஐ அடைய முடியவில்லை: %s" % e) from e
