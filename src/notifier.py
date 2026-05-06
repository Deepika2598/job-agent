"""Notify the user about new matching jobs.

Channels:
- email: Gmail SMTP using app password
- telegram: Bot API
"""
import os
import smtplib
import logging
import mimetypes
import requests
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


def _build_html(jobs_with_artifacts: list[dict]) -> str:
    rows = []
    for item in jobs_with_artifacts:
        j = item["job"]
        score = j.get("score", 0)
        hm = item.get("hiring_manager") or {}
        hm_line = ""
        if hm.get("email"):
            conf = hm.get("confidence", "low")
            hm_line = f"<br><i>Hiring contact (confidence: {conf}): {hm.get('name','')} &lt;{hm['email']}&gt;</i>"
        rows.append(f"""
        <tr>
          <td><b><a href="{j.get('url','#')}">{j.get('title','')}</a></b><br>
              <span style="color:#555">{j.get('company','')} — {j.get('location','')}</span>
              {hm_line}
          </td>
          <td style="text-align:right"><b>{score}</b></td>
        </tr>
        """)
    return f"""
    <html><body style="font-family:Calibri,Arial,sans-serif">
    <h3>{len(jobs_with_artifacts)} new data engineering match(es)</h3>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
      <thead><tr style="background:#f0f0f0">
        <th align="left">Role</th><th align="right">Score</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p style="color:#777;font-size:12px">
      Tailored resumes and cold email drafts are attached. Review before sending.
    </p>
    </body></html>
    """


def notify_email(subject: str, jobs_with_artifacts: list[dict]):
    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("NOTIFY_EMAIL", user)
    if not user or not pwd:
        log.warning("Gmail credentials missing; skipping email")
        return

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject

    msg.set_content("HTML version required to view this email properly.")
    msg.add_alternative(_build_html(jobs_with_artifacts), subtype="html")

    # Attach tailored resumes + email drafts (cap at ~10 attachments to keep email small)
    attached = 0
    for item in jobs_with_artifacts[:5]:
        for path_key in ("resume_path", "email_path"):
            p = item.get(path_key)
            if p and Path(p).exists() and attached < 10:
                ctype, _ = mimetypes.guess_type(str(p))
                maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
                with open(p, "rb") as f:
                    msg.add_attachment(
                        f.read(), maintype=maintype, subtype=subtype,
                        filename=Path(p).name
                    )
                attached += 1

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd)
            s.send_message(msg)
        log.info(f"Sent email notification to {to} with {attached} attachments")
    except Exception as e:
        log.error(f"Email send failed: {e}")


def notify_telegram(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(f"Telegram HTTP {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Telegram failed: {e}")


def notify(jobs_with_artifacts: list[dict], config: dict):
    if not jobs_with_artifacts:
        return
    channel = config.get("notify", {}).get("channel", "email")
    n = len(jobs_with_artifacts)
    subject = f"[Job Agent] {n} new match(es)"
    if channel in ("email", "both"):
        notify_email(subject, jobs_with_artifacts)
    if channel in ("telegram", "both"):
        lines = [f"<b>{n} new data engineering match(es)</b>"]
        for item in jobs_with_artifacts[:10]:
            j = item["job"]
            lines.append(
                f"• <b>{j.get('title','')}</b> @ {j.get('company','')} "
                f"(score: {j.get('score',0)})\n  {j.get('url','')}"
            )
        notify_telegram("\n".join(lines))
