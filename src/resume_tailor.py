"""Resume tailoring using Google Gemini (free tier).

Strategy:
- Send the JD + base resume_data to Gemini
- Ask it to: (1) reorder bullets, (2) rewrite bullets to emphasize JD-relevant work,
  (3) reorder skills, (4) rewrite summary
- Strict instruction: do NOT invent experience or fabricate facts
- Returns a modified copy of the resume_data dict
"""
import os
import json
import copy
import logging
import google.generativeai as genai

log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"  # fast, free-tier friendly

SYSTEM_PROMPT = """You are an expert technical resume editor for data engineering roles.

You will receive (1) a base resume in JSON, and (2) a job description. Your task is to
produce a tailored resume in the SAME JSON SCHEMA, with these rules:

HARD RULES — violating these is a critical failure:
1. NEVER invent companies, titles, dates, technologies, or accomplishments not in the base resume.
2. NEVER change quantitative metrics (e.g., "30% reduction") — keep the original numbers.
3. Keep the same companies, titles, dates, and locations exactly as in the base resume.
4. Keep tech mentioned in each role's "tech" array; you may reorder.

SOFT RULES — apply these to maximize JD relevance:
1. Reorder bullets within each role so the most JD-relevant ones are first.
2. Trim bullets to at most {max_bullets} per role, dropping the weakest matches.
3. Rephrase bullet wording (without inventing) to use JD vocabulary where the substance
   already matches. Example: if base says "PySpark" and JD says "Spark", you may use either.
4. Reorder the skills categories so JD-relevant categories come first.
5. Rewrite the summary as 4-6 punchy bullets that mirror the JD's priorities, drawing only
   from facts in the base resume.
6. Tailoring mode: {mode}. conservative = minimal edits, only reordering. moderate = reorder
   + light rewording. aggressive = full rephrasing while staying truthful.

INLINE BOLD MARKERS — important formatting rule:
- Text wrapped in **double asterisks** in the input represents inline-bold keywords in the
  rendered resume. PRESERVE these markers in your output. You may also ADD new **bold**
  markers around 1-3 JD-critical keywords per bullet (technologies, methodologies, metrics).
  Do not over-bold — at most 2-4 emphasized phrases per bullet. Never bold full sentences.

OUTPUT: A single JSON object matching the input schema. No prose, no markdown, no code fences.
"""


def _configure():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    genai.configure(api_key=api_key)


def tailor_resume(resume_data: dict, job: dict, config: dict) -> dict:
    """Return a tailored copy of resume_data for this job."""
    _configure()

    mode = config["tailoring"].get("mode", "moderate")
    max_bullets = config["tailoring"].get("max_bullets_per_role", 7)

    sys = SYSTEM_PROMPT.format(max_bullets=max_bullets, mode=mode)

    user_payload = {
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "description": (job.get("description") or "")[:8000],  # cap input size
        },
        "base_resume": resume_data,
    }

    model = genai.GenerativeModel(
        model_name=MODEL,
        system_instruction=sys,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.4,
        },
    )

    try:
        resp = model.generate_content(json.dumps(user_payload))
        tailored = json.loads(resp.text)
        # Defensive: ensure the schema fields we need are present
        if "experience" not in tailored or "skills" not in tailored:
            log.warning("Tailored resume missing required fields, falling back to base")
            return copy.deepcopy(resume_data)
        # Defensive: never let it overwrite contact, name, education
        for protected in ("name", "title", "contact", "education", "certifications"):
            if protected in resume_data:
                tailored[protected] = resume_data[protected]
        return tailored
    except Exception as e:
        log.error(f"Gemini tailoring failed, falling back to base resume: {e}")
        return copy.deepcopy(resume_data)


def evaluate_match(resume_data: dict, job: dict) -> dict:
    """Optional: ask Gemini to score the match and explain why.
    Returns {score: int, reasons: [str], gaps: [str]}.
    """
    _configure()
    prompt = f"""You are evaluating a candidate-job fit for a data engineering role.

CANDIDATE RESUME (JSON):
{json.dumps(resume_data, indent=2)[:6000]}

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Description: {(job.get('description') or '')[:6000]}

Return JSON: {{"score": 0-100, "reasons": [3 short strings], "gaps": [up to 3 short strings]}}
Score reflects how well the candidate's actual experience matches the JD.
"""
    model = genai.GenerativeModel(
        MODEL,
        generation_config={"response_mime_type": "application/json", "temperature": 0.2},
    )
    try:
        resp = model.generate_content(prompt)
        return json.loads(resp.text)
    except Exception as e:
        log.warning(f"Evaluate match failed: {e}")
        return {"score": 0, "reasons": [], "gaps": []}
