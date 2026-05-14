"""Cold email drafting + cached hiring-manager lookup.

Improvements:
- Hunter.io results are cached per company domain (quota saver)
- Email body includes a clean signature block with the candidate's contact info
- Salary range extracted from JD body when possible
"""
import os
import re
import json
import logging
import requests
import google.generativeai as genai

from . import database

log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"

EMAIL_PROMPT = """Write a cold email from a senior data engineer to a hiring manager.

Tone: {tone}. Length: 100-130 words. NO em-dashes. NO buzzwords like "synergize",
"leverage", "passionate". Write like a real person.

Structure:
1. One-line opener that references something specific from the JD (a tool, a project
   area, or a team mission). Don't be generic.
2. Two short sentences connecting candidate's actual experience to that thing.
3. One sentence acknowledging humility (don't oversell).
4. Close with this exact CTA: "{cta}"

DO NOT include a signature or sign-off — the system appends one automatically.

CANDIDATE SUMMARY:
{candidate_summary}

JOB:
{job_title} at {company}
{job_description}

Output JSON: {{"subject": "...", "body": "..."}}
The subject line must be specific (mentions the role title) and 6-10 words. No emojis.
"""


def _company_domain(company: str, url: str) -> str | None:
    if url:
        m = re.search(r"https?://([^/]+)", url)
        if m:
            host = m.group(1).lower()
            for boards in [
                "greenhouse.io", "lever.co", "ashbyhq.com",
                "remoteok.com", "adzuna.com", "boards.greenhouse.io",
                "jobs.lever.co", "jobs.ashbyhq.com"
            ]:
                if boards in host:
                    return None
            return host[4:] if host.startswith("www.") else host
    if company:
        slug = re.sub(r"[^a-z0-9]", "", company.lower())
        if slug:
            return f"{slug}.com"
    return None


def find_hiring_manager(company: str, job_url: str,
                         priority_companies: list = None) -> dict | None:
    """Returns {'email', 'name', 'title', 'confidence'} or None.

    Uses a cache to avoid burning Hunter.io quota on companies we've already
    looked up. Skips Hunter entirely for priority_companies.
    """
    domain = _company_domain(company, job_url)
    if not domain:
        return None

    # Skip Hunter for priority companies (apply via portal anyway)
    if priority_companies:
        for c in priority_companies:
            if c.lower() in company.lower():
                log.info(f"Priority company {company}: skipping Hunter lookup")
                return {
                    "email": f"recruiting@{domain}",
                    "name": "Recruiting Team",
                    "title": "Recruiting",
                    "confidence": "low",
                }

    # Cache hit?
    cached = database.get_cached_hm(domain)
    if cached:
        log.info(f"Hunter cache hit for {domain}")
        return cached

    # Cache miss — try Hunter API
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
                        "engineer", "manager", "director", "vp", "head",
                        "recruiter", "talent"
                    ]):
                        hm = {
                            "email": e.get("value"),
                            "name": f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                            "title": e.get("position", ""),
                            "confidence": "high" if e.get("confidence", 0) >= 70 else "medium",
                        }
                        database.cache_hm(domain, hm)
                        return hm
                # Hunter returned but no relevant person — cache the fallback
                fallback = {
                    "email": f"recruiting@{domain}",
                    "name": "Recruiting Team",
                    "title": "Recruiting",
                    "confidence": "low",
                }
                database.cache_hm(domain, fallback)
                return fallback
            else:
                log.warning(f"Hunter HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"Hunter lookup failed: {e}")

    # Pattern fallback when Hunter is unavailable
    fallback = {
        "email": f"recruiting@{domain}",
        "name": "Recruiting Team",
        "title": "Recruiting",
        "confidence": "low",
    }
    database.cache_hm(domain, fallback)
    return fallback


# ---------- Salary extraction ----------

_SALARY_PATTERNS = [
    re.compile(
        r"\$\s*(\d{2,3}[,.]?\d{0,3})\s*[Kk]?\s*(?:[-–—]|to)\s*\$?\s*(\d{2,3}[,.]?\d{0,3})\s*[Kk]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"USD\s*(\d{2,3}[,.]?\d{0,3})\s*[Kk]?\s*(?:[-–—]|to)\s*(\d{2,3}[,.]?\d{0,3})\s*[Kk]?",
        re.IGNORECASE,
    ),
]


def extract_salary(text: str) -> str | None:
    """Best-effort salary extraction from JD body."""
    if not text:
        return None
    snippet = text[:8000]
    for pat in _SALARY_PATTERNS:
        m = pat.search(snippet)
        if m:
            low, high = m.group(1), m.group(2)
            def norm(s):
                s = s.replace(",", "").replace(".", "")
                n = int(s)
                if n < 1000:
                    return f"${n}K"
                return f"${n // 1000}K"
            try:
                return f"{norm(low)}–{norm(high)}"
            except (ValueError, ZeroDivisionError):
                return None
    return None


# ---------- Email drafting ----------

def _signature(resume_data: dict) -> str:
    name = resume_data.get("name", "")
    contact = resume_data.get("contact", {})
    bits = []
    if contact.get("phone"):
        bits.append(contact["phone"])
    if contact.get("email"):
        bits.append(contact["email"])
    contact_line = " | ".join(bits)

    lines = ["", "Best,", name]
    if contact_line:
        lines.append(contact_line)
    if contact.get("linkedin"):
        lines.append(f"LinkedIn: {contact['linkedin']}")
    if contact.get("github"):
        lines.append(f"GitHub: {contact['github']}")
    return "\n".join(lines)


def draft_email(candidate_summary: str, job: dict, config: dict,
                resume_data: dict = None) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.warning("GEMINI_API_KEY not set, returning template email")
        body = "[GEMINI_API_KEY not configured]"
        if resume_data:
            body += "\n" + _signature(resume_data)
        return {
            "subject": f"Interest in {job.get('title','your role')}",
            "body": body,
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
        result = json.loads(resp.text)
        if resume_data:
            result["body"] = (result.get("body", "") + "\n"
                              + _signature(resume_data))
        return result
    except Exception as e:
        log.error(f"Email draft failed: {e}")
        body = f"[email generation failed: {e}]"
        if resume_data:
            body += "\n" + _signature(resume_data)
        return {
            "subject": f"Interest in {job.get('title','your role')} at {job.get('company','')}",
            "body": body,
        }
