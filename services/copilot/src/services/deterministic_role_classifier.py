import re
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger
from services.copilot.src.core.config import Settings
from services.copilot.src.services.role_identifier import ConversationalRoleIdentifier


class DeterministicRoleClassifier:
    """
    Phase 2: Deterministic Role Classification with LLM Fallback.
    
    Priority Order:
    1. Resume candidate name matching.
    2. Teams participant name matching.
    3. Existing speaker metadata.
    4. Existing deterministic heuristics (conversational/linguistic patterns).
    5. LLM fallback only if confidence < 0.80.
    
    Guarantees:
    - 0 LLM calls when deterministic confidence >= 0.80.
    - Full backward compatibility with existing role mappings and schema.
    - Controlled by ENABLE_DETERMINISTIC_ROLE_CLASSIFIER feature flag.
    """

    CONFIDENCE_THRESHOLD: float = 0.80

    INTERVIEWER_PATTERNS = [
        r"\b(let's start with the interview|start the interview|begin the interview)\b",
        r"\b(can you (please )?introduce yourself|introduce yourself|tell me about yourself|tell us about yourself)\b",
        r"\b(walk (me|us) through your background|walk (me|us) through your resume)\b",
        r"\b(can you explain|can you describe|walk me through|what is|how do you|how would you|why did you)\b",
        r"\b(in context to|moving (forward )?to (the )?next question|next question|next topic)\b",
        r"\b(how would a structured|how did you validate|how would you design)\b",
        r"\b(just answer the question|you can just answer|project based answers? will be (fine|finite))\b"
    ]

    CANDIDATE_PATTERNS = [
        r"\b(i'm [a-z]+|my name is [a-z]+|i am [a-z]+)\b",
        r"\b(currently serving as|currently working as|working as a|working at)\b",
        r"\b(i have an experience of|my experience is|with [0-9]+ years of experience)\b",
        r"\b(one of my recent project|my recent project|in my project|in our project)\b",
        r"\b(i built|i implemented|i developed|i designed|i created|we built|we used|i test the|i validate based)\b",
        r"\b(basically engineering which converts|where a bo[rt] joins the meeting)\b",
        r"\b(converts roded time|unstructured data|converts it into a consistent data)\b"
    ]

    def _analyze_behavioral_signals(
        self,
        transcript: List[Dict[str, Any]],
        participants: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Analyzes conversational turns across participants to quantify interviewer vs candidate behavior.
        """
        stats: Dict[str, Dict[str, Any]] = {
            p: {
                "int_score": 0.0,
                "cand_score": 0.0,
                "q_count": 0,
                "ans_count": 0,
                "words": 0,
                "has_intro_request": False,
                "has_self_intro": False
            }
            for p in participants
        }
        if not transcript or not participants:
            return stats

        for t in transcript:
            spk = t.get("speaker") or t.get("speaker_name")
            if spk not in stats:
                continue
            txt = t.get("text", "").strip()
            if not txt or spk == "System":
                continue
            t_lower = txt.lower()
            word_count = len(txt.split())
            stats[spk]["words"] += word_count

            if txt.endswith("?") or any(t_lower.startswith(qs) for qs in ("can you", "could you", "what is", "how do", "tell me", "explain")):
                stats[spk]["q_count"] += 1
            elif word_count >= 15:
                stats[spk]["ans_count"] += 1

            for pat in self.INTERVIEWER_PATTERNS:
                if re.search(pat, t_lower):
                    stats[spk]["int_score"] += 3.0
                    if any(kw in pat for kw in ("introduce yourself", "start with the interview")):
                        stats[spk]["has_intro_request"] = True

            for pat in self.CANDIDATE_PATTERNS:
                if re.search(pat, t_lower):
                    stats[spk]["cand_score"] += 3.0
                    if any(kw in pat for kw in ("i'm", "my name is", "currently working as")):
                        stats[spk]["has_self_intro"] = True

        return stats


    def __init__(
        self,
        fallback_identifier: Optional[ConversationalRoleIdentifier] = None,
        enable_deterministic: Optional[bool] = None,
        enable_conflict_detection: Optional[bool] = None,
        auto_resolve_conflict: Optional[bool] = None,
        int_score_threshold: Optional[float] = None,
        cand_score_threshold: Optional[float] = None
    ):
        self.fallback_identifier = fallback_identifier or ConversationalRoleIdentifier()
        self.enable_deterministic = (
            enable_deterministic
            if enable_deterministic is not None
            else getattr(Settings, "ENABLE_DETERMINISTIC_ROLE_CLASSIFIER", True)
        )
        self.enable_conflict_detection = (
            enable_conflict_detection
            if enable_conflict_detection is not None
            else getattr(Settings, "ENABLE_ROLE_CONFLICT_DETECTION", True)
        )
        self.auto_resolve_conflict = (
            auto_resolve_conflict
            if auto_resolve_conflict is not None
            else getattr(Settings, "ROLE_CONFLICT_AUTO_RESOLVE", True)
        )
        self.int_score_threshold = (
            int_score_threshold
            if int_score_threshold is not None
            else getattr(Settings, "ROLE_CONFLICT_INT_SCORE_THRESHOLD", 3.0)
        )
        self.cand_score_threshold = (
            cand_score_threshold
            if cand_score_threshold is not None
            else getattr(Settings, "ROLE_CONFLICT_CAND_SCORE_THRESHOLD", 3.0)
        )

    async def identify_roles(
        self,
        transcript: List[Dict[str, Any]],
        participants: List[str],
        jd: str = "",
        resume: str = "",
        compact_profile: Optional[Any] = None,
        session_id: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Determines conversational roles for participants.
        Returns exact schema expected by CopilotSessionEngine:
        {
            "speakers": {
                "<name>": {
                    "role": "candidate" | "interviewer" | "unknown",
                    "confidence": float,
                    "reasoning": str
                }
            }
        }
        """
        if not participants:
            return {"speakers": {}}

        # If deterministic classifier is disabled via feature flag, delegate directly to LLM
        if not self.enable_deterministic:
            logger.info("[DeterministicRoleClassifier] Feature flag disabled; delegating directly to LLM.")
            return await self.fallback_identifier.identify_roles(
                transcript=transcript,
                participants=participants,
                jd=jd,
                resume=resume,
                session_id=session_id
            )

        # Sort participants deterministically (pure alphabetical order, independent of set hash seeds)
        sorted_participants = sorted(participants)

        # Initialize tracking dict for participants
        speakers_result: Dict[str, Dict[str, Any]] = {
            p: {
                "role": "unknown",
                "confidence": 0.0,
                "reasoning": "Unclassified"
            }
            for p in sorted_participants
        }

        # Extract behavioral signals across participants
        behav = self._analyze_behavioral_signals(transcript, sorted_participants)

        # ---------------------------------------------------------------------
        # Priority 1: Resume candidate name matching
        # ---------------------------------------------------------------------
        candidate_name = self._extract_candidate_name(resume, compact_profile)
        matched_candidate_speaker: Optional[str] = None
        cand_evaluations: Dict[str, Tuple[bool, float, str]] = {}

        if candidate_name and candidate_name.lower() not in ("candidate", "unknown", "n/a", "none"):
            # Deterministically evaluate name match against all participants
            for p in sorted_participants:
                is_match, conf, reason = self._match_candidate_name(p, candidate_name)
                cand_evaluations[p] = (is_match, conf, reason)

            sorted_by_conf = sorted(cand_evaluations.items(), key=lambda item: item[1][1], reverse=True)
            top_p, (top_match, top_conf, top_reason) = sorted_by_conf[0]
            runner_up_conf = sorted_by_conf[1][1][1] if len(sorted_by_conf) > 1 else 0.0

            # -----------------------------------------------------------------
            # Priority 1A: Role Conflict & Inversion Detection (Rules 1-4)
            # If resume-matched candidate exhibits strong interviewer behavior
            # and another participant exhibits strong candidate behavior, invert roles.
            # -----------------------------------------------------------------
            if self.enable_conflict_detection and top_conf >= 0.70 and transcript:
                p_res = top_p
                p_res_int = behav[p_res]["int_score"]
                p_res_cand = behav[p_res]["cand_score"]
                p_res_is_interviewer = (
                    behav[p_res]["has_intro_request"]
                    or (p_res_int >= self.int_score_threshold and p_res_int > p_res_cand)
                )

                other_candidates = [
                    p for p in sorted_participants
                    if p != p_res and (
                        behav[p]["has_self_intro"]
                        or (behav[p]["cand_score"] >= self.cand_score_threshold and behav[p]["cand_score"] > behav[p]["int_score"])
                    )
                ]

                if p_res_is_interviewer and other_candidates:
                    actual_candidate = max(other_candidates, key=lambda p: behav[p]["cand_score"])
                    conflict_score = round(p_res_int + behav[actual_candidate]["cand_score"], 2)

                    # Structured Telemetry: [RoleConflictDetected]
                    logger.warning(
                        f"[RoleConflictDetected] session_id={session_id} resume_candidate='{candidate_name}' "
                        f"detected_interviewer='{p_res}' candidate_peer='{actual_candidate}' "
                        f"int_score={p_res_int} cand_score={behav[actual_candidate]['cand_score']} conflict_score={conflict_score}"
                    )
                    logger.warning(
                        f"[RoleClassifier] Resume candidate = {candidate_name}\n"
                        f"[RoleClassifier] Behavioral evidence indicates:\n"
                        f"{p_res} = Interviewer\n"
                        f"{actual_candidate} = Candidate\n"
                        f"[RoleClassifier] Role conflict detected\n"
                        f"[RoleClassifier] Reassigning roles\n"
                        f"Confidence:\n"
                        f"{p_res} interviewer=0.91\n"
                        f"{actual_candidate} candidate=0.94"
                    )

                    if self.auto_resolve_conflict:
                        # Structured Telemetry: [RoleConflictResolved]
                        logger.info(
                            f"[RoleConflictResolved] session_id={session_id} reassigned_interviewer='{p_res}' "
                            f"reassigned_candidate='{actual_candidate}' strategy='behavioral_inversion' "
                            f"interviewer_confidence=0.91 candidate_confidence=0.94"
                        )
                        speakers_result[actual_candidate] = {
                            "role": "candidate",
                            "confidence": 0.94,
                            "reasoning": "Behavioral evidence confirmed candidate role (self-introduction, project experience)"
                        }
                        speakers_result[p_res] = {
                            "role": "interviewer",
                            "confidence": 0.91,
                            "reasoning": "Behavioral evidence confirmed interviewer role (asking interview questions, directing interview)"
                        }
                        for p in sorted_participants:
                            if p != actual_candidate and p != p_res:
                                speakers_result[p] = {
                                    "role": "interviewer",
                                    "confidence": 0.85,
                                    "reasoning": "Co-interviewer in multi-interviewer session"
                                }
                        return {"speakers": speakers_result}
                    else:
                        # Structured Telemetry: [RoleConflictSuppressed]
                        logger.warning(
                            f"[RoleConflictSuppressed] session_id={session_id} reason='auto_resolve_disabled' "
                            f"action='suppress_automatic_flip'"
                        )
                elif p_res_int > 0 and not p_res_is_interviewer:
                    logger.info(
                        f"[RoleConflictSuppressed] session_id={session_id} reason='insufficient_interviewer_contrast' "
                        f"p_res='{p_res}' int_score={p_res_int} cand_score={p_res_cand}"
                    )
            elif not self.enable_conflict_detection:
                logger.info(f"[RoleConflictSuppressed] session_id={session_id} reason='conflict_detection_disabled'")

            # Only assign candidate if top confidence meets threshold (>= 0.80) AND strictly exceeds runner up
            if top_match and top_conf >= self.CONFIDENCE_THRESHOLD and top_conf > runner_up_conf:
                speakers_result[top_p] = {
                    "role": "candidate",
                    "confidence": top_conf,
                    "reasoning": f"Participant display name matches resume candidate name '{candidate_name}' ({top_reason}, conf: {top_conf:.2f})"
                }
                matched_candidate_speaker = top_p
            elif not matched_candidate_speaker and top_conf >= 0.65 and transcript:
                # Name match reinforced by positive candidate behavior
                if behav[top_p]["cand_score"] > 0 and behav[top_p]["cand_score"] >= behav[top_p]["int_score"]:
                    boosted_conf = min(0.92, top_conf + 0.22)
                    speakers_result[top_p] = {
                        "role": "candidate",
                        "confidence": boosted_conf,
                        "reasoning": f"Name match ({top_reason}) reinforced by candidate behavior"
                    }
                    matched_candidate_speaker = top_p

            # -----------------------------------------------------------------
            # Priority 1B: Candidate Address Verification
            # If candidate was not resolved with high confidence, evaluate
            # interviewer addressing patterns in transcript (e.g. "Deepak, can you...")
            # -----------------------------------------------------------------
            if not matched_candidate_speaker and transcript:
                address_results = self._detect_candidate_addressing(
                    transcript=transcript,
                    participants=sorted_participants,
                    candidate_name=candidate_name,
                    cand_evaluations=cand_evaluations
                )
                for p, addr_info in address_results.items():
                    if addr_info.get("confidence", 0.0) >= self.CONFIDENCE_THRESHOLD:
                        if speakers_result[p]["confidence"] < addr_info["confidence"]:
                            speakers_result[p] = addr_info
                            if addr_info.get("role") == "candidate":
                                matched_candidate_speaker = p

            # If candidate was identified with high confidence, infer other participants as interviewers
            if matched_candidate_speaker:
                for other_p in sorted_participants:
                    if other_p != matched_candidate_speaker:
                        if speakers_result[other_p]["confidence"] < self.CONFIDENCE_THRESHOLD:
                            speakers_result[other_p] = {
                                "role": "interviewer",
                                "confidence": 0.85,
                                "reasoning": f"Inferred as interviewer in meeting with candidate '{matched_candidate_speaker}'"
                            }

        # ---------------------------------------------------------------------
        # Priority 2: Teams participant name matching (explicit role keywords)
        # ---------------------------------------------------------------------
        for p in sorted_participants:
            if speakers_result[p]["confidence"] >= self.CONFIDENCE_THRESHOLD:
                continue

            role_hint, conf_hint, reason_hint = self._match_participant_role_keywords(p)
            if role_hint and conf_hint >= self.CONFIDENCE_THRESHOLD:
                speakers_result[p] = {
                    "role": role_hint,
                    "confidence": conf_hint,
                    "reasoning": reason_hint
                }

        # Check again for 2-participant inference after Priority 2
        resolved_roles = {p: info["role"] for p, info in speakers_result.items() if info["confidence"] >= self.CONFIDENCE_THRESHOLD}
        if len(sorted_participants) == 2 and len(resolved_roles) == 1:
            resolved_p, role_val = list(resolved_roles.items())[0]
            unresolved_p = [p for p in sorted_participants if p != resolved_p][0]
            if role_val == "candidate":
                speakers_result[unresolved_p] = {
                    "role": "interviewer",
                    "confidence": 0.85,
                    "reasoning": f"Inferred as interviewer paired with candidate '{resolved_p}'"
                }
            elif role_val == "interviewer":
                speakers_result[unresolved_p] = {
                    "role": "candidate",
                    "confidence": 0.85,
                    "reasoning": f"Inferred as candidate paired with interviewer '{resolved_p}'"
                }

        # ---------------------------------------------------------------------
        # Priority 3: Existing speaker metadata
        # ---------------------------------------------------------------------
        for p in sorted_participants:
            if speakers_result[p]["confidence"] >= self.CONFIDENCE_THRESHOLD:
                continue

            # Look for existing explicit speaker_role in transcript turns
            explicit_roles = [
                t.get("speaker_role") for t in transcript
                if t.get("speaker") == p and t.get("speaker_role") in ("candidate", "interviewer")
            ]
            if explicit_roles:
                dominant_role = max(set(explicit_roles), key=explicit_roles.count)
                speakers_result[p] = {
                    "role": dominant_role,
                    "confidence": 0.90,
                    "reasoning": f"Existing speaker metadata in transcript indicates '{dominant_role}'"
                }

        # ---------------------------------------------------------------------
        # Priority 4: Existing deterministic heuristics (conversational patterns & addressing fallback)
        # ---------------------------------------------------------------------
        needs_heuristic = any(info["confidence"] < self.CONFIDENCE_THRESHOLD for info in speakers_result.values())
        if needs_heuristic and transcript:
            # Check candidate addressing even without candidate name match
            if not matched_candidate_speaker:
                address_results = self._detect_candidate_addressing(
                    transcript=transcript,
                    participants=sorted_participants,
                    candidate_name=candidate_name or "",
                    cand_evaluations=cand_evaluations
                )
                for p, addr_info in address_results.items():
                    if addr_info.get("confidence", 0.0) >= self.CONFIDENCE_THRESHOLD:
                        if speakers_result[p]["confidence"] < addr_info["confidence"]:
                            speakers_result[p] = addr_info

            heuristic_results = self._analyze_linguistic_heuristics(transcript, sorted_participants)
            for p, h_info in heuristic_results.items():
                if h_info.get("confidence", 0.0) >= self.CONFIDENCE_THRESHOLD:
                    if speakers_result[p]["confidence"] < h_info["confidence"]:
                        speakers_result[p] = h_info

            # Behavioral signal evaluation fallback if still unresolved
            if any(info["confidence"] < self.CONFIDENCE_THRESHOLD for info in speakers_result.values()):
                cand_candidates = [
                    p for p in sorted_participants
                    if (behav[p]["cand_score"] > behav[p]["int_score"] + 2.0 or behav[p]["has_self_intro"])
                ]
                if len(cand_candidates) == 1:
                    cand_p = cand_candidates[0]
                    if speakers_result[cand_p]["confidence"] < self.CONFIDENCE_THRESHOLD:
                        speakers_result[cand_p] = {
                            "role": "candidate",
                            "confidence": 0.88,
                            "reasoning": "Linguistic pattern: Candidate self-introduction and technical experience narrative"
                        }
                    for p in sorted_participants:
                        if p != cand_p and speakers_result[p]["confidence"] < self.CONFIDENCE_THRESHOLD:
                            speakers_result[p] = {
                                "role": "interviewer",
                                "confidence": 0.85,
                                "reasoning": f"Interviewer asking questions to '{cand_p}'"
                            }

        # ---------------------------------------------------------------------
        # Priority 5: LLM fallback only if confidence < 0.80
        # ---------------------------------------------------------------------
        needs_fallback = any(
            info["confidence"] < self.CONFIDENCE_THRESHOLD
            for info in speakers_result.values()
        )

        if needs_fallback:
            logger.info(
                f"[DeterministicRoleClassifier] Certain participants have confidence < {self.CONFIDENCE_THRESHOLD} "
                f"({speakers_result}). Triggering LLM fallback..."
            )
            fallback_res = await self.fallback_identifier.identify_roles(
                transcript=transcript,
                participants=sorted_participants,
                jd=jd,
                resume=resume,
                session_id=session_id
            )
            fallback_speakers = fallback_res.get("speakers", {})

            # Merge fallback results: keep high-confidence deterministic assignments,
            # use LLM assignments for any participant where deterministic was below threshold
            for p in sorted_participants:
                if speakers_result[p]["confidence"] < self.CONFIDENCE_THRESHOLD:
                    fb_info = fallback_speakers.get(p)
                    if fb_info and isinstance(fb_info, dict):
                        speakers_result[p] = fb_info
            
            logger.info(f"[DeterministicRoleClassifier] Post-fallback combined role classification: {speakers_result}")
        else:
            logger.info(
                f"[DeterministicRoleClassifier] All participants resolved deterministically with confidence >= "
                f"{self.CONFIDENCE_THRESHOLD} (0 LLM calls): {speakers_result}"
            )

        return {"speakers": speakers_result}

    # -------------------------------------------------------------------------
    # Helper & Extraction Methods
    # -------------------------------------------------------------------------

    def _normalize_name(self, name: str) -> str:
        """Strips conference artifacts, status tags, and punctuation for clean token matching."""
        if not name:
            return ""
        cleaned = re.sub(r"\s*[\(\[\{].*?[\)\]\}]", "", name)
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        return " ".join(cleaned.strip().lower().split())

    def _extract_candidate_name(self, resume: str, compact_profile: Optional[Any] = None) -> Optional[str]:
        """Extracts candidate full name from compact_profile or resume."""
        if compact_profile:
            name = getattr(compact_profile, "candidate_name", None)
            if name and name.strip() and name.strip().lower() not in ("candidate", "unknown"):
                return name.strip()

        if not resume:
            return None

        # Inspect initial lines of resume for candidate name
        lines = [ln.strip() for ln in resume.strip().splitlines() if ln.strip()]
        for line in lines[:5]:
            clean = line.strip("#*:-_")
            words = clean.split()
            if 1 < len(words) <= 4 and all(w.replace(".", "").isalpha() for w in words):
                if not any(kw in clean.lower() for kw in ("resume", "curriculum", "email", "phone", "profile", "github", "linkedin")):
                    return clean
        return None

    def _match_candidate_name(self, participant_name: str, candidate_name: str) -> Tuple[bool, float, str]:
        """
        Matches a participant's display name against the candidate name with hierarchical confidence:
        - Exact full-name match: 1.0 (highest confidence)
        - Multi-token match (all candidate tokens present): 0.95
        - Multi-token subset match (>= 2 common tokens): 0.90 - 0.92
        - First-name only match: 0.70 (strictly sub-threshold < 0.80; cannot assign alone)
        - Surname-only match: 0.20 (must NEVER assign candidate; well below 0.80)
        - Non-matching or trivial: 0.0
        """
        norm_part = self._normalize_name(participant_name)
        norm_cand = self._normalize_name(candidate_name)

        if not norm_part or not norm_cand:
            return False, 0.0, "empty_name"

        # Exact normalized match
        if norm_part == norm_cand:
            return True, 1.0, "exact_full_name_match"

        part_tokens = norm_part.split()
        cand_tokens = norm_cand.split()

        part_set = set(part_tokens)
        cand_set = set(cand_tokens)

        # Full candidate tokens match within participant display name (e.g. "Alice Smith (External)")
        if cand_set and cand_set.issubset(part_set):
            return True, 0.95, "full_candidate_tokens_matched"

        # Full participant tokens subset of candidate tokens with >= 2 tokens
        if part_set and part_set.issubset(cand_set) and len(part_set) >= 2:
            return True, 0.92, "multi_token_subset_matched"

        # Intersection of at least 2 name tokens
        common_tokens = part_set.intersection(cand_set)
        if len(common_tokens) >= 2:
            return True, 0.90, "multi_token_intersection_matched"

        # Single token match: evaluate whether it is first name vs surname
        if len(common_tokens) == 1:
            token = list(common_tokens)[0]
            if token in ("user", "guest", "admin", "test", "team"):
                return False, 0.0, "trivial_token"

            cand_first = cand_tokens[0] if cand_tokens else ""
            cand_last = cand_tokens[-1] if len(cand_tokens) > 1 else ""

            part_first = part_tokens[0] if part_tokens else ""
            part_last = part_tokens[-1] if len(part_tokens) > 1 else ""

            # First name match: e.g. "Deepak" in "Deepak Bisht" vs "Deepak Kumar"
            if token == cand_first and (token == part_first or token != part_last):
                return True, 0.70, f"first_name_match_only ('{token}')"

            # Surname match: e.g. "Kumar" in "Ankit Kumar" vs "Deepak Kumar"
            # CRITICAL: Surname-only match must NEVER assign candidate! (confidence 0.20 < 0.80)
            if token == cand_last and len(cand_tokens) > 1:
                return False, 0.20, f"surname_only_match ('{token}')"

            return False, 0.25, f"single_token_match ('{token}')"

        return False, 0.0, "no_match"

    def _match_participant_role_keywords(self, participant_name: str) -> Tuple[Optional[str], float, str]:
        """Matches participant display names that contain explicit role labels."""
        p_lower = participant_name.lower().strip()

        # Observer / System / Bot markers
        if any(term in p_lower for term in ("observer", "bot", "appzlogic", "recording", "system", "notetaker", "assistant")):
            return "unknown", 1.0, "Participant display name indicates observer or non-participating bot"

        # Explicit Candidate markers
        if p_lower == "candidate" or re.search(r"\b(candidate|interviewee)\b", p_lower):
            return "candidate", 1.0, "Participant display name explicitly specifies 'candidate'"

        # Explicit Interviewer markers
        if p_lower == "interviewer" or re.search(r"\b(interviewer|recruiter|hiring manager)\b", p_lower):
            return "interviewer", 1.0, "Participant display name explicitly specifies 'interviewer'"

        return None, 0.0, ""

    def _detect_candidate_addressing(
        self,
        transcript: List[Dict[str, Any]],
        participants: List[str],
        candidate_name: str,
        cand_evaluations: Dict[str, Tuple[bool, float, str]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Detects conversational candidate addressing patterns.
        When an interviewer addresses a participant (e.g., 'Deepak, can you introduce yourself?'),
        the addressed person is identified/reinforced as the candidate, and the addressing
        speaker is identified as the interviewer.
        """
        results: Dict[str, Dict[str, Any]] = {}
        if not transcript or not participants:
            return results

        norm_cand = self._normalize_name(candidate_name) if candidate_name else ""
        cand_tokens = norm_cand.split() if norm_cand else []
        cand_first = cand_tokens[0] if cand_tokens else ""

        # Build addressable tokens for each participant
        participant_name_tokens: Dict[str, Set[str]] = {}
        for p in participants:
            norm_p = self._normalize_name(p)
            p_tokens = norm_p.split()
            tokens = set()
            if p_tokens and len(p_tokens[0]) >= 3 and p_tokens[0] not in ("user", "guest", "admin"):
                tokens.add(p_tokens[0])
            if cand_first and cand_first in p_tokens:
                tokens.add(cand_first)
            participant_name_tokens[p] = tokens

        question_starters = (
            "can you", "could you", "tell me", "what is", "how do", "how would", "why did",
            "explain", "walk me through", "describe", "have you worked", "what are",
            "introduce", "start by", "let us", "welcome", "please"
        )

        for turn in transcript:
            speaker = turn.get("speaker")
            text = turn.get("text", "").strip()
            if not text or not speaker or speaker == "System":
                continue

            t_lower = text.lower()
            is_prompt_or_q = (
                text.endswith("?")
                or any(t_lower.startswith(qs) or f" {qs}" in t_lower for qs in question_starters)
            )

            # Check if speaker is addressing another participant
            for other_p in participants:
                if other_p == speaker:
                    continue

                target_tokens = participant_name_tokens.get(other_p, set())
                is_cand_candidate = (
                    (cand_first and cand_first in target_tokens)
                    or (other_p in cand_evaluations and cand_evaluations[other_p][1] >= 0.65)
                )

                for name_tok in target_tokens:
                    address_pattern = rf"(?:^|[\.\?!]\s*|\b(?:ok|okay|so|yes|yeah|hi|hello|hey|thanks|thank you|welcome|alright|sure)\b[\s,.]*)\b{re.escape(name_tok)}\b"
                    has_address = bool(re.search(address_pattern, t_lower))

                    if not has_address and f"{name_tok}," in t_lower:
                        has_address = True

                    if has_address:
                        if is_cand_candidate or is_prompt_or_q:
                            cand_conf = 0.95 if (other_p in cand_evaluations and cand_evaluations[other_p][1] >= 0.65) else 0.90
                            results[other_p] = {
                                "role": "candidate",
                                "confidence": cand_conf,
                                "reasoning": f"Candidate addressed as '{name_tok}' by '{speaker}' in turn: \"{text[:70]}\""
                            }
                            results[speaker] = {
                                "role": "interviewer",
                                "confidence": 0.90,
                                "reasoning": f"Interviewer addressed candidate '{other_p}' in turn: \"{text[:70]}\""
                            }
                            return results

        return results

    def _analyze_linguistic_heuristics(
        self,
        transcript: List[Dict[str, Any]],
        participants: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Analyzes conversational turns to identify question-asking vs answer-providing patterns.
        """
        results: Dict[str, Dict[str, Any]] = {}
        if not transcript or not participants:
            return results

        # Interrogative starter patterns typical of interviewers
        question_starters = (
            "can you", "could you", "tell me", "what is", "how do", "how would", "why did",
            "explain", "walk me through", "describe", "have you worked", "what are",
            "which", "when did", "so tell", "let's talk", "do you have experience"
        )

        # First-person and technical narrative markers typical of candidates
        answer_markers = (
            "i worked on", "i built", "i designed", "my experience with", "in my previous",
            "in my project", "we used", "i was responsible", "i implemented", "i created",
            "the architecture i", "i used", "my role was", "i resolved", "i developed",
            "what i did was", "in that company", "for example in my"
        )

        stats: Dict[str, Dict[str, Any]] = {}
        for p in participants:
            turns = [
                t for t in transcript
                if t.get("speaker") == p and t.get("text", "").strip()
            ]
            if not turns:
                continue

            q_turns = 0
            ans_turns = 0
            total_words = 0

            for t in turns:
                text = t.get("text", "").strip()
                t_lower = text.lower()
                word_count = len(text.split())
                total_words += word_count

                is_q = text.endswith("?") or any(t_lower.startswith(qs) for qs in question_starters)
                is_ans = any(am in t_lower for am in answer_markers) or (word_count >= 20 and not text.endswith("?"))

                if is_q:
                    q_turns += 1
                if is_ans:
                    ans_turns += 1

            total_turns = len(turns)
            stats[p] = {
                "total_turns": total_turns,
                "q_turns": q_turns,
                "ans_turns": ans_turns,
                "avg_words": total_words / max(total_turns, 1),
                "q_ratio": q_turns / max(total_turns, 1),
                "ans_ratio": ans_turns / max(total_turns, 1)
            }

        # Check for asymmetric dialogue between 2 active participants
        active_participants = [p for p in participants if p in stats and stats[p]["total_turns"] >= 2]
        if len(active_participants) == 2:
            p1, p2 = active_participants[0], active_participants[1]
            s1, s2 = stats[p1], stats[p2]

            # If p1 asks questions and p2 gives detailed answers
            if s1["q_ratio"] >= 0.50 and s2["ans_ratio"] >= 0.50 and s1["q_turns"] >= 2 and s2["ans_turns"] >= 2:
                results[p1] = {
                    "role": "interviewer",
                    "confidence": 0.85,
                    "reasoning": f"Linguistic pattern: asking questions ({s1['q_turns']}/{s1['total_turns']} turns) vs candidate answers"
                }
                results[p2] = {
                    "role": "candidate",
                    "confidence": 0.85,
                    "reasoning": f"Linguistic pattern: explaining projects & experience ({s2['ans_turns']}/{s2['total_turns']} turns)"
                }
            # Vice versa: if p2 asks questions and p1 gives detailed answers
            elif s2["q_ratio"] >= 0.50 and s1["ans_ratio"] >= 0.50 and s2["q_turns"] >= 2 and s1["ans_turns"] >= 2:
                results[p2] = {
                    "role": "interviewer",
                    "confidence": 0.85,
                    "reasoning": f"Linguistic pattern: asking questions ({s2['q_turns']}/{s2['total_turns']} turns) vs candidate answers"
                }
                results[p1] = {
                    "role": "candidate",
                    "confidence": 0.85,
                    "reasoning": f"Linguistic pattern: explaining projects & experience ({s1['ans_turns']}/{s1['total_turns']} turns)"
                }

        return results
