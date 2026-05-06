"""Main orchestrator: fetch → filter → tailor → notify."""
import os
import json
import logging
import re
from pathlib import Path
from datetime import datetime
import yaml
from dotenv import load_dotenv

from . import database, job_fetcher, job_filter, resume_tailor, resume_generator, cold_email, notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("agent")

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "data" / "output"


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:60].strip("_")


def _load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _load_resume() -> dict:
    with open(ROOT / "resume_data.json") as f:
        return json.load(f)


def main():
    load_dotenv(ROOT / ".env")
    database.init_db()
    database.cleanup_old(days=60)

    config = _load_config()
    base_resume = _load_resume()

    # 1. Fetch
    raw_jobs = job_fetcher.fetch_all(config)

    # 2. Filter
    matches = job_filter.filter_jobs(raw_jobs, config)
    log.info(f"{len(matches)} jobs passed filtering")

    # 3. Dedupe
    new_matches = [j for j in matches if not database.is_seen(j["id"])]
    log.info(f"{len(new_matches)} new (after dedup)")
    if not new_matches:
        log.info("Nothing new this run.")
        return

    min_notify = config["notify"].get("min_notify_score", 70)

    # 4. Process each new match
    artifacts = []
    candidate_summary = " ".join(base_resume.get("summary", []))[:1500]

    run_dir = OUTPUT_DIR / datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    for job in new_matches:
        try:
            slug = _safe_filename(f"{job.get('company','x')}_{job.get('title','x')}")

            # tailor resume
            tailored = resume_tailor.tailor_resume(base_resume, job, config)
            resume_path = run_dir / f"resume_{slug}.docx"
            resume_generator.generate_docx(tailored, resume_path)

            # cold email + hiring manager lookup (if enabled)
            email_path = None
            hm = None
            if config.get("cold_email", {}).get("enabled"):
                drafted = cold_email.draft_email(candidate_summary, job, config)
                hm = cold_email.find_hiring_manager(
                    job.get("company", ""), job.get("url", "")
                )
                email_path = run_dir / f"email_{slug}.txt"
                lines = []
                if hm:
                    lines.append(f"To: {hm.get('email','')}  ({hm.get('name','')} — confidence: {hm.get('confidence','low')})")
                lines.append(f"Subject: {drafted.get('subject','')}")
                lines.append("")
                lines.append(drafted.get("body", ""))
                email_path.write_text("\n".join(lines), encoding="utf-8")

            artifacts.append({
                "job": job,
                "resume_path": str(resume_path),
                "email_path": str(email_path) if email_path else None,
                "hiring_manager": hm,
            })

            database.mark_seen(
                job, score=job.get("score", 0),
                resume_path=str(resume_path),
                cold_email_path=str(email_path) if email_path else None,
                hiring_manager_email=(hm or {}).get("email"),
            )
        except Exception as e:
            log.error(f"Processing job {job.get('id')} failed: {e}", exc_info=True)

    # 5. Notify (only the high-score ones)
    notifiable = [a for a in artifacts if a["job"].get("score", 0) >= min_notify]
    log.info(f"Notifying about {len(notifiable)} of {len(artifacts)} jobs")
    if notifiable:
        notifier.notify(notifiable, config)

    log.info("Run complete.")


if __name__ == "__main__":
    main()
