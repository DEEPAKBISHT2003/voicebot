import asyncio
import json
from typing import List, Dict, Any, Optional
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


class FinalEvaluationService:
    """
    Phase 3B: Final Evaluation Synthesis Engine.
    Evaluates holistic candidate competency, JD alignment, resume project validation,
    strengths, and growth areas across the complete interview evidence.

    Authoritative Invariant:
    - Phase 2V per-question accuracy scores are locked and preserved without recalculation.
    - Holistic Competency is calculated deterministically from evaluated dimensions.
    - Overall score (70/30) is NOT calculated here; it belongs to Phase 3C/3D backend aggregation.
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

    def format_interview_evidence(
        self,
        transcript: List[Dict[str, Any]],
        confirmed_qa_pairs: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Builds complete, chronological, human-readable interview evidence
        from finalized logical transcript turns and confirmed Q&A pairs.

        Guarantees:
        - NEVER truncates to transcript[-20:].
        - Filters out empty messages and raw intermediate events.
        - Preserves chronological order, turn IDs, and speaker identities.
        """
        evidence_lines = []

        # 1. Confirmed Q&A Summary (Primary substantive evidence with Phase 2V accuracy scores)
        if confirmed_qa_pairs:
            evidence_lines.append("=== CONFIRMED INTERVIEW Q&A PAIRS (WITH PHASE 2V ACCURACY SCORES) ===")
            for idx, qa in enumerate(confirmed_qa_pairs, 1):
                pair_id = qa.get("pair_id") or qa.get("qa_id") or f"QA_{idx}"
                q_text = (qa.get("question") or "").strip()
                a_text = (qa.get("answer") or "").strip()
                acc_score = qa.get("accuracy_score")
                score_str = f"{acc_score}%" if acc_score is not None else "Not Evaluated"
                evidence_lines.append(f"[Pair {pair_id}]")
                evidence_lines.append(f"  Question: {q_text}")
                evidence_lines.append(f"  Answer: {a_text}")
                evidence_lines.append(f"  Phase 2V Accuracy Score: {score_str}")
                evidence_lines.append("")

        # 2. Complete Chronological Logical Transcript
        evidence_lines.append("=== COMPLETE LOGICAL INTERVIEW TRANSCRIPT ===")
        valid_turns = 0
        for t in transcript:
            # Skip non-dict or raw DOM events
            if not isinstance(t, dict):
                continue
            text = (t.get("text") or "").strip()
            if not text:
                continue

            speaker = t.get("speaker") or t.get("speaker_name") or "Unknown"
            # Skip pure system annotations if uninformative
            if speaker == "System" and not text:
                continue

            turn_id = t.get("turn_id") if t.get("turn_id") is not None else t.get("id")
            timestamp = t.get("timestamp") or t.get("finalized_at") or ""
            ts_str = f" ({timestamp})" if timestamp else ""
            tid_str = f"[Turn {turn_id}] " if turn_id is not None else ""

            evidence_lines.append(f"{tid_str}[{speaker}]{ts_str}: {text}")
            valid_turns += 1

        if valid_turns == 0 and not confirmed_qa_pairs:
            return "No conversational evidence recorded for this session."

        return "\n".join(evidence_lines)

    async def evaluate_final_interview(
        self,
        transcript: List[Dict[str, Any]],
        jd: str = "",
        resume: str = "",
        confirmed_qa_pairs: Optional[List[Dict[str, Any]]] = None,
        custom_prompt: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Public entry point for Final Evaluation synthesis.
        Executes deep holistic evaluation across the complete interview evidence.

        Returns structured FinalEvaluationReport dict, or None on failure.
        """
        confirmed_qa_pairs = list(confirmed_qa_pairs) if confirmed_qa_pairs else []
        evidence_text = self.format_interview_evidence(transcript, confirmed_qa_pairs)

        # Build confirmed Q&A lookup for validation and score preservation
        qa_lookup: Dict[str, Dict[str, Any]] = {}
        for idx, qa in enumerate(confirmed_qa_pairs):
            qid = str(qa.get("pair_id") or qa.get("qa_id") or f"QA_{idx + 1}")
            qa_lookup[qid] = qa

        # Compose dedicated system & user prompt
        custom_section = f"\nCustom Interviewer Directives:\n{custom_prompt}\n" if custom_prompt else ""

        system_prompt = (
            "You are an authoritative Senior Technical Evaluation Architect. "
            "Your task is to synthesize a complete candidate technical interview and generate a "
            "rigorous, evidence-grounded final evaluation dossier.\n\n"
            "CRITICAL OPERATIONAL RULES:\n"
            "1. Ground every claim directly in the provided interview evidence, Job Description (JD), and Resume.\n"
            "2. DO NOT invent candidate projects, skills, or answers not in the evidence.\n"
            "3. DO NOT recalculate or modify Phase 2V Accuracy Scores. Those scores are authoritative.\n"
            "4. Carefully distinguish between 'not demonstrated' and 'not assessed'. "
            "If a topic was never asked, it is 'not assessed', not a candidate deficiency.\n"
            "5. Evaluate Holistic Competency across exactly 4 dimensions: "
            "'technical_depth', 'practical_experience', 'problem_solving', and 'communication_clarity'.\n"
            "6. Every dimension score must be an integer between 0 and 100 with an evidence-grounded summary.\n"
            "7. Return ONLY valid JSON matching the specified schema. No markdown code blocks, no additional prose."
        )

        user_prompt = f"""Target Job Description:
{jd.strip() if jd and jd.strip() else "None provided"}

Candidate Resume:
{resume.strip() if resume and resume.strip() else "None provided"}
{custom_section}
Interview Evidence (Full Transcript & Confirmed Q&A):
{evidence_text}

Output a single JSON object with EXACTLY the following structure:
{{
    "holistic_competency": {{
        "dimensions": {{
            "technical_depth": {{
                "score": <0-100 integer>,
                "summary": "<Concise evidence-grounded assessment of conceptual mastery & technical correctness>"
            }},
            "practical_experience": {{
                "score": <0-100 integer>,
                "summary": "<Concise assessment of hands-on implementation, production debugging & tools>"
            }},
            "problem_solving": {{
                "score": <0-100 integer>,
                "summary": "<Concise assessment of analytical approach, edge-case handling & trade-off reasoning>"
            }},
            "communication_clarity": {{
                "score": <0-100 integer>,
                "summary": "<Concise assessment of clarity, structured delivery, technical phrasing & conciseness>"
            }}
        }}
    }},
    "strengths": [
        "<Concise, evidence-grounded strength demonstrated during interview>",
        "<Second concise strength>"
    ],
    "development_areas": [
        "<Concise, evidence-grounded knowledge gap or improvement area observed>",
        "<Second concise development area>"
    ],
    "jd_analysis": {{
        "covered_skills": ["<Skill from JD demonstrated in interview>"],
        "remaining_skills": ["<Skill from JD not demonstrated or not assessed>"],
        "summary": "<Concise summary of alignment between candidate demonstrated skills and target role requirements>"
    }},
    "resume_validation": {{
        "verified_projects": ["<Project from resume discussed and supported by interview answers>"],
        "unverified_projects": ["<Project from resume not discussed or insufficiently explored>"],
        "summary": "<Concise summary of candidate claimed background consistency with interview performance>"
    }},
    "question_analysis": [
        {{
            "pair_id": "<Existing pair_id matching confirmed Q&A>",
            "observations": "<Concise observation on the candidate's response to this specific question>"
        }}
    ],
    "conversation_summary": "<Executive 2-3 sentence overview of the interview conversation flow and candidate performance>",
    "observer_notes": [
        "<Key objective observation about interview demeanor, responsiveness, or notable highlights>"
    ]
}}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        raw_dict = None
        for attempt in range(2):
            try:
                if attempt == 0:
                    logger.info(f"[FinalEvaluationService] Submitting interview evaluation request to {self.model}...")
                else:
                    logger.warning(
                        "[FinalEvaluationService] First attempt returned malformed JSON. "
                        "Executing single retry with strict JSON instruction..."
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response could not be parsed as valid JSON. "
                            "Return ONLY valid JSON matching the required schema. "
                            "Do not include markdown fences, commentary, explanations, "
                            "or unescaped quotation marks inside JSON strings."
                        )
                    })

                chat_completion = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        messages=messages,
                        model=self.model,
                        response_format={"type": "json_object"}
                    ),
                    timeout=45.0
                )

                response_content = chat_completion.choices[0].message.content
                if not response_content or not response_content.strip():
                    logger.error(f"[FinalEvaluationService] Received empty response from LLM on attempt {attempt + 1}.")
                    if attempt == 0:
                        continue
                    return None

                try:
                    raw_dict = clean_json_loads(response_content)
                    break
                except (json.JSONDecodeError, ValueError) as json_err:
                    logger.warning(
                        f"[FinalEvaluationService] JSON parse error on attempt {attempt + 1}: {json_err}"
                    )
                    if attempt == 0:
                        continue
                    logger.error("[FinalEvaluationService] Malformed JSON persisted after retry.")
                    return None

            except asyncio.TimeoutError:
                logger.error(f"[FinalEvaluationService] LLM call timed out after 45.0 seconds on attempt {attempt + 1}.")
                return None
            except Exception as e:
                logger.error(f"[FinalEvaluationService] Unexpected error during interview evaluation on attempt {attempt + 1}: {e}")
                return None

        if not raw_dict:
            return None

        validated_report = self._validate_and_sanitize_response(raw_dict, confirmed_qa_pairs, qa_lookup)
        if not validated_report:
            logger.error("[FinalEvaluationService] Response validation failed.")
            return None

        logger.info(
            f"[FinalEvaluationService] Evaluation completed successfully. "
            f"Holistic Competency Score: {validated_report['holistic_competency']['score']}%"
        )
        return validated_report

    def _validate_and_sanitize_response(
        self,
        data: Dict[str, Any],
        confirmed_qa_pairs: List[Dict[str, Any]],
        qa_lookup: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Validates LLM response schema, score ranges, and enforces data integrity:
        1. Recalculates holistic_competency.score deterministically from dimension arithmetic mean.
        2. Clamps all dimension scores to [0, 100].
        3. Preserves Phase 2V accuracy_score on all confirmed Q&A pairs (never allows LLM to override).
        4. Validates list and string types.
        """
        if not isinstance(data, dict):
            logger.error(f"[FinalEvaluationService] Expected dict, got {type(data)}")
            return None

        # 1. Validate holistic competency dimensions
        holistic_raw = data.get("holistic_competency")
        if not isinstance(holistic_raw, dict):
            logger.error("[FinalEvaluationService] Missing or invalid 'holistic_competency' field.")
            return None

        dims_raw = holistic_raw.get("dimensions")
        if not isinstance(dims_raw, dict):
            logger.error("[FinalEvaluationService] Missing or invalid 'dimensions' field in holistic_competency.")
            return None

        sanitized_dimensions: Dict[str, Dict[str, Any]] = {}
        dimension_scores: List[int] = []

        for dim_key in self.SUPPORTED_DIMENSIONS:
            dim_data = dims_raw.get(dim_key)
            if not isinstance(dim_data, dict):
                logger.error(f"[FinalEvaluationService] Missing required competency dimension: '{dim_key}'")
                return None

            raw_score = dim_data.get("score")
            try:
                score_int = int(raw_score)
            except (ValueError, TypeError):
                logger.error(f"[FinalEvaluationService] Invalid score for '{dim_key}': {raw_score}")
                return None

            # Score range validation: [0, 100]
            if score_int < 0 or score_int > 100:
                logger.error(f"[FinalEvaluationService] Score out of range (0-100) for '{dim_key}': {score_int}")
                return None

            summary_str = str(dim_data.get("summary") or "").strip()
            sanitized_dimensions[dim_key] = {
                "score": score_int,
                "summary": summary_str
            }
            dimension_scores.append(score_int)

        # Deterministic Holistic Competency Score: Arithmetic mean of all dimensions
        holistic_score = round(sum(dimension_scores) / len(dimension_scores)) if dimension_scores else 0
        holistic_score = max(0, min(100, holistic_score))

        # 2. Validate strengths & development areas
        strengths = data.get("strengths")
        if not isinstance(strengths, list):
            strengths = [str(strengths)] if strengths else []
        strengths = [str(s).strip() for s in strengths if str(s).strip()]

        development_areas = data.get("development_areas")
        if not isinstance(development_areas, list):
            development_areas = [str(development_areas)] if development_areas else []
        development_areas = [str(d).strip() for d in development_areas if str(d).strip()]

        # 3. Validate JD analysis
        jd_raw = data.get("jd_analysis")
        if not isinstance(jd_raw, dict):
            jd_raw = {}
        covered_skills = [str(s).strip() for s in jd_raw.get("covered_skills", []) if str(s).strip()] if isinstance(jd_raw.get("covered_skills"), list) else []
        remaining_skills = [str(s).strip() for s in jd_raw.get("remaining_skills", []) if str(s).strip()] if isinstance(jd_raw.get("remaining_skills"), list) else []
        jd_summary = str(jd_raw.get("summary") or "").strip()

        # 4. Validate resume validation
        resume_raw = data.get("resume_validation")
        if not isinstance(resume_raw, dict):
            resume_raw = {}
        verified_projects = [str(p).strip() for p in resume_raw.get("verified_projects", []) if str(p).strip()] if isinstance(resume_raw.get("verified_projects"), list) else []
        unverified_projects = [str(p).strip() for p in resume_raw.get("unverified_projects", []) if str(p).strip()] if isinstance(resume_raw.get("unverified_projects"), list) else []
        resume_summary = str(resume_raw.get("summary") or "").strip()

        # 5. Question-by-question analysis: Strictly preserve Phase 2V accuracy_score
        llm_q_analysis = data.get("question_analysis")
        llm_obs_map: Dict[str, str] = {}
        if isinstance(llm_q_analysis, list):
            for item in llm_q_analysis:
                if isinstance(item, dict):
                    pid = str(item.get("pair_id") or item.get("qa_id") or "")
                    if pid:
                        llm_obs_map[pid] = str(item.get("observations") or item.get("comment") or "").strip()

        sanitized_qa_analysis: List[Dict[str, Any]] = []
        for idx, qa in enumerate(confirmed_qa_pairs):
            pid = str(qa.get("pair_id") or qa.get("qa_id") or f"QA_{idx + 1}")
            q_text = qa.get("question", "")
            a_text = qa.get("answer", "")
            # STRICT INVARIANT: Preserve authoritative Phase 2V accuracy_score
            p2v_score = qa.get("accuracy_score")
            obs = llm_obs_map.get(pid, "")

            entry = {
                "pair_id": pid,
                "qa_id": pid,
                "question": q_text,
                "answer": a_text,
                "accuracy_score": p2v_score,
                "observations": obs
            }
            sanitized_qa_analysis.append(entry)

        # 6. Conversation summary & Observer notes
        conv_summary = str(data.get("conversation_summary") or "").strip()
        obs_notes = data.get("observer_notes")
        if not isinstance(obs_notes, list):
            obs_notes = [str(obs_notes)] if obs_notes else []
        obs_notes = [str(n).strip() for n in obs_notes if str(n).strip()]

        return {
            "holistic_competency": {
                "score": holistic_score,
                "dimensions": sanitized_dimensions
            },
            "strengths": strengths,
            "development_areas": development_areas,
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


# Convenience module-level entry point
async def evaluate_final_interview(
    transcript: List[Dict[str, Any]],
    jd: str = "",
    resume: str = "",
    confirmed_qa_pairs: Optional[List[Dict[str, Any]]] = None,
    custom_prompt: str = ""
) -> Optional[Dict[str, Any]]:
    """Isolated module-level entry point for Final Evaluation synthesis."""
    service = FinalEvaluationService()
    return await service.evaluate_final_interview(
        transcript=transcript,
        jd=jd,
        resume=resume,
        confirmed_qa_pairs=confirmed_qa_pairs,
        custom_prompt=custom_prompt
    )
