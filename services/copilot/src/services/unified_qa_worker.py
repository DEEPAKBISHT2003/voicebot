import os
import json
import time
import asyncio
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings


def clean_json_loads(text: str) -> dict:
    """Safely parse JSON responses that may be wrapped in markdown codeblocks."""
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    return json.loads(clean_text)


# Observability Registry for Phase 4A Shadow Telemetry
unified_qa_shadow_metrics: Dict[str, Any] = {
    "unified_qa_shadow_runs_total": 0,
    "unified_qa_shadow_exact_accuracy_matches": 0,
    "unified_qa_shadow_high_accuracy_matches": 0,  # Delta <= 10
    "unified_qa_shadow_high_skill_matches": 0,     # Jaccard >= 0.70
    "unified_qa_shadow_errors_total": 0,
    "unified_qa_shadow_avg_accuracy_delta": 0.0,
    "total_accuracy_delta_accumulated": 0.0
}


def get_unified_qa_shadow_metrics() -> Dict[str, Any]:
    """Returns a snapshot of the Phase 4A shadow execution telemetry."""
    return dict(unified_qa_shadow_metrics)


def reset_unified_qa_shadow_metrics() -> None:
    """Resets Phase 4A shadow metrics counters (useful for unit tests)."""
    unified_qa_shadow_metrics["unified_qa_shadow_runs_total"] = 0
    unified_qa_shadow_metrics["unified_qa_shadow_exact_accuracy_matches"] = 0
    unified_qa_shadow_metrics["unified_qa_shadow_high_accuracy_matches"] = 0
    unified_qa_shadow_metrics["unified_qa_shadow_high_skill_matches"] = 0
    unified_qa_shadow_metrics["unified_qa_shadow_errors_total"] = 0
    unified_qa_shadow_metrics["unified_qa_shadow_avg_accuracy_delta"] = 0.0
    unified_qa_shadow_metrics["total_accuracy_delta_accumulated"] = 0.0


# Observability Registry for Phase 4B Production Telemetry
unified_qa_production_metrics: Dict[str, Any] = {
    "unified_qa_production_runs_total": 0,
    "unified_qa_production_success_total": 0,
    "unified_qa_production_fallback_total": 0,
    "unified_qa_total_latency_seconds": 0.0,
    "unified_qa_avg_latency_seconds": 0.0,
    "unified_qa_tokens_used_estimated": 0
}


def get_unified_qa_production_metrics() -> Dict[str, Any]:
    """Returns a snapshot of the Phase 4B production execution telemetry."""
    return dict(unified_qa_production_metrics)


def reset_unified_qa_production_metrics() -> None:
    """Resets Phase 4B production metrics counters (useful for unit tests)."""
    unified_qa_production_metrics["unified_qa_production_runs_total"] = 0
    unified_qa_production_metrics["unified_qa_production_success_total"] = 0
    unified_qa_production_metrics["unified_qa_production_fallback_total"] = 0
    unified_qa_production_metrics["unified_qa_total_latency_seconds"] = 0.0
    unified_qa_production_metrics["unified_qa_avg_latency_seconds"] = 0.0
    unified_qa_production_metrics["unified_qa_tokens_used_estimated"] = 0


def calculate_deterministic_accuracy_score(
    relevance: int,
    technical: int,
    resume_match: int,
    completeness: int
) -> int:
    """
    Computes the deterministic weighted accuracy score using the production formula:
    accuracy_score = round(0.30 * relevance + 0.30 * technical + 0.25 * resume_match + 0.15 * completeness)
    Clamped strictly to [0, 100].
    """
    rel = max(0, min(100, int(relevance)))
    tech = max(0, min(100, int(technical)))
    res = max(0, min(100, int(resume_match)))
    comp = max(0, min(100, int(completeness)))

    score = round(0.30 * rel + 0.30 * tech + 0.25 * res + 0.15 * comp)
    return max(0, min(100, score))


def compute_jaccard_similarity(list_a: List[str], list_b: List[str]) -> float:
    """Computes Jaccard similarity index between two string lists (case-insensitive)."""
    set_a = {str(x).strip().lower() for x in list_a if str(x).strip()}
    set_b = {str(x).strip().lower() for x in list_b if str(x).strip()}
    if not set_a and not set_b:
        return 1.0
    intersection = set_a.intersection(set_b)
    union = set_a.union(set_b)
    return len(intersection) / len(union) if union else 1.0


def compute_text_similarity(text_a: str, text_b: str) -> float:
    """Computes token-level Jaccard similarity between two strings."""
    tokens_a = {w.lower().strip(".,?!;:-_\"'") for w in text_a.split() if len(w) > 2}
    tokens_b = {w.lower().strip(".,?!;:-_\"'") for w in text_b.split() if len(w) > 2}
    if not tokens_a and not tokens_b:
        return 1.0
    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    return round(len(intersection) / len(union), 3) if union else 1.0


def compute_parity_metrics(
    legacy_data: Dict[str, Any],
    unified_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Computes the 6 required parity telemetry metrics comparing legacy outputs with UnifiedQAWorker outputs:
    1. Accuracy Score Delta
    2. Skill Coverage Similarity
    3. Remaining Skills Similarity
    4. Follow-Up Similarity
    5. Topic Detection Similarity
    6. Recommendation Similarity
    """
    # 1. Accuracy Score Delta
    legacy_acc = legacy_data.get("accuracy_score")
    unified_acc = unified_data.get("accuracy_score")
    if legacy_acc is not None and unified_acc is not None:
        accuracy_delta = abs(int(legacy_acc) - int(unified_acc))
    else:
        accuracy_delta = None

    # 2. Skill Coverage Similarity
    legacy_covered = legacy_data.get("covered_skills") or []
    unified_covered = unified_data.get("intelligence", {}).get("covered_skills") or []
    skill_coverage_similarity = round(compute_jaccard_similarity(legacy_covered, unified_covered), 3)

    # 3. Remaining Skills Similarity
    legacy_remaining = legacy_data.get("remaining_skills") or []
    unified_remaining = unified_data.get("intelligence", {}).get("remaining_skills") or []
    remaining_skills_similarity = round(compute_jaccard_similarity(legacy_remaining, unified_remaining), 3)

    # 4. Follow-Up Similarity
    legacy_suggs = legacy_data.get("dynamic_suggestions") or []
    unified_suggs = unified_data.get("suggestions") or []
    legacy_suggs_text = " ".join(legacy_suggs)
    unified_suggs_text = " ".join(unified_suggs)
    follow_up_similarity = compute_text_similarity(legacy_suggs_text, unified_suggs_text)

    # 5. Topic Detection Similarity
    legacy_topic = (legacy_data.get("current_topic") or "").strip().lower()
    unified_topic = (unified_data.get("intelligence", {}).get("current_topic") or "").strip().lower()
    if not legacy_topic and not unified_topic:
        topic_similarity = 1.0
    elif legacy_topic == unified_topic:
        topic_similarity = 1.0
    elif legacy_topic in unified_topic or unified_topic in legacy_topic:
        topic_similarity = 0.8
    else:
        topic_similarity = compute_text_similarity(legacy_topic, unified_topic)

    # 6. Recommendation Similarity
    legacy_rec = (legacy_data.get("recommended_next_topic") or "").strip()
    unified_rec = (unified_data.get("assistance", {}).get("recommended_next_topic") or "").strip()
    recommendation_similarity = compute_text_similarity(legacy_rec, unified_rec)

    # Update global observability metrics
    unified_qa_shadow_metrics["unified_qa_shadow_runs_total"] += 1
    if accuracy_delta is not None:
        if accuracy_delta == 0:
            unified_qa_shadow_metrics["unified_qa_shadow_exact_accuracy_matches"] += 1
        if accuracy_delta <= 10:
            unified_qa_shadow_metrics["unified_qa_shadow_high_accuracy_matches"] += 1
        unified_qa_shadow_metrics["total_accuracy_delta_accumulated"] += accuracy_delta
        runs = unified_qa_shadow_metrics["unified_qa_shadow_runs_total"]
        unified_qa_shadow_metrics["unified_qa_shadow_avg_accuracy_delta"] = round(
            unified_qa_shadow_metrics["total_accuracy_delta_accumulated"] / runs, 2
        )

    if skill_coverage_similarity >= 0.70:
        unified_qa_shadow_metrics["unified_qa_shadow_high_skill_matches"] += 1

    return {
        "accuracy_score_delta": accuracy_delta,
        "skill_coverage_similarity": skill_coverage_similarity,
        "remaining_skills_similarity": remaining_skills_similarity,
        "follow_up_similarity": follow_up_similarity,
        "topic_detection_similarity": topic_similarity,
        "recommendation_similarity": recommendation_similarity,
        "legacy_accuracy_score": legacy_acc,
        "unified_accuracy_score": unified_acc,
        "legacy_current_topic": legacy_topic,
        "unified_current_topic": unified_topic,
        "legacy_suggestions_count": len(legacy_suggs),
        "unified_suggestions_count": len(unified_suggs)
    }


class UnifiedQAWorker:
    """
    Phase 4A: Unified post-QA worker executing in parallel Shadow Mode.
    
    Consolidates:
      1. Technical Answer Accuracy Evaluation (Section A: Question + Answer + Resume only)
      2. Conversation Intelligence (Section B: JD + Resume + Conversation)
      3. Real-Time Assistance Guidance (Section C: Next topic, Notes, Understanding)
      4. Dynamic Follow-Up Suggestions (Section D: Exactly 2 suggestions)
    into a single structured LLM call.
    
    CRITICAL: In Phase 4A, this worker runs in SHADOW MODE ONLY.
    Its results are stored strictly for parity analysis and never surfaced to users or APIs.
    """

    def __init__(
        self,
        api_key: str = Settings.DEEPSEEK_API_KEY,
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL,
        client: Optional[AsyncOpenAI] = None
    ):
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def build_prompt(
        self,
        question: str,
        answer: str,
        resume: str,
        jd: str,
        current_interview_state: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Builds the unified prompt with strict architectural separation between
        Accuracy Evaluation (No JD) and Intelligence/Follow-Ups (With JD).
        """
        state = current_interview_state or {}
        covered_so_far = state.get("covered_skills", [])
        current_topic = state.get("current_topic", "")

        return f"""You are an expert AI interview system conducting simultaneous post-Q/A analysis.
An interviewer has just asked a technical question and the candidate provided an answer.

────────────────────────────────────────────────────────────────
INPUT CONTEXT:

[INTERVIEWER QUESTION]
{question.strip()}

[CANDIDATE ANSWER]
{answer.strip()}

[CANDIDATE RESUME]
{resume.strip() if resume else "None provided"}

[TARGET JOB DESCRIPTION]
{jd.strip() if jd else "N/A"}

[CURRENT INTERVIEW STATE]
- Current Active Topic: {current_topic or "General / Introduction"}
- Previously Covered Skills: {covered_so_far if covered_so_far else "None"}

────────────────────────────────────────────────────────────────
SECTION 1: TECHNICAL ACCURACY EVALUATION (STRICT ISOLATION RULE)
CRITICAL INSTRUCTION:
- You must evaluate the candidate's answer against the [INTERVIEWER QUESTION] and [CANDIDATE RESUME] ONLY.
- Do NOT use or assume any [TARGET JOB DESCRIPTION] for this section!
- Rate the following 4 dimensions from 0 to 100:
  1. "relevance": How directly did the candidate answer the question asked? (0-100)
  2. "technical": Are the technical facts, architecture, logic, and terminology accurate? (0-100)
  3. "resume_match": Is the claimed experience consistent with their resume background? (0-100)
  4. "completeness": Did they address the core depth of the question without excessive evasion? (0-100)
- Do NOT calculate an aggregate accuracy score here. Python will compute the weighted score.

────────────────────────────────────────────────────────────────
SECTION 2: CONVERSATION INTELLIGENCE
- Analyze the entire conversation context (Question, Answer, Resume, and Target Job Description).
- "current_topic": What technical topic is currently being discussed (e.g., "Database Indexing", "System Design", "Concurrency")?
- "covered_skills": Array of skills from JD or interview that the candidate has now demonstrated.
- "remaining_skills": Array of key skills from the JD not yet adequately explored.
- "resume_projects_covered": Array of resume projects discussed so far.
- "resume_projects_remaining": Key resume projects not yet explored.

────────────────────────────────────────────────────────────────
SECTION 3: REAL-TIME ASSISTANCE
- "recommended_next_topic": Forward guidance on where the interviewer should guide the conversation next.
- "interview_notes": 1-2 bullet observations about this specific answer.
- "current_candidate_understanding": 1 sentence holistic assessment of candidate technical capability so far.

────────────────────────────────────────────────────────────────
SECTION 4: DYNAMIC QUESTION SUGGESTIONS
- Provide EXACTLY 2 next question suggestions for the interviewer:
  - Suggestion 1 (JD + Resume based): Explores an important requirement from JD or project from Resume.
  - Suggestion 2 (Conversation follow-up based): Probes deeper into what the candidate just claimed in their answer.

────────────────────────────────────────────────────────────────
REQUIRED JSON OUTPUT SCHEMA:
Return ONLY valid JSON matching this exact structure:
{{
  "accuracy_evaluation": {{
    "relevance": 0,
    "technical": 0,
    "resume_match": 0,
    "completeness": 0
  }},
  "intelligence": {{
    "current_topic": "...",
    "covered_skills": ["..."],
    "remaining_skills": ["..."],
    "resume_projects_covered": ["..."],
    "resume_projects_remaining": ["..."]
  }},
  "assistance": {{
    "recommended_next_topic": "...",
    "interview_notes": ["..."],
    "current_candidate_understanding": "..."
  }},
  "suggestions": [
    "<Suggestion 1: JD+Resume>",
    "<Suggestion 2: Follow-up to answer>"
  ]
}}
"""

    async def process_completed_qa(
        self,
        question: str,
        answer: str,
        resume: str = "",
        jd: str = "",
        current_interview_state: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0
    ) -> Optional[Dict[str, Any]]:
        """
        Executes the unified post-QA inference call and deterministically computes the accuracy score.
        Returns the parsed unified dictionary or None on failure.
        """
        if not question or not question.strip() or not answer or not answer.strip():
            logger.warning("[UnifiedQAWorker] Cannot process completed QA with empty question or answer.")
            return None

        unified_qa_production_metrics["unified_qa_production_runs_total"] += 1

        prompt = self.build_prompt(
            question=question,
            answer=answer,
            resume=resume,
            jd=jd,
            current_interview_state=current_interview_state
        )

        start_time = time.time()
        try:
            chat_completion = await asyncio.wait_for(
                self.client.chat.completions.create(
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    model=self.model,
                    temperature=0.2,
                    response_format={"type": "json_object"}
                ),
                timeout=timeout
            )
            raw_content = chat_completion.choices[0].message.content
            duration = time.time() - start_time
            parsed = clean_json_loads(raw_content)

            # Validate top-level schema keys
            required_sections = ["accuracy_evaluation", "intelligence", "assistance", "suggestions"]
            if not all(k in parsed for k in required_sections):
                logger.error(f"[UnifiedQAWorker] Missing required sections in LLM response: {list(parsed.keys())}")
                unified_qa_shadow_metrics["unified_qa_shadow_errors_total"] += 1
                return None

            # Calculate deterministic accuracy score in Python
            acc_eval = parsed.get("accuracy_evaluation", {})
            relevance = acc_eval.get("relevance", 0)
            technical = acc_eval.get("technical", 0)
            resume_match = acc_eval.get("resume_match", 0)
            completeness = acc_eval.get("completeness", 0)

            accuracy_score = calculate_deterministic_accuracy_score(
                relevance=relevance,
                technical=technical,
                resume_match=resume_match,
                completeness=completeness
            )
            parsed["accuracy_score"] = accuracy_score

            # Validate suggestions list count
            suggs = parsed.get("suggestions", [])
            if not isinstance(suggs, list):
                suggs = []
            valid_suggs = [str(s).strip() for s in suggs if str(s).strip()]
            if len(valid_suggs) > 2:
                valid_suggs = valid_suggs[:2]
            elif len(valid_suggs) < 2:
                while len(valid_suggs) < 2:
                    valid_suggs.append("Can you elaborate on your technical implementation trade-offs?")
            parsed["suggestions"] = valid_suggs

            # Attach performance telemetry
            parsed["duration_seconds"] = round(duration, 2)
            est_tokens = (len(prompt) + len(raw_content)) // 4
            parsed["estimated_tokens"] = est_tokens

            # Update production telemetry counters
            unified_qa_production_metrics["unified_qa_total_latency_seconds"] += duration
            unified_qa_production_metrics["unified_qa_tokens_used_estimated"] += est_tokens
            runs = max(unified_qa_production_metrics["unified_qa_production_runs_total"], 1)
            unified_qa_production_metrics["unified_qa_avg_latency_seconds"] = round(
                unified_qa_production_metrics["unified_qa_total_latency_seconds"] / runs, 2
            )

            logger.info(
                f"[UnifiedQAWorker] QA processing complete in {duration:.2f}s (~{est_tokens} tokens): "
                f"accuracy_score={accuracy_score}%, "
                f"topic='{parsed.get('intelligence', {}).get('current_topic')}', "
                f"suggestions={len(parsed['suggestions'])}"
            )
            return parsed

        except asyncio.TimeoutError:
            logger.error(f"[UnifiedQAWorker] Timeout ({timeout}s) exceeded during shadow QA processing.")
            unified_qa_shadow_metrics["unified_qa_shadow_errors_total"] += 1
            return None
        except Exception as e:
            logger.error(f"[UnifiedQAWorker] Error during shadow QA processing: {e}")
            unified_qa_shadow_metrics["unified_qa_shadow_errors_total"] += 1
            return None
