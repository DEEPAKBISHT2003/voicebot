import os
import re
import json
import time
import asyncio
from typing import Dict, Any, List, Optional, Union
import uuid
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings
from services.copilot.src.services.usage_service import usage_service


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


# Patterns used for identifying questions or answers with personal experiential claims
EXPERIENTIAL_QUESTION_PATTERNS = [
    re.compile(r"\b(?:tell me about|describe|walk me through|give (?:me )?an example of)\b.*\b(?:project|time|experience|situation|role|challenge)\b", re.I),
    re.compile(r"\b(?:in|from|at)\s+(?:your|the)\s+(?:past|previous|last|current|prior)?\s*(?:job|company|role|team|project|work|experience|organization)\b", re.I),
    re.compile(r"\bhave you (?:ever |personally )?(?:built|worked with|used|managed|led|scaled|deployed|architected|maintained|debugged)\b", re.I),
    re.compile(r"\bwhat (?:is|was) your (?:role|experience|responsibility|contribution)\b", re.I),
    re.compile(r"\bon your resume\b", re.I),
    re.compile(r"\bin your experience\b", re.I),
    re.compile(r"\byour background\b", re.I),
]

EXPERIENTIAL_ANSWER_PATTERNS = [
    re.compile(r"\b(?:at|in|with)\s+(?:my|our)\s+(?:last|previous|current|past|prior)?\s*(?:job|company|role|team|project|workplace|startup|firm)\b", re.I),
    re.compile(r"\bwhen i (?:was at|worked at|joined|led|managed|built)\b", re.I),
    re.compile(r"\bin (?:my|our) (?:last|previous|current|past|production)\s+(?:project|system|codebase|environment|infrastructure|cluster)\b", re.I),
    re.compile(r"\b(?:we|i) (?:built|deployed|migrated|scaled|implemented|designed|shipped|managed|refactored)\b", re.I),
    re.compile(r"\bon my resume\b", re.I),
]


def is_resume_match_applicable(
    question: str,
    answer: str = "",
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Determines deterministically whether resume match is applicable to a Q&A pair.
    
    Applicability Rules:
    1. Explicit Metadata/Rubric:
       - If metadata contains 'resume_match_applicable' or 'is_resume_applicable', honors explicit bool.
       - If metadata contains rubric with 'resume_match_applicable', honors explicit bool.
       - If metadata defines 'question_type' or 'category':
         * Experiential/behavioral/resume/project -> True
         * Conceptual/algorithmic/theoretical -> False (unless answer claims personal experience)
    2. Question Intent:
       - If the question asks about the candidate's personal experience, past projects,
         roles, or resume claims -> True
    3. Answer Claims:
       - If the candidate makes explicit claims about past employment, specific projects,
         or production deployments from their career -> True
    4. Default:
       - Purely conceptual, theoretical, or algorithmic questions where no personal experience
         claims are made -> False (inapplicable).
    """
    # 1. Explicit metadata / rubric check
    if metadata and isinstance(metadata, dict):
        if "resume_match_applicable" in metadata:
            return bool(metadata["resume_match_applicable"])
        if "is_resume_applicable" in metadata:
            return bool(metadata["is_resume_applicable"])
        rubric = metadata.get("rubric")
        if isinstance(rubric, dict) and "resume_match_applicable" in rubric:
            return bool(rubric["resume_match_applicable"])
        
        q_type = str(metadata.get("question_type") or metadata.get("category") or "").strip().lower()
        if q_type in ("behavioral", "experience", "resume", "project", "work_history", "career", "experiential"):
            return True
        if q_type in ("conceptual", "algorithmic", "coding", "technical_theory", "knowledge", "architecture_concept", "theoretical"):
            if answer and any(p.search(answer) for p in EXPERIENTIAL_ANSWER_PATTERNS):
                return True
            return False

    q_text = str(question or "").strip()
    a_text = str(answer or "").strip()
    if not q_text:
        return False

    # 2. Question text intent
    if any(p.search(q_text) for p in EXPERIENTIAL_QUESTION_PATTERNS):
        return True

    # 3. Answer text experiential claims
    if a_text and any(p.search(a_text) for p in EXPERIENTIAL_ANSWER_PATTERNS):
        return True

    return False


def calculate_deterministic_accuracy_score(
    relevance: int,
    technical: int,
    resume_match: Optional[int] = None,
    completeness: int = 0,
    is_resume_applicable: Optional[bool] = None
) -> int:
    """
    Computes the deterministic weighted accuracy score using the calibrated formula:
    
    When resume match is applicable:
      accuracy_score = round(0.30 * relevance + 0.30 * technical + 0.25 * resume_match + 0.15 * completeness)
      
    When resume match is inapplicable:
      Renormalized weights excluding resume_match (0.30/0.75=0.40, 0.30/0.75=0.40, 0.15/0.75=0.20):
      accuracy_score = round(0.40 * relevance + 0.40 * technical + 0.20 * completeness)
      
    Invariants:
      - If question is completely unanswered (relevance=0, technical=0, completeness=0), score is strictly 0.
      - Clamped strictly to [0, 100].
      - Applicable weights sum strictly to 1.00.
    """
    rel = max(0, min(100, int(relevance)))
    tech = max(0, min(100, int(technical)))
    comp = max(0, min(100, int(completeness)))

    # Invariant: Unanswered questions receive 0 points (no free resume bonus)
    if rel == 0 and tech == 0 and comp == 0:
        return 0

    # Determine applicability:
    # If explicitly passed, honor it.
    # Otherwise, if resume_match is None -> inapplicable.
    # If resume_match is provided and is_resume_applicable is None -> applicable (for backward compatibility).
    applicable = is_resume_applicable
    if applicable is None:
        applicable = (resume_match is not None)

    if applicable:
        res = max(0, min(100, int(resume_match if resume_match is not None else 0)))
        score = round(0.30 * rel + 0.30 * tech + 0.25 * res + 0.15 * comp)
    else:
        # Renormalized: 0.30/0.75 = 0.40, 0.30/0.75 = 0.40, 0.15/0.75 = 0.20
        score = round(0.40 * rel + 0.40 * tech + 0.20 * comp)

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
  1. "relevance": How directly did the candidate answer the question asked? (0-100). Do not penalize concise delivery or valid alternative framing.
  2. "technical": Are the technical facts, architecture, logic, and terminology accurate? (0-100).
     * Proportional credit: Award credit proportional to correctness. Core concepts correct with a minor secondary omission deserve proportional credit (75-90), not a failing score.
     * Technical equivalence: Accept valid alternative designs, equivalent terminology, and sound trade-offs.
     * Rigor: Materially incorrect, unsafe, or false technical claims must continue to be penalized (0-40). Plausible-sounding errors must not receive credit.
  3. "resume_match": Consistency with candidate's resume background (0-100 or null).
     * Applicability: If the question or candidate's answer references personal experience, past projects, or claims from their resume, evaluate consistency with documented background (0-100). Penalize ONLY if the candidate makes specific experiential claims that directly contradict or materially exaggerate documented background.
     * Conceptual Neutrality: For conceptual, technical, or algorithmic questions where no personal experience claims are made, output null (not applicable). Do NOT output 100 to represent neutrality, as Python deterministically excludes inapplicable dimensions and renormalizes remaining weights.
  4. "completeness": Did they address the core depth of the question without excessive evasion? (0-100).
     * Do NOT judge completeness simply by answer length; concise answers that address the core question are complete.
     * If transcript text is ambiguous or cut off due to caption/ASR loss, evaluate fairly on available evidence without assuming candidate incompetence. If completely unanswered, score 0.
- Communication style rule: Do NOT penalize conversational filler ("basically", "uh"), pauses, accent, or speech-to-text artifacts.
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
- Provide up to 2 next question suggestions for the interviewer:
  - Suggestion 1 (JD + Resume based): Explores an important requirement from JD or project from Resume not yet covered.
  - Suggestion 2 (Conversation follow-up based): Probes deeper into what the candidate just claimed in their answer.
- Follow-Up Conversational Style Rules:
  * Sound like a real interviewer: Use natural, direct, spoken English appropriate for a live technical interview.
  * Ask ONE question at a time: Never combine multiple independent questions into one suggestion using "and", "also", or sequences of question marks. Each suggestion must have one primary question and clear conversational intent.
  * Short, direct, and conversational: Aim for approximately 8 to 20 words. Eliminate verbose preambles, artificial lead-ins, and explanations of what the interviewer wants to know.
  * Specific and grounded: Probe what the candidate just claimed, trade-offs, or unclear details; never invent claims they didn't make.
  * Avoid formulaic expressions: Do NOT use "Could you elaborate further on your approach?", "Could you explain in greater detail...", "Can you walk me through your overall approach and discuss...", or "What are the advantages and disadvantages of...".
  * Avoid unnecessary probing when the candidate's answer was already clear and addressed the point.
  * Avoid academic filler, formal clichés, or interrogation tone (e.g. avoid "Could you elaborate further on your approach?").
  * Examples:
    - Instead of: "Can you explain how you implemented caching, what eviction strategy you selected, and how you handled cache invalidation?"
      Use: "You mentioned Redis caching. How did you handle cache invalidation?"
    - Instead of: "Could you elaborate further on the architectural considerations and trade-offs that informed your decision to employ asynchronous processing?"
      Use: "What made you choose async processing for this part?"
    - Instead of: "Could you elaborate further on the architectural considerations that informed your decision to employ asynchronous processing?"
      Use: "Why did you choose async processing here?"
    - Instead of: "Could you provide further details regarding the challenges encountered during the implementation of your solution?"
      Use: "What was the main challenge during implementation?"
    - Instead of: "What are the advantages and disadvantages of microservices?"
      Use: "You mentioned splitting the service into microservices. What problem were you trying to solve with that?"
    - Instead of: "Why didn't you use a better architecture, and how would you justify this design?"
      Use: "What other approaches did you consider before settling on this design?"

────────────────────────────────────────────────────────────────
REQUIRED JSON OUTPUT SCHEMA:
Return ONLY valid JSON matching this exact structure:
{{
  "accuracy_evaluation": {{
    "relevance": 0,
    "technical": 0,
    "resume_match": null,
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
        timeout: float = 15.0,
        session_id: Optional[Union[str, uuid.UUID]] = None,
        call_identifier: Optional[str] = None
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

        try:
            t0 = time.monotonic()
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
            duration_ms = int((time.monotonic() - t0) * 1000)

            if session_id and hasattr(chat_completion, "usage") and chat_completion.usage:
                try:
                    await usage_service.record_llm_usage(
                        session_id=session_id,
                        stage="unified_qa",
                        model=self.model,
                        usage=chat_completion.usage,
                        duration_ms=duration_ms,
                        call_identifier=call_identifier,
                    )
                except Exception as usage_err:
                    logger.warning(f"[UnifiedQAWorker] Failed to record usage telemetry for session {session_id}: {usage_err}")

            raw_content = chat_completion.choices[0].message.content
            duration = duration_ms / 1000.0
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
            raw_resume = acc_eval.get("resume_match")
            completeness = acc_eval.get("completeness", 0)

            resume_match: Optional[int] = None
            if raw_resume is not None and str(raw_resume).strip().lower() not in ("null", "none", "n/a"):
                try:
                    resume_match = max(0, min(100, int(raw_resume)))
                except (ValueError, TypeError):
                    resume_match = None

            is_applicable = is_resume_match_applicable(
                question=question,
                answer=answer,
                metadata=current_interview_state
            )
            if resume_match is None:
                is_applicable = False

            accuracy_score = calculate_deterministic_accuracy_score(
                relevance=relevance,
                technical=technical,
                resume_match=resume_match,
                completeness=completeness,
                is_resume_applicable=is_applicable
            )
            parsed["accuracy_score"] = accuracy_score

            # Validate suggestions list count
            suggs = parsed.get("suggestions", [])
            if not isinstance(suggs, list):
                suggs = []
            valid_suggs = [str(s).strip() for s in suggs if str(s).strip()]
            if len(valid_suggs) > 2:
                valid_suggs = valid_suggs[:2]
            elif len(valid_suggs) < 1:
                valid_suggs = ["How did you handle that in your implementation?"]
            elif len(valid_suggs) == 1:
                valid_suggs.append("What other approaches did you consider before settling on this design?")
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
