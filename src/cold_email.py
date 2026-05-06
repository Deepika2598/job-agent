"""Cold email drafting + best-effort hiring manager email lookup.

Uses Gemini for the email body. For hiring manager email:
  1. Try Hunter.io domain search if HUNTER_API_KEY is set (25/mo free tier)
  2. Fall back to common pattern guessing using the company domain

The goal: produce a draft + best-effort recipient suggestion for the user
to review before sending. NEVER auto-send.
"""
import os
import re
import json
import logging
import requests
import google.generativeai as genai

log = logging.getLogger(__name__)

MODEL = "gemini-2.0-flash"

EMAIL_PROMPT = """Write a cold email from a senior data engineer to a hiring manager.

Tone: {tone}. Length: 100-130 words. NO em-dashes. NO buzzwords like "synergize",
"leverage", "passionate". Write like a real person.

Structure:
1. One-line opener that references something specific from the JD (a tool, a project area,
   or a team mission). Don't be generic.
2. Two short sentences connecting candidate's actual experience to that thing.
3. One sentence acknowledging humility (don't oversell).
4. Close with this exact CTA: "{cta}"

CANDIDATE SUMMARY:
{candidate_summary}

JOB:
{job_title} at {company}
{job_description}

Output JSON: {{"subject": "...", "body": "..."}}
The subject line must be specific (mentions the role title) and 6-10 words. No emojis.
"""


def _company_domain(company: str, url: str) -> str | None:
    """Try to derive a sane domain for the company. Best-effort."""
    if url:
        m = re.search(r"https?://([^/]+)", url)
        if m:
            host = m.group(1).lower()
            # strip job board hosts
            for boards in ["greenhouse.io", "lever.co", "ashbyhq.com",
                            "remoteok.com", "adzuna.com", "boards.greenhouse.io",
                            "jobs.lever.co", "jobs.ashbyhq.com"]:
                if boards in host:
                    return None
            # strip leading www.
            return host[4:] if host.startswith("www.") else host
    # fallback: lowercase company name + .com (works ~30% of the time)
    if company:
        slug = re.sub(r"[^a-z0-9]", "", company.lower())
        if slug:
            return f"{slug}.com"
    return None


def find_hiring_manager(company: str, job_url: str) -> dict | None:
    """Returns {'email': ..., 'name': ..., 'confidence': 'high|medium|low'} or None."""
    domain = _company_domain(company, job_url)
    if not domain:
        return None

    api_key = os.getenv("HUNTER_API_KEY")
    if api_key:
        try:
            r = requests.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain, "api_key": api_key,
                    "department": "engineering", "limit": 5
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                for e in data.get("emails", []):
                    title = (e.get("position") or "").lower()
                    if any(k in title for k in [
                        "data", "engineering", "platform", "analytics",
                        "engineer", "manager", "director", "vp", "head"
                    ]):
                        return {
                            "email": e.get("value"),
                            "name": f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                            "title": e.get("position", ""),
                            "confidence": "high" if e.get("confidence", 0) >= 70 else "medium",
                        }
            else:
                log.warning(f"Hunter HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"Hunter lookup failed: {e}")

    # Pattern fallback — low confidence; user must verify
    return {
        "email": f"recruiting@{domain}",
        "name": "Hiring Team",
        "title": "Recruiting",
        "confidence": "low",
    }


def draft_email(candidate_summary: str, job: dict, config: dict) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY not set, returning template email")
        return {
            "subject": f"Interest in {job.get('title','your role')}",
            "body": "[GEMINI_API_KEY not configured]",
        }

    genai.configure(api_key=api_key)
    cold_cfg = config.get("cold_email", {})

    prompt = EMAIL_PROMPT.format(
        tone=cold_cfg.get("tone", "direct"),
        cta=cold_cfg.get("cta", "Would you be open to a brief call this week?"),
        candidate_summary=candidate_summary,
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        job_description=(job.get("description") or "")[:4000],
    )

    model = genai.GenerativeModel(
        MODEL,
        generation_config={"response_mime_type": "application/json", "temperature": 0.6},
    )
    try:
        resp = model.generate_content(prompt)
        return json.loads(resp.text)
    except Exception as e:
        log.error(f"Email draft failed: {e}")
        return {
            "subject": f"Interest in {job.get('title','your role')} at {job.get('company','')}",
            "body": f"[email generation failed: {e}]",
        }
