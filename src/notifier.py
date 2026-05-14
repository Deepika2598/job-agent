"""Email and Telegram notifier — adds salary column, smarter HTML."""
import os
import smtplib
import logging
import mimetypes
import requests
from collections import defaultdict
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


def _row_html(item: dict) -> str:
    j = item["job"]
    score = j.get("score", 0)
    salary = j.get("salary") or ""
    salary_cell = f"<br><span style='color:#0a7d3e'>💰 {salary}</span>" if salary else ""
    hm = item.get("hiring_manager") or {}
    hm_line = ""
    if hm.get("email"):
        conf = hm.get("confidence", "low")
        conf_color = {"high": "#0a7d3e", "medium": "#b8860b", "low": "#999"}.get(conf, "#999")
        hm_line = (f"<br><span style='color:{conf_color};font-size:12px'>"
                   f"Contact ({conf}): {hm.get('name','')} &lt;{hm['email']}&gt;</span>")
    return f"""
    <tr>
      <td style="padding:8px 6px;border-bottom:1px solid #eee">
        <b><a href="{j.get('url','#')}" style="color:#1a5fc4;text-decoration:none">
          {j.get('title','')}
        </a></b>
        <br><span style="color:#555">{j.get('company','')} — {j.get('location','')}</span>
        {salary_cell}
        {hm_line}
      </td>
      <td style="padding:8px 6px;border-bottom:1px solid #eee;text-align:right">
        <b style="font-size:18px">{score}</b>
      </td>
    </tr>
    """


def _build_html(jobs_with_artifacts: list) -> str:
    by_company = defaultdict(list)
    for item in jobs_with_artifacts:
        by_company[item["job"].get("company", "")].append(item)

    sections = []
    for company, items in sorted(by_company.items()):
        rows = "".join(_row_html(i) for i in items)
        sections.append(f"""
        <h4 style="margin:18px 0 6px 0;color:#222">{company} ({len(items)})</h4>
        <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%">
          {rows}
        </table>
        """)

    return f"""
    <html><body style="font-family:Calibri,Arial,sans-serif;color:#222">
    <h3 style="margin-bottom:6px">{len(jobs_with_artifacts)} new data engineering match(es)</h3>
    <p style="color:#666;margin-top:0;font-size:13px">
      Tailored resumes and cold email drafts are attached. Review before sending.
    </p>
    {''.join(sections)}
    </body></html>
    """


def notify_email(subject: str, jobs_with_artifacts: list):
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


def notify(jobs_with_artifacts: list, config: dict):
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


# ---------- Daily summary ----------

def send_daily_summary(jobs_today: list):
    user = os.getenv("GMAIL_USER")
    pwd = os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("NOTIFY_EMAIL", user)
    if not user or not pwd or not jobs_today:
        return

    n = len(jobs_today)
    top = jobs_today[:5]
    by_company = defaultdict(int)
    for j in jobs_today:
        by_company[j.get("company", "")] += 1
    top_companies = sorted(by_company.items(), key=lambda x: -x[1])[:5]

    top_rows = "".join(f"""
        <tr>
          <td style="padding:6px;border-bottom:1px solid #eee">
            <a href="{j.get('url','#')}" style="color:#1a5fc4">{j.get('title','')}</a>
            <br><span style="color:#555;font-size:12px">{j.get('company','')} — {j.get('location','')}</span>
            {f"<br><span style='color:#0a7d3e;font-size:12px'>💰 {j['salary']}</span>" if j.get('salary') else ""}
          </td>
          <td style="padding:6px;border-bottom:1px solid #eee;text-align:right"><b>{j.get('score','')}</b></td>
        </tr>
    """ for j in top)

    company_rows = "".join(f"""
        <tr><td style="padding:4px 8px">{c}</td><td style="padding:4px 8px;text-align:right">{n_jobs}</td></tr>
    """ for c, n_jobs in top_companies)

    html = f"""
    <html><body style="font-family:Calibri,Arial,sans-serif;color:#222">
      <h2 style="margin-bottom:4px">Job Agent — Daily Summary</h2>
      <p style="color:#666;margin-top:0">
        {n} matching role(s) surfaced in the last 24 hours
      </p>

      <h4>Top 5 by score</h4>
      <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%">
        {top_rows}
      </table>

      <h4>Most active companies</h4>
      <table cellpadding="0" cellspacing="0" style="border-collapse:collapse">
        {company_rows}
      </table>

      <p style="color:#888;font-size:12px;margin-top:24px">
        Detailed listings and tailored resumes were sent in their individual emails throughout the day.
      </p>
    </body></html>
    """

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = f"[Job Agent] Daily Summary — {n} match(es) today"
    msg.set_content("HTML version required.")
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd)
            s.send_message(msg)
        log.info(f"Sent daily summary to {to}")
    except Exception as e:
        log.error(f"Daily summary send failed: {e}")
