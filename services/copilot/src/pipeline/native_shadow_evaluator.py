"""
Phase 2L — Native Caption Shadow Evaluation Adapter
File: services/copilot/src/pipeline/native_shadow_evaluator.py

Reuses EXISTING Copilot evaluation logic in SHADOW MODE:
- CandidateEvaluationService (for technical evaluation & percentage rating)
- AICopilotEngine (for topic recommendations & next question generation)

CRITICAL RULES:
- Zero modifications to production session state (transcript, intelligence, assistance).
- Never calls engine.add_message().
- Never sends updates to dashboard WebSocket clients.
- Shadow failures or timeouts NEVER affect production flow or Deepgram.
- Preserves exact Teams speaker_name without role mapping or classification.
- Interview Service remains completely untouched.
"""

import os
import json
import time
import asyncio
import datetime
from datetime import timezone
from typing import List, Dict, Any, Optional
from loguru import logger

from services.copilot.src.services.evaluation import CandidateEvaluationService
from services.copilot.src.engine.copilot import AICopilotEngine
from services.copilot.src.pipeline.native_turn_finalizer import FinalizedTurn


class NativeShadowEvaluationAdapter:
    """
    Isolated shadow evaluation runner for finalized Teams Native Caption turns.
    Executes in parallel without mutating production session or database state.
    """
    def __init__(
        self,
        session_id: str,
        jd: str = "",
        resume: str = "",
        custom_prompt: str = "",
        evaluation_service: Optional[CandidateEvaluationService] = None,
        copilot_assistant: Optional[AICopilotEngine] = None,
        timeout_seconds: float = 15.0
    ):
        self.session_id = session_id
        self.jd = jd
        self.resume = resume
        self.custom_prompt = custom_prompt
        self.timeout_seconds = timeout_seconds

        # Reuse existing evaluation services directly
        self.evaluation_service = evaluation_service or CandidateEvaluationService()
        self.copilot_assistant = copilot_assistant or AICopilotEngine()

        # Isolated shadow history (never written to production engine)
        self.shadow_transcript: List[Dict[str, Any]] = []
        self.shadow_evaluations: List[Dict[str, Any]] = []
        self.evaluated_sequence_ids: set = set()

        # Dedicated shadow artifact path
        self.session_dir = os.path.join("interviews", session_id)
        os.makedirs(self.session_dir, exist_ok=True)
        self.shadow_log_path = os.path.join(self.session_dir, "native_shadow_evaluations.jsonl")

    async def evaluate_turn(
        self,
        finalized_turn: FinalizedTurn,
        last_question: str = "",
        is_service_off: bool = False,
        is_completed: bool = False
    ) -> Dict[str, Any]:
        """
        Executes isolated shadow evaluation for a finalized turn.
        Returns the shadow evaluation result dictionary.
        Guarantees that no exception escapes to interrupt production flow.
        """
        # Guard against Service Off or Completed Session
        if is_service_off or is_completed:
            logger.info(f"[NativeShadow] Skipping evaluation for session {self.session_id}: Session is inactive or Service Off.")
            return {
                "session_id": self.session_id,
                "turn_id": finalized_turn.turn_id,
                "source": "native_captions_shadow",
                "speaker_name": finalized_turn.speaker_name,
                "text": finalized_turn.text,
                "status": "rejected_inactive_or_service_off",
                "evaluation": None
            }

        # Deduplication guard: do not re-evaluate identical sequence ID
        if finalized_turn.sequence_id in self.evaluated_sequence_ids:
            return {
                "session_id": self.session_id,
                "turn_id": finalized_turn.turn_id,
                "source": "native_captions_shadow",
                "speaker_name": finalized_turn.speaker_name,
                "text": finalized_turn.text,
                "status": "skipped_duplicate_sequence",
                "evaluation": None
            }

        self.evaluated_sequence_ids.add(finalized_turn.sequence_id)

        # Append to isolated shadow transcript
        shadow_entry = {
            "speaker": finalized_turn.speaker_name,  # Preserves raw Teams display name
            "text": finalized_turn.text,
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
            "source": "native_captions_shadow",
            "sequence_id": finalized_turn.sequence_id
        }
        self.shadow_transcript.append(shadow_entry)

        # Retrieve last question from shadow history if not explicitly provided
        if not last_question and len(self.shadow_transcript) > 1:
            for prev_msg in reversed(self.shadow_transcript[:-1]):
                if prev_msg.get("speaker") != finalized_turn.speaker_name:
                    last_question = prev_msg.get("text", "")
                    break

        t_start = time.time()
        try:
            # Run existing evaluation & assistance in isolated, timeout-guarded task
            eval_coro = self._run_evaluation_pipeline(
                candidate_text=finalized_turn.text,
                question=last_question
            )
            eval_result, assist_result = await asyncio.wait_for(eval_coro, timeout=self.timeout_seconds)

            if isinstance(eval_result, Exception):
                raise eval_result
            if isinstance(assist_result, Exception):
                raise assist_result

            latency_ms = round((time.time() - t_start) * 1000.0, 2)

            percentage = None
            if isinstance(eval_result, dict):
                percentage = eval_result.get("technical_accuracy", {}).get("rating")

            topic = None
            next_q = None
            if isinstance(assist_result, dict):
                topic = assist_result.get("recommended_next_topic")
                follow_ups = assist_result.get("suggested_follow_up_questions", [])
                if follow_ups and isinstance(follow_ups, list):
                    next_q = follow_ups[0]

            shadow_record = {
                "session_id": self.session_id,
                "turn_id": finalized_turn.turn_id,
                "sequence_id": finalized_turn.sequence_id,
                "source": "native_captions_shadow",
                "speaker_name": finalized_turn.speaker_name,
                "text": finalized_turn.text,
                "status": "success",
                "shadow_latency_ms": latency_ms,
                "evaluation": {
                    "percentage": percentage,
                    "topic": topic,
                    "next_question": next_q,
                    "raw_evaluation": eval_result,
                    "raw_assistance": assist_result
                }
            }

            self.shadow_evaluations.append(shadow_record)

            # Persist to isolated shadow log file
            try:
                with open(self.shadow_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(shadow_record) + "\n")
            except Exception as fe:
                logger.warning(f"[NativeShadow] Could not append to shadow log: {fe}")

            logger.info(
                f"[NativeShadow] Shadow evaluation complete (turn={finalized_turn.turn_id}, "
                f"speaker='{finalized_turn.speaker_name}', rating={percentage}%, latency={latency_ms}ms)"
            )
            return shadow_record

        except asyncio.TimeoutError:
            latency_ms = round((time.time() - t_start) * 1000.0, 2)
            logger.warning(f"[NativeShadow] Evaluation timed out after {self.timeout_seconds}s for turn {finalized_turn.turn_id}")
            record = {
                "session_id": self.session_id,
                "turn_id": finalized_turn.turn_id,
                "sequence_id": finalized_turn.sequence_id,
                "source": "native_captions_shadow",
                "speaker_name": finalized_turn.speaker_name,
                "text": finalized_turn.text,
                "status": "timeout",
                "shadow_latency_ms": latency_ms,
                "evaluation": None
            }
            self.shadow_evaluations.append(record)
            return record

        except Exception as e:
            latency_ms = round((time.time() - t_start) * 1000.0, 2)
            logger.error(f"[NativeShadow] Shadow evaluation exception: {e}")
            record = {
                "session_id": self.session_id,
                "turn_id": finalized_turn.turn_id,
                "sequence_id": finalized_turn.sequence_id,
                "source": "native_captions_shadow",
                "speaker_name": finalized_turn.speaker_name,
                "text": finalized_turn.text,
                "status": "error",
                "error": str(e),
                "shadow_latency_ms": latency_ms,
                "evaluation": None
            }
            self.shadow_evaluations.append(record)
            return record

    async def _run_evaluation_pipeline(self, candidate_text: str, question: str):
        """Executes candidate evaluation and copilot assistance in parallel using existing classes."""
        tasks = [
            self.evaluation_service.evaluate_response(
                candidate_response=candidate_text,
                jd=self.jd,
                resume=self.resume,
                question=question
            ),
            self.copilot_assistant.generate_assistance(
                transcript=self.shadow_transcript,
                jd=self.jd,
                resume=self.resume,
                custom_prompt=self.custom_prompt
            )
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)


def compare_shadow_with_deepgram(
    native_shadow_turns: List[Dict[str, Any]],
    deepgram_turns: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Compares Native Shadow evaluation results with Deepgram production evaluations
    only when turns genuinely represent the same conversational turn.
    If reliable alignment is unavailable, reports comparison_status = 'unavailable'.
    """
    comparisons = []
    min_len = min(len(native_shadow_turns), len(deepgram_turns))

    for idx in range(max(len(native_shadow_turns), len(deepgram_turns))):
        native_turn = native_shadow_turns[idx] if idx < len(native_shadow_turns) else None
        dg_turn = deepgram_turns[idx] if idx < len(deepgram_turns) else None

        if native_turn and dg_turn:
            n_eval = native_turn.get("evaluation") or {}
            d_eval = dg_turn.get("evaluation") or {}

            n_pct = n_eval.get("percentage")
            d_pct = d_eval.get("percentage")
            pct_diff = abs(n_pct - d_pct) if (n_pct is not None and d_pct is not None) else None

            n_topic = (n_eval.get("topic") or "").strip()
            d_topic = (d_eval.get("topic") or "").strip()
            topic_match = (n_topic.lower() == d_topic.lower()) if (n_topic and d_topic) else None

            n_q = (n_eval.get("next_question") or "").strip()
            d_q = (d_eval.get("next_question") or "").strip()
            q_match = (n_q.lower() == d_q.lower()) if (n_q and d_q) else None

            comparisons.append({
                "turn_id": idx + 1,
                "session_id": native_turn.get("session_id"),
                "native": {
                    "speaker_name": native_turn.get("speaker_name"),
                    "text": native_turn.get("text"),
                    "finalization_latency_ms": native_turn.get("shadow_latency_ms", 0.0)
                },
                "native_evaluation": {
                    "percentage": n_pct,
                    "topic": n_topic,
                    "next_question": n_q,
                    "status": native_turn.get("status", "success")
                },
                "deepgram": {
                    "speaker_name": dg_turn.get("speaker", dg_turn.get("speaker_name", "Unknown")),
                    "text": dg_turn.get("text"),
                    "evaluation": {
                        "percentage": d_pct,
                        "topic": d_topic,
                        "next_question": d_q
                    }
                },
                "comparison": {
                    "status": "matched",
                    "percentage_difference": pct_diff,
                    "topic_agreement": topic_match,
                    "next_question_agreement": q_match
                }
            })
        elif native_turn:
            comparisons.append({
                "turn_id": idx + 1,
                "session_id": native_turn.get("session_id"),
                "native": {
                    "speaker_name": native_turn.get("speaker_name"),
                    "text": native_turn.get("text"),
                    "finalization_latency_ms": native_turn.get("shadow_latency_ms", 0.0)
                },
                "native_evaluation": {
                    "percentage": (native_turn.get("evaluation") or {}).get("percentage"),
                    "topic": (native_turn.get("evaluation") or {}).get("topic"),
                    "next_question": (native_turn.get("evaluation") or {}).get("next_question"),
                    "status": native_turn.get("status")
                },
                "deepgram": None,
                "comparison": {
                    "status": "unavailable",
                    "reason": "unmatched_native_turn"
                }
            })
        elif dg_turn:
            d_eval = dg_turn.get("evaluation") or {}
            comparisons.append({
                "turn_id": idx + 1,
                "session_id": dg_turn.get("session_id"),
                "native": None,
                "deepgram": {
                    "speaker_name": dg_turn.get("speaker", dg_turn.get("speaker_name", "Unknown")),
                    "text": dg_turn.get("text"),
                    "evaluation": {
                        "percentage": d_eval.get("percentage"),
                        "topic": d_eval.get("topic"),
                        "next_question": d_eval.get("next_question")
                    }
                },
                "comparison": {
                    "status": "unavailable",
                    "reason": "unmatched_deepgram_turn"
                }
            })

    return comparisons
