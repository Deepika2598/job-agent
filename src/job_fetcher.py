"""Fetches jobs from multiple free sources.

Sources: Greenhouse, Lever, Ashby, Workable, SmartRecruiters, RemoteOK, Adzuna.
"""
import os
import re
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterator
import requests

log = logging.getLogger(__name__)

USER_AGENT = "JobHunterAgent/1.0 (personal use)"
TIMEOUT = 20


def _id(source: str, key: str) -> str:
    return f"{source}:{hashlib.md5(key.encode()).hexdigest()[:16]}"


def _within_window(iso_str: str, max_age_hours: int) -> bool:
    if not iso_str:
        return True
    try:
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        return dt >= cutoff
    except Exception:
        return True


def fetch_greenhouse(boards: list, max_age_hours: int) -> Iterator[dict]:
    for board in boards:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"Greenhouse {board}: HTTP {r.status_code}")
                continue
            data = r.json()
            for j in data.get("jobs", []):
                posted = j.get("updated_at") or j.get("first_published")
                if not _within_window(posted, max_age_hours):
                    continue
                yield {
                    "id": _id("greenhouse", str(j["id"])),
                    "source": "greenhouse",
                    "company": board.replace("-", " ").title(),
                    "title": j.get("title", ""),
                    "location": (j.get("location") or {}).get("name", ""),
                    "url": j.get("absolute_url", ""),
                    "posted_at": posted or "",
                    "description": j.get("content", ""),
                }
        except Exception as e:
            log.warning(f"Greenhouse {board} failed: {e}")


def fetch_lever(boards: list, max_age_hours: int) -> Iterator[dict]:
    for board in boards:
        url = f"https://api.lever.co/v0/postings/{board}?mode=json"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"Lever {board}: HTTP {r.status_code}")
                continue
            for j in r.json():
                posted_ms = j.get("createdAt", 0)
                posted_iso = datetime.fromtimestamp(
                    posted_ms / 1000, tz=timezone.utc
                ).isoformat() if posted_ms else ""
                if not _within_window(posted_iso, max_age_hours):
                    continue
                yield {
                    "id": _id("lever", j.get("id", "")),
                    "source": "lever",
                    "company": board.replace("-", " ").title(),
                    "title": j.get("text", ""),
                    "location": (j.get("categories") or {}).get("location", ""),
                    "url": j.get("hostedUrl", ""),
                    "posted_at": posted_iso,
                    "description": j.get("descriptionPlain", "") + "\n" +
                                   "\n".join(li.get("text", "") for li in j.get("lists", [])),
                }
        except Exception as e:
            log.warning(f"Lever {board} failed: {e}")


def fetch_ashby(boards: list, max_age_hours: int) -> Iterator[dict]:
    for board in boards:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"Ashby {board}: HTTP {r.status_code}")
                continue
            data = r.json()
            for j in data.get("jobs", []):
                posted = j.get("publishedAt") or j.get("updatedAt")
                if not _within_window(posted, max_age_hours):
                    continue
                yield {
                    "id": _id("ashby", j.get("id", "")),
                    "source": "ashby",
                    "company": board,
                    "title": j.get("title", ""),
                    "location": j.get("location", ""),
                    "url": j.get("jobUrl", ""),
                    "posted_at": posted or "",
                    "description": j.get("descriptionPlain", "") or j.get("descriptionHtml", ""),
                }
        except Exception as e:
            log.warning(f"Ashby {board} failed: {e}")


def fetch_workable(boards: list, max_age_hours: int) -> Iterator[dict]:
    """Workable public API — https://apply.workable.com/api/v3/accounts/<slug>/jobs"""
    for board in boards:
        url = f"https://apply.workable.com/api/v3/accounts/{board}/jobs"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"Workable {board}: HTTP {r.status_code}")
                continue
            data = r.json()
            for j in data.get("results", []):
                posted = j.get("published_on") or j.get("created_at")
                if not _within_window(posted, max_age_hours):
                    continue
                loc = j.get("location", {}) or {}
                loc_str = ", ".join(filter(None, [
                    loc.get("city"), loc.get("region"), loc.get("country")
                ]))
                yield {
                    "id": _id("workable", str(j.get("id", j.get("shortcode", "")))),
                    "source": "workable",
                    "company": board.replace("-", " ").title(),
                    "title": j.get("title", ""),
                    "location": loc_str,
                    "url": j.get("url", "") or f"https://apply.workable.com/{board}/j/{j.get('shortcode','')}/",
                    "posted_at": posted or "",
                    "description": j.get("description", "") or "",
                }
        except Exception as e:
            log.warning(f"Workable {board} failed: {e}")


def fetch_smartrecruiters(boards: list, max_age_hours: int) -> Iterator[dict]:
    """SmartRecruiters public postings API."""
    for board in boards:
        url = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                log.warning(f"SmartRecruiters {board}: HTTP {r.status_code}")
                continue
            data = r.json()
            for j in data.get("content", []):
                posted = j.get("releasedDate") or j.get("createdOn")
                if not _within_window(posted, max_age_hours):
                    continue
                loc = j.get("location", {}) or {}
                loc_str = ", ".join(filter(None, [
                    loc.get("city"), loc.get("region"), loc.get("country")
                ]))
                yield {
                    "id": _id("smartrecruiters", str(j.get("id", ""))),
                    "source": "smartrecruiters",
                    "company": board,
                    "title": j.get("name", ""),
                    "location": loc_str,
                    "url": f"https://jobs.smartrecruiters.com/{board}/{j.get('id','')}",
                    "posted_at": posted or "",
                    "description": (j.get("jobAd", {}) or {}).get("sections", {}).get("jobDescription", {}).get("text", ""),
                }
        except Exception as e:
            log.warning(f"SmartRecruiters {board} failed: {e}")


def fetch_remoteok(keywords: list, max_age_hours: int) -> Iterator[dict]:
    url = "https://remoteok.com/api"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning(f"RemoteOK: HTTP {r.status_code}")
            return
        data = r.json()
        kws = [k.lower() for k in keywords]
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):
                continue
            title = j.get("position", "").lower()
            if not any(k in title for k in kws):
                continue
            posted = j.get("date", "")
            if not _within_window(posted, max_age_hours):
                continue
            yield {
                "id": _id("remoteok", str(j.get("id", j.get("slug", "")))),
                "source": "remoteok",
                "company": j.get("company", ""),
                "title": j.get("position", ""),
                "location": j.get("location", "Remote"),
                "url": j.get("url", j.get("apply_url", "")),
                "posted_at": posted,
                "description": j.get("description", ""),
            }
    except Exception as e:
        log.warning(f"RemoteOK failed: {e}")


def fetch_adzuna(keywords: list, country: str, max_age_hours: int) -> Iterator[dict]:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_API_KEY")
    if not app_id or not app_key:
        log.info("Adzuna keys not set, skipping")
        return
    max_days_old = max(1, max_age_hours // 24 + 1)
    what_or = " ".join(keywords[:3])
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": app_id, "app_key": app_key,
        "results_per_page": 50, "what_or": what_or,
        "max_days_old": max_days_old, "content-type": "application/json"
    }
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning(f"Adzuna: HTTP {r.status_code}: {r.text[:200]}")
            return
        for j in r.json().get("results", []):
            yield {
                "id": _id("adzuna", str(j.get("id", ""))),
                "source": "adzuna",
                "company": (j.get("company") or {}).get("display_name", ""),
                "title": j.get("title", ""),
                "location": (j.get("location") or {}).get("display_name", ""),
                "url": j.get("redirect_url", ""),
                "posted_at": j.get("created", ""),
                "description": j.get("description", ""),
            }
    except Exception as e:
        log.warning(f"Adzuna failed: {e}")


def fetch_all(config: dict) -> list:
    keywords = config["search"]["keywords"]
    max_age = config["search"]["max_age_hours"]
    sources = config["sources"]

    all_jobs = []
    if sources.get("greenhouse", {}).get("enabled"):
        all_jobs += list(fetch_greenhouse(sources["greenhouse"]["boards"], max_age))
    if sources.get("lever", {}).get("enabled"):
        all_jobs += list(fetch_lever(sources["lever"]["boards"], max_age))
    if sources.get("ashby", {}).get("enabled"):
        all_jobs += list(fetch_ashby(sources["ashby"]["boards"], max_age))
    if sources.get("workable", {}).get("enabled"):
        all_jobs += list(fetch_workable(sources["workable"]["boards"], max_age))
    if sources.get("smartrecruiters", {}).get("enabled"):
        all_jobs += list(fetch_smartrecruiters(sources["smartrecruiters"]["boards"], max_age))
    if sources.get("remoteok", {}).get("enabled"):
        all_jobs += list(fetch_remoteok(keywords, max_age))
    if sources.get("adzuna", {}).get("enabled"):
        all_jobs += list(fetch_adzuna(keywords, sources["adzuna"].get("country", "us"), max_age))

    for j in all_jobs:
        j["description"] = _clean_html(j.get("description", ""))

    log.info(f"Fetched {len(all_jobs)} jobs across all sources")
    return all_jobs


def _clean_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    for k, v in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')]:
        text = text.replace(k, v)
    return text.strip()
