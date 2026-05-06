"""Filters jobs by keyword, sponsorship, clearance, and computes a match score."""
import logging

log = logging.getLogger(__name__)


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    text = text.lower()
    return any(k.lower() in text for k in keywords)


def _matches_locations(loc: str, locations: list[str]) -> bool:
    if not locations:
        return True
    loc = (loc or "").lower()
    if not loc:  # unknown — keep
        return True
    return any(l.lower() in loc or loc in l.lower() for l in locations)


def _has_excluded(text: str, phrases: list[str]) -> str | None:
    """Return the first excluded phrase found, else None."""
    text = text.lower()
    for p in phrases:
        if p.lower() in text:
            return p
    return None


def _score(job: dict, config: dict) -> int:
    """Heuristic score 0-100. LLM can refine later."""
    score = 50
    title = job.get("title", "").lower()
    desc = job.get("description", "").lower()
    company = job.get("company", "").lower()

    # title match boosts
    for kw in config["search"]["keywords"]:
        if kw.lower() in title:
            score += 15
            break

    # senior/staff title bumps
    if any(s in title for s in ["senior", "sr.", "staff", "lead", "principal"]):
        score += 5

    # positive sponsorship phrases
    for p in config["filters"].get("positive_phrases", []):
        if p.lower() in desc:
            score += 10
            break

    # priority companies
    for c in config["filters"].get("priority_companies", []):
        if c.lower() in company:
            score += 10
            break

    # tech stack overlap (boost if multiple known-good keywords appear in JD)
    tech_signals = ["pyspark", "airflow", "dbt", "snowflake", "databricks",
                    "delta lake", "iceberg", "kafka", "redshift", "terraform"]
    overlap = sum(1 for t in tech_signals if t in desc)
    score += min(15, overlap * 3)

    return min(100, max(0, score))


def filter_jobs(jobs: list[dict], config: dict) -> list[dict]:
    """Apply all filters, attach score, return surviving jobs."""
    filt = config["filters"]
    search = config["search"]
    blacklist = [b.lower() for b in filt.get("blacklist_companies", [])]

    out = []
    rejection_counts = {"keyword": 0, "location": 0, "exclude": 0,
                        "blacklist": 0, "score": 0}

    for j in jobs:
        title = j.get("title", "")
        desc = j.get("description", "")
        company = j.get("company", "").lower()
        location = j.get("location", "")

        if not _matches_keywords(title, search["keywords"]):
            rejection_counts["keyword"] += 1
            continue

        if not _matches_locations(location, search.get("locations", [])):
            rejection_counts["location"] += 1
            continue

        if any(b in company for b in blacklist):
            rejection_counts["blacklist"] += 1
            continue

        excluded = _has_excluded(title + " " + desc, filt.get("exclude_phrases", []))
        if excluded:
            rejection_counts["exclude"] += 1
            j["_rejected_for"] = excluded
            continue

        score = _score(j, config)
        if score < search.get("min_match_score", 60):
            rejection_counts["score"] += 1
            continue

        j["score"] = score
        out.append(j)

    log.info(
        f"Filter results: {len(out)} kept, rejections: {rejection_counts}"
    )
    # Sort by score desc
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out
