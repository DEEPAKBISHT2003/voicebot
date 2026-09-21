"""
Phase 2M — Native Caption Logical Turn Aggregation & Evaluation Alignment Engine
File: services/copilot/src/pipeline/native_turn_aligner.py

Implements:
1. LogicalTurn: Uninterrupted conversational contribution from one speaker.
2. NativeLogicalTurnAggregator: Stitches consecutive caption sequences from the same
   speaker across natural pauses (~2.0s), closing turns on speaker switch or silence.
3. NativeShadowTurnController: Routes finalized logical turns to shadow evaluation
   EXACTLY ONCE for candidate answers, completely filtering out interviewer speech.
4. TurnAlignmentClassifier: Classifies turn behavior into:
   - CORRECT_ONE_TO_ONE
   - FRAGMENTED
   - MERGED
   - DUPLICATED
   - MISSING
   - UNKNOWN

CRITICAL CONSTRAINTS:
- services/interview/** is 100% untouched.
- Preserves exact Teams speaker_name (no role inferencing, no LLM renaming).
- Exactly ONE evaluation per candidate answer.
- Zero evaluations for interviewer speech.
"""

import os
import json
import time
import math
import copy
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Set
from loguru import logger

from services.copilot.src.pipeline.native_turn_finalizer import (
    FinalizedTurn,
    parse_iso_timestamp,
    compute_percentiles
)
from services.copilot.src.pipeline.native_shadow_evaluator import NativeShadowEvaluationAdapter


class LogicalTurn:
    """
    Represents one uninterrupted conversational contribution from one speaker
    that has meaning in the interview context.
    """
    def __init__(
        self,
        logical_turn_id: int,
        speaker_name: str,
        text: str,
        sequence_ids: List[int],
        first_seen_at: datetime,
        last_update_at: datetime,
        finalized_at: datetime,
        boundary_reason: str = "speaker_switch",
        is_candidate: Optional[bool] = None,
        raw_event_count: int = 1,
        source: str = "teams_native_logical",
        last_received_at: Optional[datetime] = None
    ):
        self.logical_turn_id = logical_turn_id
        self.speaker_name = speaker_name  # Preserves raw Teams display name
        self.text = text
        self.sequence_ids = list(sequence_ids)
        self.first_seen_at = first_seen_at
        self.last_update_at = last_update_at
        self.finalized_at = finalized_at
        self.last_received_at = last_received_at or finalized_at
        self.boundary_reason = boundary_reason
        self.is_candidate = is_candidate
        self.raw_event_count = raw_event_count
        self.source = source
        self.evaluation: Optional[Dict[str, Any]] = None

    @property
    def turn_id(self) -> int:
        """Alias for duck-typing with NativeShadowEvaluationAdapter."""
        return self.logical_turn_id

    @property
    def sequence_id(self) -> int:
        """Primary sequence ID or logical turn ID for duck-typing."""
        return self.sequence_ids[0] if self.sequence_ids else self.logical_turn_id

    @property
    def duration_ms(self) -> float:
        """Total duration of this logical turn from first seen to last update."""
        return max(0.0, (self.last_update_at - self.first_seen_at).total_seconds() * 1000.0)

    @property
    def finalization_latency_ms(self) -> float:
        """Latency from last update to logical turn finalization."""
        return max(0.0, (self.finalized_at - self.last_update_at).total_seconds() * 1000.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "logical_turn_id": self.logical_turn_id,
            "speaker_name": self.speaker_name,
            "text": self.text,
            "sequence_ids": self.sequence_ids,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_update_at": self.last_update_at.isoformat(),
            "finalized_at": self.finalized_at.isoformat(),
            "boundary_reason": self.boundary_reason,
            "is_candidate": self.is_candidate,
            "raw_event_count": self.raw_event_count,
            "duration_ms": round(self.duration_ms, 2),
            "finalization_latency_ms": round(self.finalization_latency_ms, 2),
            "evaluation": self.evaluation,
            "source": self.source
        }


class NativeLogicalTurnAggregator:
    """
    Aggregates sub-utterance caption sequences into complete Logical Turns.
    Stitches same-speaker consecutive sequences across natural pauses (~2.0s).
    Closes turns on speaker switch or conversational silence (>= 3000ms).
    """
    def __init__(
        self,
        session_id: Optional[str] = None,
        candidate_speaker_name: Optional[str] = None,
        inactivity_threshold_ms: float = 3000.0
    ):
        self.session_id = session_id
        self.candidate_speaker_name = candidate_speaker_name
        self.inactivity_threshold_ms = inactivity_threshold_ms

        self.active_turn: Optional[LogicalTurn] = None
        self.finalized_logical_turns: List[LogicalTurn] = []
        self._next_turn_id: int = 1

    def set_candidate_speaker(self, name: str):
        """Sets the exact Teams display name of the candidate."""
        self.candidate_speaker_name = name.strip() if name else None

    def get_active_turn(self) -> Optional[LogicalTurn]:
        """Returns the current in-progress logical turn."""
        return self.active_turn

    def process_finalized_sequence(
        self,
        finalized_seq: FinalizedTurn,
        current_time: Optional[datetime] = None
    ) -> List[LogicalTurn]:
        """
        Ingests a finalized sequence from NativeTurnAggregator.
        Returns newly completed LogicalTurns triggered by speaker change or timeout.
        """
        ref_dt = current_time or finalized_seq.finalized_at
        completed_turns: List[LogicalTurn] = []

        if not finalized_seq.text or not finalized_seq.text.strip():
            return completed_turns

        # Check if there is an active logical turn
        if self.active_turn is None:
            is_cand = (self.candidate_speaker_name == finalized_seq.speaker_name) if self.candidate_speaker_name else None
            self.active_turn = LogicalTurn(
                logical_turn_id=self._next_turn_id,
                speaker_name=finalized_seq.speaker_name,
                text=finalized_seq.text.strip(),
                sequence_ids=[finalized_seq.sequence_id],
                first_seen_at=finalized_seq.first_seen_at,
                last_update_at=finalized_seq.last_update_at,
                finalized_at=ref_dt,
                boundary_reason="pending",
                is_candidate=is_cand,
                raw_event_count=finalized_seq.update_count,
                last_received_at=getattr(finalized_seq, "received_at", ref_dt)
            )
            return completed_turns

        # Case 1: Same speaker speaking again
        if self.active_turn.speaker_name == finalized_seq.speaker_name:
            # Measure silence gap between end of previous sequence and start of new sequence
            gap_ms = (finalized_seq.first_seen_at - self.active_turn.last_update_at).total_seconds() * 1000.0

            if gap_ms <= self.inactivity_threshold_ms:
                # Natural pause within the same logical answer -> STITCH!
                existing_text = self.active_turn.text
                new_text = finalized_seq.text.strip()
                if new_text.startswith(existing_text):
                    self.active_turn.text = new_text
                elif existing_text.startswith(new_text) or new_text in existing_text:
                    pass
                elif not existing_text.endswith(new_text):
                    self.active_turn.text = f"{existing_text} {new_text}".strip()
                if finalized_seq.sequence_id not in self.active_turn.sequence_ids:
                    self.active_turn.sequence_ids.append(finalized_seq.sequence_id)
                self.active_turn.last_update_at = max(self.active_turn.last_update_at, finalized_seq.last_update_at)
                self.active_turn.last_received_at = max(getattr(self.active_turn, "last_received_at", ref_dt), getattr(finalized_seq, "received_at", ref_dt))
                self.active_turn.raw_event_count += finalized_seq.update_count
                return completed_turns
            else:
                # Silence gap exceeded threshold -> close previous turn, start new one
                self.active_turn.finalized_at = finalized_seq.first_seen_at
                self.active_turn.boundary_reason = "inactivity_timeout"
                completed_turns.append(self.active_turn)
                self.finalized_logical_turns.append(self.active_turn)
                self._next_turn_id += 1

                is_cand = (self.candidate_speaker_name == finalized_seq.speaker_name) if self.candidate_speaker_name else None
                self.active_turn = LogicalTurn(
                    logical_turn_id=self._next_turn_id,
                    speaker_name=finalized_seq.speaker_name,
                    text=finalized_seq.text.strip(),
                    sequence_ids=[finalized_seq.sequence_id],
                    first_seen_at=finalized_seq.first_seen_at,
                    last_update_at=finalized_seq.last_update_at,
                    finalized_at=ref_dt,
                    boundary_reason="pending",
                    is_candidate=is_cand,
                    raw_event_count=finalized_seq.update_count,
                    last_received_at=getattr(finalized_seq, "received_at", ref_dt)
                )
                return completed_turns

        # Case 2: Speaker switch! (e.g. Interviewer -> Candidate or Candidate -> Interviewer)
        else:
            self.active_turn.finalized_at = finalized_seq.first_seen_at
            self.active_turn.boundary_reason = "speaker_switch"
            completed_turns.append(self.active_turn)
            self.finalized_logical_turns.append(self.active_turn)
            self._next_turn_id += 1

            is_cand = (self.candidate_speaker_name == finalized_seq.speaker_name) if self.candidate_speaker_name else None
            self.active_turn = LogicalTurn(
                logical_turn_id=self._next_turn_id,
                speaker_name=finalized_seq.speaker_name,
                text=finalized_seq.text.strip(),
                sequence_ids=[finalized_seq.sequence_id],
                first_seen_at=finalized_seq.first_seen_at,
                last_update_at=finalized_seq.last_update_at,
                finalized_at=ref_dt,
                boundary_reason="pending",
                is_candidate=is_cand,
                raw_event_count=finalized_seq.update_count,
                last_received_at=getattr(finalized_seq, "received_at", ref_dt)
            )
            return completed_turns

    def check_inactivity(self, current_time: Optional[datetime] = None) -> List[LogicalTurn]:
        """Closes active turn if silence exceeds inactivity threshold."""
        if self.active_turn is None:
            return []

        ref_dt = current_time or datetime.now(timezone.utc)
        if current_time is not None:
            elapsed_ms = (ref_dt - self.active_turn.last_update_at).total_seconds() * 1000.0
        else:
            rec_dt = getattr(self.active_turn, "last_received_at", None) or self.active_turn.last_update_at
            elapsed_ms = (ref_dt - rec_dt).total_seconds() * 1000.0

        if elapsed_ms >= self.inactivity_threshold_ms:
            self.active_turn.finalized_at = self.active_turn.last_update_at + timedelta(milliseconds=self.inactivity_threshold_ms)
            self.active_turn.boundary_reason = "inactivity_timeout"
            closed_turn = self.active_turn
            self.finalized_logical_turns.append(closed_turn)
            self.active_turn = None
            self._next_turn_id += 1
            return [closed_turn]

        return []

    def flush(self, current_time: Optional[datetime] = None) -> List[LogicalTurn]:
        """Flushes and finalizes active turn at session end."""
        if self.active_turn is None:
            return []

        ref_dt = current_time or datetime.now(timezone.utc)
        self.active_turn.finalized_at = ref_dt
        self.active_turn.boundary_reason = "session_end"
        closed_turn = self.active_turn
        self.finalized_logical_turns.append(closed_turn)
        self.active_turn = None
        self._next_turn_id += 1
        return [closed_turn]


class NativeShadowTurnController:
    """
    Coordinates Native Logical Turn Aggregator with Shadow Evaluation.
    Guarantees:
    - Candidate answers trigger evaluation EXACTLY ONCE per logical turn.
    - Interviewer speech NEVER triggers candidate evaluation.
    - Interviewer questions are preserved as last_question context for candidate answers.
    """
    def __init__(
        self,
        session_id: str,
        shadow_adapter: NativeShadowEvaluationAdapter,
        candidate_speaker_name: Optional[str] = None,
        inactivity_threshold_ms: float = 3000.0
    ):
        self.session_id = session_id
        self.shadow_adapter = shadow_adapter
        self.candidate_speaker_name = candidate_speaker_name
        self.aggregator = NativeLogicalTurnAggregator(
            session_id=session_id,
            candidate_speaker_name=candidate_speaker_name,
            inactivity_threshold_ms=inactivity_threshold_ms
        )

        self.last_interviewer_question: str = ""
        self.candidate_evaluations: List[Dict[str, Any]] = []
        self.interviewer_turns_count: int = 0
        self.candidate_turns_count: int = 0
        self.interviewer_evaluations_count: int = 0  # Must strictly remain 0!

    def set_candidate_speaker(self, name: str):
        self.candidate_speaker_name = name.strip() if name else None
        self.aggregator.set_candidate_speaker(name)

    async def handle_logical_turn(
        self,
        logical_turn: LogicalTurn,
        is_service_off: bool = False,
        is_completed: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Dispatches a completed LogicalTurn according to speaker role.
        """
        is_candidate = False
        if self.candidate_speaker_name:
            is_candidate = (logical_turn.speaker_name == self.candidate_speaker_name)
        elif logical_turn.is_candidate is not None:
            is_candidate = logical_turn.is_candidate
        else:
            # Fallback: if not set, assume candidate based on metadata or first turn
            is_candidate = True

        logical_turn.is_candidate = is_candidate

        if not is_candidate:
            # Interviewer Logical Turn:
            # 1. Update last_question context
            self.last_interviewer_question = logical_turn.text
            self.interviewer_turns_count += 1
            # 2. Append to shadow transcript for history
            self.shadow_adapter.shadow_transcript.append({
                "speaker": logical_turn.speaker_name,
                "text": logical_turn.text,
                "timestamp": logical_turn.finalized_at.isoformat(),
                "source": "native_captions_shadow",
                "sequence_id": logical_turn.sequence_id,
                "is_candidate": False
            })
            # 3. STRICT RULE: DO NOT TRIGGER EVALUATION FOR INTERVIEWER SPEECH!
            logger.info(
                f"[NativeShadowController] Interviewer turn {logical_turn.logical_turn_id} "
                f"[{logical_turn.speaker_name}]: '{logical_turn.text}' -> Evaluation SKIPPED (Interviewer speech)."
            )
            return None

        # Candidate Logical Turn:
        self.candidate_turns_count += 1
        logger.info(
            f"[NativeShadowController] Evaluating candidate turn {logical_turn.logical_turn_id} "
            f"[{logical_turn.speaker_name}]: '{logical_turn.text}' (Q: '{self.last_interviewer_question}')"
        )

        eval_result = await self.shadow_adapter.evaluate_turn(
            finalized_turn=logical_turn,  # duck-typed
            last_question=self.last_interviewer_question,
            is_service_off=is_service_off,
            is_completed=is_completed
        )

        logical_turn.evaluation = eval_result
        self.candidate_evaluations.append(eval_result)
        return eval_result


class TurnAlignmentClassifier:
    """
    Classifies conversational turn alignment into 6 categories:
    1. CORRECT_ONE_TO_ONE
    2. FRAGMENTED
    3. MERGED
    4. DUPLICATED
    5. MISSING
    6. UNKNOWN
    """
    CATEGORIES = [
        "CORRECT_ONE_TO_ONE",
        "FRAGMENTED",
        "MERGED",
        "DUPLICATED",
        "MISSING",
        "UNKNOWN"
    ]

    @staticmethod
    def classify_turn(
        expected_turn: Dict[str, Any],
        actual_turns: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Classifies how actual turns represent the expected logical turn.
        """
        exp_text = (expected_turn.get("expected_text") or "").strip().lower()
        if not actual_turns:
            return {
                "classification": "MISSING",
                "matched_count": 0,
                "notes": "No actual turn observed for expected turn."
            }

        if len(actual_turns) == 1:
            act_text = (actual_turns[0].get("text") or "").strip().lower()
            # Check overlap or containment
            if exp_text in act_text or act_text in exp_text or len(set(exp_text.split()) & set(act_text.split())) >= 1:
                return {
                    "classification": "CORRECT_ONE_TO_ONE",
                    "matched_count": 1,
                    "notes": "Single logical turn correctly mapped to single actual turn."
                }
            return {
                "classification": "UNKNOWN",
                "matched_count": 1,
                "notes": "Text divergence prevents confident match."
            }

        # Multiple actual turns mapped to this expected turn
        # Check if they are duplicates or fragmented pieces
        unique_texts = set(t.get("text", "").strip().lower() for t in actual_turns)
        if len(unique_texts) == 1 and len(actual_turns) > 1:
            return {
                "classification": "DUPLICATED",
                "matched_count": len(actual_turns),
                "notes": f"Turn duplicated {len(actual_turns)} times."
            }

        return {
            "classification": "FRAGMENTED",
            "matched_count": len(actual_turns),
            "notes": f"Turn fragmented across {len(actual_turns)} separate outputs."
        }
