import os
import json
import time
import asyncio
from typing import List, Dict, Any, Optional, Set
from openai import AsyncOpenAI
from loguru import logger
from services.copilot.src.core.config import Settings
from services.copilot.src.services.precompiler import CompactProfile


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

    try:
        return json.loads(clean_text)
    except (json.JSONDecodeError, ValueError) as err:
        open_b = clean_text.count("{")
        close_b = clean_text.count("}")
        if open_b > close_b and clean_text.startswith("{"):
            try:
                return json.loads(clean_text + ("}" * (open_b - close_b)))
            except Exception:
                pass
        raise err


def compute_jaccard_similarity(list_a: List[str], list_b: List[str]) -> float:
    """Computes Jaccard similarity index between two string lists (case-insensitive)."""
    set_a = {str(x).strip().lower() for x in list_a if str(x).strip()}
    set_b = {str(x).strip().lower() for x in list_b if str(x).strip()}
    if not set_a and not set_b:
        return 1.0
    intersection = set_a.intersection(set_b)
    union = set_a.union(set_b)
    return round(len(intersection) / len(union), 3) if union else 1.0


def compute_text_similarity(text_a: str, text_b: str) -> float:
    """Computes token-level Jaccard similarity between two strings."""
    tokens_a = {w.lower().strip(".,?!;:-_\"'") for w in text_a.split() if len(w) > 2}
    tokens_b = {w.lower().strip(".,?!;:-_\"'") for w in text_b.split() if len(w) > 2}
    if not tokens_a and not tokens_b:
        return 1.0
    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    return round(len(intersection) / len(union), 3) if union else 1.0


def is_conversational_noise(text: str) -> bool:
    """
    Identifies pure conversational filler, greetings, audio checks, and administrative logistics
    that should be excluded from technical evidence.
    """
    clean = text.strip().lower()
    words = clean.split()
    if len(words) == 0:
        return True

    noise_patterns = [
        "can you hear me", "hear me loud and clear", "loud and clear",
        "i can hear you", "share my screen", "see my screen",
        "good morning", "good afternoon", "thank you", "thanks", "bye",
        "goodbye", "have a good day", "have a great day",
        "sounds good", "let's move on", "hr will reach out", "hr will be in touch"
    ]
    if any(p in clean for p in noise_patterns):
        return True

    noise_phrases = {
        "ok", "okay", "yes", "yeah", "yep", "sure", "got it", "right", "uh-huh",
        "hello", "hi", "test", "testing", "great", "cool", "perfect"
    }
    if clean in noise_phrases or (len(words) <= 2 and words[0] in noise_phrases):
        return True

    return False


def extract_closing_candidate_dialogue(
    transcript: List[Dict[str, Any]],
    confirmed_qa_pairs: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """
    Extracts substantive dialogue occurring after the last technical QA pair.
    Filters out greetings, audio checks, screen-sharing banter, and administrative logistics.
    """
    if not transcript:
        return []

    # Find the maximum turn ID involved in confirmed Q&A pairs
    max_qa_turn_id = -1
    for qa in confirmed_qa_pairs:
        qid = qa.get("question_turn_id")
        if isinstance(qid, int) and qid > max_qa_turn_id:
            max_qa_turn_id = qid
        for aid in qa.get("answer_turn_ids", []):
            if isinstance(aid, int) and aid > max_qa_turn_id:
                max_qa_turn_id = aid

    closing_dialogue: List[Dict[str, str]] = []
    for turn in transcript:
        tid = turn.get("turn_id") if turn.get("turn_id") is not None else turn.get("id")
        # Consider turns strictly after the last confirmed technical answer
        if isinstance(tid, int) and tid > max_qa_turn_id:
            text = (turn.get("text") or "").strip()
            speaker = turn.get("speaker") or "Speaker"
            if text and not is_conversational_noise(text):
                closing_dialogue.append({
                    "speaker": speaker,
                    "text": text
                })

    return closing_dialogue


def build_structured_dossier(
    compact_profile: Optional[Any],
    confirmed_qa_pairs: List[Dict[str, Any]],
    intelligence: Dict[str, Any],
    assistance: Dict[str, Any],
    transcript: List[Dict[str, Any]],
    jd: str = "",
    resume: str = "",
    custom_prompt: str = ""
) -> str:
    """
    Phase 5: Assembles the concise, high-signal Structured Evidence Dossier.
    Consolidates CompactProfile, confirmed Q&A records (with locked accuracy scores),
    session intelligence, assistance state, and trailing candidate inquiries.
    Filters out raw conversational filler, audio checks, and administrative chatter.
    """
    sections = []

    # 1. Candidate & Target Role Summary (from CompactProfile or raw text fallback)
    sections.append("=== SECTION 1: CANDIDATE & ROLE CONTEXT ===")
    if compact_profile:
        if isinstance(compact_profile, CompactProfile):
            cp_data = compact_profile.to_dict()
        elif isinstance(compact_profile, dict):
            cp_data = compact_profile
        else:
            cp_data = getattr(compact_profile, "__dict__", {})

        target_role = cp_data.get("target_role") or "Candidate"
        cand_name = cp_data.get("candidate_name") or "Candidate"
        skills = ", ".join(cp_data.get("core_skills", [])) or "Not Specified"
        projects = "; ".join(cp_data.get("project_claims", [])) or "None listed"

        sections.append(f"Candidate: {cand_name}")
        sections.append(f"Target Role: {target_role}")
        sections.append(f"Core Requirements & Skills: {skills}")
        sections.append(f"Documented Project Claims: {projects}")
    else:
        # Fallback if precompiler profile not available
        sections.append(f"Target Role / JD Summary: {jd[:400].strip()}...")
        sections.append(f"Candidate Resume Summary: {resume[:400].strip()}...")

    sections.append("")

    # 2. Confirmed Technical Q&A Pairs (Primary Evidence with Locked Accuracy Scores)
    sections.append("=== SECTION 2: CONFIRMED TECHNICAL Q&A PAIRS (PRIMARY EVIDENCE) ===")
    if confirmed_qa_pairs:
        for idx, qa in enumerate(confirmed_qa_pairs, 1):
            pair_id = qa.get("pair_id") or qa.get("qa_id") or f"QA_{idx}"
            question = (qa.get("question") or "").strip()
            answer = (qa.get("answer") or "").strip()
            score = qa.get("accuracy_score")
            score_str = f"{score}%" if score is not None else "Not Evaluated"

            sections.append(f"[Pair {pair_id}]")
            sections.append(f"  Question: {question}")
            sections.append(f"  Candidate Answer: {answer}")
            sections.append(f"  Phase 4B Evaluated Accuracy Score: {score_str}")
            sections.append("")
    else:
        sections.append("No confirmed Q&A pairs recorded.")
        sections.append("")

    # 3. Accumulated Conversation Intelligence & Real-Time Tracking State
    sections.append("=== SECTION 3: ACCUMULATED CONVERSATION INTELLIGENCE ===")
    covered_skills = intelligence.get("covered_skills", []) if isinstance(intelligence, dict) else []
    remaining_skills = intelligence.get("remaining_skills", []) if isinstance(intelligence, dict) else []
    verified_projects = intelligence.get("resume_projects_covered", []) if isinstance(intelligence, dict) else []
    timeline = intelligence.get("conversation_timeline", []) if isinstance(intelligence, dict) else []

    sections.append(f"Covered Skills in Interview: {', '.join(covered_skills) if covered_skills else 'None'}")
    sections.append(f"Remaining / Unassessed Skills: {', '.join(remaining_skills) if remaining_skills else 'None'}")
    sections.append(f"Verified Resume Projects: {', '.join(verified_projects) if verified_projects else 'None'}")

    if timeline:
        topics = [t.get("topic") for t in timeline if isinstance(t, dict) and t.get("topic")]
        sections.append(f"Topic Progression: {' -> '.join(topics)}")

    # 4. Live Assistance Observations & Candidate Understanding
    sections.append("")
    sections.append("=== SECTION 4: INTERVIEWER NOTES & CANDIDATE UNDERSTANDING ===")
    notes = assistance.get("interview_notes", []) if isinstance(assistance, dict) else []
    understanding = assistance.get("current_candidate_understanding", "") if isinstance(assistance, dict) else ""

    if notes:
        sections.append("Interviewer Observations Across Questions:")
        for note in notes[:5]:
            sections.append(f"  • {note}")
    if understanding:
        sections.append(f"Candidate Technical Understanding Assessment: {understanding}")

    # 5. Candidate Inquiries & Closing Dialogue (Dialogue after final technical QA)
    closing_turns = extract_closing_candidate_dialogue(transcript, confirmed_qa_pairs)
    sections.append("")
    sections.append("=== SECTION 5: CANDIDATE CLOSING INQUIRIES & QUESTIONS ===")
    if closing_turns:
        for t in closing_turns:
            sections.append(f"[{t['speaker']}]: {t['text']}")
    else:
        sections.append("No candidate closing inquiries recorded.")

    if custom_prompt:
        sections.append("")
        sections.append(f"=== SECTION 6: CUSTOM RECRUITER DIRECTIVES ===\n{custom_prompt}")

    return "\n".join(sections)


# Telemetry Registry for Phase 5A Shadow Execution
final_eval_shadow_metrics: Dict[str, Any] = {
    "final_eval_shadow_runs_total": 0,
    "final_eval_shadow_success_total": 0,
    "final_eval_shadow_errors_total": 0,
    "final_eval_total_score_delta": 0.0,
    "final_eval_avg_score_delta": 0.0,
    "final_eval_avg_token_reduction_pct": 0.0,
    "final_eval_avg_latency_reduction_pct": 0.0,
    "final_eval_avg_report_similarity": 0.0
}


def get_final_eval_shadow_metrics() -> Dict[str, Any]:
    """Returns a snapshot of Phase 5A shadow telemetry."""
    return dict(final_eval_shadow_metrics)


def reset_final_eval_shadow_metrics() -> None:
    """Resets Phase 5A shadow metrics counters."""
    final_eval_shadow_metrics["final_eval_shadow_runs_total"] = 0
    final_eval_shadow_metrics["final_eval_shadow_success_total"] = 0
    final_eval_shadow_metrics["final_eval_shadow_errors_total"] = 0
    final_eval_shadow_metrics["final_eval_total_score_delta"] = 0.0
    final_eval_shadow_metrics["final_eval_avg_score_delta"] = 0.0
    final_eval_shadow_metrics["final_eval_avg_token_reduction_pct"] = 0.0
    final_eval_shadow_metrics["final_eval_avg_latency_reduction_pct"] = 0.0
# Telemetry Registry for Phase 5B Production Execution
optimized_final_eval_production_metrics: Dict[str, Any] = {
    "optimized_eval_success_total": 0,
    "optimized_eval_failure_total": 0,
    "optimized_eval_fallback_total": 0,
    "optimized_eval_latency_ms": 0.0,
    "optimized_eval_avg_latency_ms": 0.0,
    "optimized_eval_token_usage": 0,
    "legacy_eval_latency_ms": 0.0,
    "legacy_eval_token_usage": 0,
    "report_similarity_score": 1.0,
    "score_delta": 0
}


def get_optimized_final_eval_production_metrics() -> Dict[str, Any]:
    """Returns a snapshot of Phase 5B production telemetry."""
    return dict(optimized_final_eval_production_metrics)


def reset_optimized_final_eval_production_metrics() -> None:
    """Resets Phase 5B production metrics counters."""
    optimized_final_eval_production_metrics["optimized_eval_success_total"] = 0
    optimized_final_eval_production_metrics["optimized_eval_failure_total"] = 0
    optimized_final_eval_production_metrics["optimized_eval_fallback_total"] = 0
    optimized_final_eval_production_metrics["optimized_eval_latency_ms"] = 0.0
    optimized_final_eval_production_metrics["optimized_eval_avg_latency_ms"] = 0.0
    optimized_final_eval_production_metrics["optimized_eval_token_usage"] = 0
    optimized_final_eval_production_metrics["legacy_eval_latency_ms"] = 0.0
    optimized_final_eval_production_metrics["legacy_eval_token_usage"] = 0
    optimized_final_eval_production_metrics["report_similarity_score"] = 1.0
    optimized_final_eval_production_metrics["score_delta"] = 0


class FinalEvaluationComparator:
    """
    Phase 5A & 5B: Final Evaluation Optimizer & Shadow Mode Comparator.
    Executes alongside the existing FinalEvaluationService, consuming the
    Structured Evidence Dossier instead of the raw unparsed transcript.
    Maintains zero impact on production contracts, scores, or dashboards.
    """

    SUPPORTED_DIMENSIONS = [
        "technical_depth",
        "practical_experience",
        "problem_solving",
        "communication_clarity"
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Optional[AsyncOpenAI] = None
    ):
        self.api_key = api_key or Settings.DEEPSEEK_API_KEY
        self.model = model or Settings.DEEPSEEK_MODEL
        self.base_url = base_url or Settings.DEEPSEEK_BASE_URL
        self.client = client or AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def build_prompts(self, dossier_text: str) -> tuple[str, str]:
        """Builds concise, schema-enforced prompts for the structured dossier."""
        system_prompt = (
            "You are an authoritative Senior Technical Evaluation Architect. "
            "Synthesize the structured interview evidence dossier and generate a rigorous final evaluation dossier.\n\n"
            "CRITICAL OPERATIONAL RULES:\n"
            "1. Ground every claim directly in the provided Structured Evidence Dossier.\n"
            "2. DO NOT invent candidate projects or skills not present in the evidence.\n"
            "3. DO NOT recalculate or modify Phase 4B Accuracy Scores; they are authoritative.\n"
            "4. Distinguish between 'not demonstrated' and 'not assessed'. If unasked, it is 'not assessed'.\n"
            "5. Evaluate Holistic Competency across exactly 4 dimensions: "
            "'technical_depth', 'practical_experience', 'problem_solving', and 'communication_clarity'.\n"
            "6. Every dimension score must be an integer between 0 and 100 with an evidence-grounded summary.\n"
            "7. STRICT JSON: Return strictly valid RFC 8259 JSON ending with '}'."
        )

        user_prompt = f"""Structured Interview Evidence Dossier:
{dossier_text}

Output a single JSON object with EXACTLY this top-level schema:
{{
  "holistic_competency": {{
    "dimensions": {{
      "technical_depth": {{ "score": <0-100 integer>, "summary": "<Concise assessment>" }},
      "practical_experience": {{ "score": <0-100 integer>, "summary": "<Concise assessment>" }},
      "problem_solving": {{ "score": <0-100 integer>, "summary": "<Concise assessment>" }},
      "communication_clarity": {{ "score": <0-100 integer>, "summary": "<Concise assessment>" }}
    }}
  }},
  "strengths": [
    "<Concise strength>",
    "<Second concise strength>"
  ],
  "development_areas": [
    "<Concise development area>",
    "<Second concise development area>"
  ],
  "jd_analysis": {{
    "covered_skills": [],
    "remaining_skills": [],
    "summary": "<Concise summary>"
  }},
  "resume_validation": {{
    "verified_projects": [],
    "unverified_projects": [],
    "summary": "<Concise summary>"
  }},
  "question_analysis": [
    {{
      "pair_id": "<Existing pair_id matching confirmed Q&A>",
      "observations": "<Concise observation on response>"
    }}
  ],
  "conversation_summary": "<Executive 2-3 sentence overview>",
  "observer_notes": [
    "<Key objective observation>"
  ]
}}
"""
        return system_prompt, user_prompt

    def _validate_and_sanitize_response(
        self,
        data: Dict[str, Any],
        confirmed_qa_pairs: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Validates schema, recalculates holistic score deterministically,
        and enforces Phase 4B accuracy_score immutability.
        """
        if not isinstance(data, dict):
            return None

        holistic_raw = data.get("holistic_competency")
        if not isinstance(holistic_raw, dict):
            return None

        dims_raw = holistic_raw.get("dimensions")
        if not isinstance(dims_raw, dict):
            return None

        sanitized_dimensions: Dict[str, Dict[str, Any]] = {}
        dimension_scores: List[int] = []

        for dim_key in self.SUPPORTED_DIMENSIONS:
            dim_data = dims_raw.get(dim_key)
            if not isinstance(dim_data, dict):
                return None
            try:
                score_int = int(dim_data.get("score", 0))
            except (ValueError, TypeError):
                return None
            score_int = max(0, min(100, score_int))
            summary_str = str(dim_data.get("summary") or "").strip()
            sanitized_dimensions[dim_key] = {
                "score": score_int,
                "summary": summary_str
            }
            dimension_scores.append(score_int)

        # Deterministic arithmetic mean for holistic competency score
        holistic_score = round(sum(dimension_scores) / len(dimension_scores)) if dimension_scores else 0
        holistic_score = max(0, min(100, holistic_score))

        # Strengths & Development Areas
        strengths = data.get("strengths") or []
        if not isinstance(strengths, list):
            strengths = [str(strengths)]
        strengths = [str(s).strip() for s in strengths if str(s).strip()]

        dev_areas = data.get("development_areas") or []
        if not isinstance(dev_areas, list):
            dev_areas = [str(dev_areas)]
        dev_areas = [str(d).strip() for d in dev_areas if str(d).strip()]

        # JD Analysis
        jd_raw = data.get("jd_analysis") or {}
        covered_skills = [str(s).strip() for s in jd_raw.get("covered_skills", []) if str(s).strip()] if isinstance(jd_raw.get("covered_skills"), list) else []
        remaining_skills = [str(s).strip() for s in jd_raw.get("remaining_skills", []) if str(s).strip()] if isinstance(jd_raw.get("remaining_skills"), list) else []
        jd_summary = str(jd_raw.get("summary") or "").strip()

        # Resume Validation
        resume_raw = data.get("resume_validation") or {}
        verified_projects = [str(p).strip() for p in resume_raw.get("verified_projects", []) if str(p).strip()] if isinstance(resume_raw.get("verified_projects"), list) else []
        unverified_projects = [str(p).strip() for p in resume_raw.get("unverified_projects", []) if str(p).strip()] if isinstance(resume_raw.get("unverified_projects"), list) else []
        resume_summary = str(resume_raw.get("summary") or "").strip()

        # Question Analysis: Strictly preserve Phase 4B accuracy_score
        llm_q_analysis = data.get("question_analysis") or []
        llm_obs_map: Dict[str, str] = {}
        if isinstance(llm_q_analysis, list):
            for item in llm_q_analysis:
                if isinstance(item, dict):
                    pid = str(item.get("pair_id") or item.get("qa_id") or "")
                    if pid:
                        llm_obs_map[pid] = str(item.get("observations") or "").strip()

        sanitized_qa_analysis: List[Dict[str, Any]] = []
        for idx, qa in enumerate(confirmed_qa_pairs):
            pid = str(qa.get("pair_id") or qa.get("qa_id") or f"QA_{idx + 1}")
            entry = {
                "pair_id": pid,
                "qa_id": pid,
                "question": qa.get("question", ""),
                "answer": qa.get("answer", ""),
                # STRICT INVARIANT: Locked accuracy_score from confirmed Q&A
                "accuracy_score": qa.get("accuracy_score"),
                "observations": llm_obs_map.get(pid, "")
            }
            sanitized_qa_analysis.append(entry)

        conv_summary = str(data.get("conversation_summary") or "").strip()
        obs_notes = data.get("observer_notes") or []
        if not isinstance(obs_notes, list):
            obs_notes = [str(obs_notes)]
        obs_notes = [str(n).strip() for n in obs_notes if str(n).strip()]

        return {
            "holistic_competency": {
                "score": holistic_score,
                "dimensions": sanitized_dimensions
            },
            "strengths": strengths,
            "development_areas": dev_areas,
            "jd_analysis": {
                "covered_skills": covered_skills,
                "remaining_skills": remaining_skills,
                "summary": jd_summary
            },
            "resume_validation": {
                "verified_projects": verified_projects,
                "unverified_projects": unverified_projects,
                "summary": resume_summary
            },
            "question_analysis": sanitized_qa_analysis,
            "conversation_summary": conv_summary,
            "observer_notes": obs_notes
        }

    async def evaluate_optimized_interview(
        self,
        compact_profile: Optional[Any],
        confirmed_qa_pairs: List[Dict[str, Any]],
        intelligence: Dict[str, Any],
        assistance: Dict[str, Any],
        transcript: List[Dict[str, Any]],
        jd: str = "",
        resume: str = "",
        custom_prompt: str = "",
        timeout: float = 25.0
    ) -> Optional[Dict[str, Any]]:
        """
        Executes the optimized final evaluation using the Structured Evidence Dossier.
        Returns a dict containing the validated report and performance telemetry metadata.
        """
        dossier = build_structured_dossier(
            compact_profile=compact_profile,
            confirmed_qa_pairs=confirmed_qa_pairs,
            intelligence=intelligence,
            assistance=assistance,
            transcript=transcript,
            jd=jd,
            resume=resume,
            custom_prompt=custom_prompt
        )

        system_prompt, user_prompt = self.build_prompts(dossier)
        start_time = time.time()

        try:
            chat_completion = await asyncio.wait_for(
                self.client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    model=self.model,
                    response_format={"type": "json_object"}
                ),
                timeout=timeout
            )
            duration_ms = round((time.time() - start_time) * 1000, 2)
            content = chat_completion.choices[0].message.content or "{}"
            raw_dict = clean_json_loads(content)

            validated = self._validate_and_sanitize_response(raw_dict, confirmed_qa_pairs)
            if not validated:
                logger.error("[Phase5A Shadow] Validation failed for optimized final evaluation.")
                return None

            est_tokens = (len(system_prompt) + len(user_prompt) + len(content)) // 4
            return {
                "report": validated,
                "meta": {
                    "duration_ms": duration_ms,
                    "estimated_tokens": est_tokens
                }
            }

        except Exception as e:
            logger.error(f"[Phase5A Shadow] Error executing optimized final evaluation: {e}")
            return None

    def compare_evaluations(
        self,
        legacy_report: Dict[str, Any],
        optimized_report: Dict[str, Any],
        legacy_meta: Dict[str, Any],
        optimized_meta: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Computes granular similarity, delta, and efficiency telemetry comparing
        the production legacy report with the shadow optimized report.
        """
        # 1. Holistic Competency Score Delta
        legacy_h = legacy_report.get("holistic_competency", {})
        legacy_score = legacy_h.get("score") if isinstance(legacy_h, dict) else None
        if legacy_score is None and isinstance(legacy_h, dict) and "dimensions" in legacy_h:
            dims = legacy_h.get("dimensions", {})
            dim_scores = [d.get("score", 0) for d in dims.values() if isinstance(d, dict) and "score" in d]
            legacy_score = round(sum(dim_scores) / len(dim_scores)) if dim_scores else 0
        legacy_score = int(legacy_score or 0)

        opt_h = optimized_report.get("holistic_competency", {})
        opt_score = opt_h.get("score") if isinstance(opt_h, dict) else None
        if opt_score is None and isinstance(opt_h, dict) and "dimensions" in opt_h:
            dims = opt_h.get("dimensions", {})
            dim_scores = [d.get("score", 0) for d in dims.values() if isinstance(d, dict) and "score" in d]
            opt_score = round(sum(dim_scores) / len(dim_scores)) if dim_scores else 0
        opt_score = int(opt_score or 0)

        score_delta = abs(legacy_score - opt_score)

        # 2. Strengths Similarity
        legacy_strengths = legacy_report.get("strengths") or []
        opt_strengths = optimized_report.get("strengths") or []
        strength_sim = compute_jaccard_similarity(legacy_strengths, opt_strengths)

        # 3. Development Areas Similarity
        legacy_dev = legacy_report.get("development_areas") or []
        opt_dev = optimized_report.get("development_areas") or []
        dev_sim = compute_jaccard_similarity(legacy_dev, opt_dev)

        # 4. Skill Coverage Similarity
        legacy_covered = legacy_report.get("jd_analysis", {}).get("covered_skills") or []
        opt_covered = optimized_report.get("jd_analysis", {}).get("covered_skills") or []
        skill_coverage_sim = compute_jaccard_similarity(legacy_covered, opt_covered)

        # 5. Resume Validation Similarity
        legacy_verified = legacy_report.get("resume_validation", {}).get("verified_projects") or []
        opt_verified = optimized_report.get("resume_validation", {}).get("verified_projects") or []
        resume_val_sim = compute_jaccard_similarity(legacy_verified, opt_verified)

        # 6. Conversation Summary Similarity
        legacy_summary = legacy_report.get("conversation_summary") or ""
        opt_summary = optimized_report.get("conversation_summary") or ""
        conv_summary_sim = compute_text_similarity(legacy_summary, opt_summary)

        # 7. Observer Notes Similarity
        legacy_notes = legacy_report.get("observer_notes") or []
        opt_notes = optimized_report.get("observer_notes") or []
        obs_notes_sim = compute_jaccard_similarity(legacy_notes, opt_notes)

        # Overall Report Similarity (Weighted multi-attribute blend)
        score_alignment = max(0.0, 1.0 - (score_delta / 100.0))
        overall_similarity = round(
            (0.20 * score_alignment) +
            (0.15 * strength_sim) +
            (0.15 * dev_sim) +
            (0.15 * skill_coverage_sim) +
            (0.15 * resume_val_sim) +
            (0.10 * conv_summary_sim) +
            (0.10 * obs_notes_sim),
            3
        )

        # Efficiency metrics
        leg_tokens = legacy_meta.get("estimated_tokens", 0)
        opt_tokens = optimized_meta.get("estimated_tokens", 0)
        token_reduction_pct = round(
            ((leg_tokens - opt_tokens) / max(leg_tokens, 1)) * 100.0, 1
        ) if leg_tokens > opt_tokens else 0.0

        leg_lat = legacy_meta.get("duration_ms", 0.0)
        opt_lat = optimized_meta.get("duration_ms", 0.0)
        lat_reduction_pct = round(
            ((leg_lat - opt_lat) / max(leg_lat, 1.0)) * 100.0, 1
        ) if leg_lat > opt_lat else 0.0

        # Success Threshold Gates
        thresholds_met = {
            "holistic_score_delta_met": score_delta <= 3,
            "strength_similarity_met": strength_sim >= 0.90,
            "development_area_similarity_met": dev_sim >= 0.90,
            "skill_coverage_similarity_met": skill_coverage_sim >= 0.95,
            "resume_validation_similarity_met": resume_val_sim >= 0.95,
            "conversation_summary_similarity_met": conv_summary_sim >= 0.90,
            "overall_report_similarity_met": overall_similarity >= 0.90
        }

        comparison = {
            "score_delta": score_delta,
            "legacy_holistic_score": legacy_score,
            "optimized_holistic_score": opt_score,
            "strength_similarity": strength_sim,
            "development_area_similarity": dev_sim,
            "skill_coverage_similarity": skill_coverage_sim,
            "resume_validation_similarity": resume_val_sim,
            "conversation_summary_similarity": conv_summary_sim,
            "observer_notes_similarity": obs_notes_sim,
            "report_similarity_score": overall_similarity,
            "legacy_tokens": leg_tokens,
            "optimized_tokens": opt_tokens,
            "token_reduction_pct": token_reduction_pct,
            "legacy_latency_ms": leg_lat,
            "optimized_latency_ms": opt_lat,
            "latency_reduction_pct": lat_reduction_pct,
            "thresholds_met": thresholds_met,
            "all_thresholds_met": all(thresholds_met.values()),
            "timestamp": time.time()
        }
        return comparison

    async def run_shadow_comparison(
        self,
        legacy_report: Dict[str, Any],
        legacy_meta: Dict[str, Any],
        compact_profile: Optional[Any],
        confirmed_qa_pairs: List[Dict[str, Any]],
        intelligence: Dict[str, Any],
        assistance: Dict[str, Any],
        transcript: List[Dict[str, Any]],
        jd: str = "",
        resume: str = "",
        custom_prompt: str = "",
        session_id: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Executes the shadow comparison pipeline safely:
        1. Runs the optimized evaluation on the structured dossier.
        2. Compares outputs with legacy production report.
        3. Updates shadow telemetry registry.
        4. Logs results without modifying production state.
        """
        final_eval_shadow_metrics["final_eval_shadow_runs_total"] += 1

        opt_result = await self.evaluate_optimized_interview(
            compact_profile=compact_profile,
            confirmed_qa_pairs=confirmed_qa_pairs,
            intelligence=intelligence,
            assistance=assistance,
            transcript=transcript,
            jd=jd,
            resume=resume,
            custom_prompt=custom_prompt
        )

        if not opt_result:
            final_eval_shadow_metrics["final_eval_shadow_errors_total"] += 1
            return None

        comparison = self.compare_evaluations(
            legacy_report=legacy_report,
            optimized_report=opt_result["report"],
            legacy_meta=legacy_meta,
            optimized_meta=opt_result["meta"]
        )
        comparison["session_id"] = session_id

        # Update running aggregate telemetry
        final_eval_shadow_metrics["final_eval_shadow_success_total"] += 1
        runs = final_eval_shadow_metrics["final_eval_shadow_success_total"]
        final_eval_shadow_metrics["final_eval_total_score_delta"] += comparison["score_delta"]
        final_eval_shadow_metrics["final_eval_avg_score_delta"] = round(
            final_eval_shadow_metrics["final_eval_total_score_delta"] / runs, 2
        )
        final_eval_shadow_metrics["final_eval_avg_token_reduction_pct"] = round(
            comparison["token_reduction_pct"], 1
        )
        final_eval_shadow_metrics["final_eval_avg_latency_reduction_pct"] = round(
            comparison["latency_reduction_pct"], 1
        )
        final_eval_shadow_metrics["final_eval_avg_report_similarity"] = round(
            comparison["report_similarity_score"], 3
        )

        logger.info(
            f"[Phase5A Shadow] Comparison complete for session {session_id}: "
            f"score_delta={comparison['score_delta']}, "
            f"report_similarity={comparison['report_similarity_score']}, "
            f"token_reduction={comparison['token_reduction_pct']}%, "
            f"latency_reduction={comparison['latency_reduction_pct']}%"
        )
        return comparison
