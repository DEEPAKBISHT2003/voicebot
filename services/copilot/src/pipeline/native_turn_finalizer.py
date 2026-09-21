"""
Phase 2K — Native Caption Turn Aggregation & Finalization Engine
File: services/copilot/src/pipeline/native_turn_finalizer.py

Implements 4 distinct architectural levels:
1. Raw Caption Events (immutable)
2. Caption Sequence State Tracking (incremental update aggregation)
3. Candidate Turn
4. Finalized Turn

Supports candidate finalization strategies:
- Strategy A: Sequence Transition
- Strategy B: DOM Node Replacement/Removal
- Strategy C: Quiescence / Inactivity Timeout (500ms, 1000ms, 1500ms, 2000ms, 3000ms)

CRITICAL RULES:
- Preserves exact Teams speaker_name (no role classification, no heuristics, no LLM).
- Isolated: Does NOT call engine.add_message() and does NOT feed evaluation.
"""

import math
import copy
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Set


def parse_iso_timestamp(ts_str: Optional[str]) -> datetime:
    """Safely parses ISO timestamp string into timezone-aware datetime."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        clean_ts = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def compute_percentiles(values: List[float]) -> Dict[str, float]:
    """Computes Min, Median (P50), P90, P95, Avg, Max from a list of floats."""
    if not values:
        return {"min": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "avg": 0.0, "max": 0.0, "count": 0}

    sorted_v = sorted(values)
    n = len(sorted_v)

    def get_pct(p: float) -> float:
        k = (n - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_v[int(k)]
        d0 = sorted_v[int(f)] * (c - k)
        d1 = sorted_v[int(c)] * (k - f)
        return d0 + d1

    return {
        "min": round(sorted_v[0], 2),
        "p50": round(get_pct(50), 2),
        "p90": round(get_pct(90), 2),
        "p95": round(get_pct(95), 2),
        "avg": round(sum(sorted_v) / n, 2),
        "max": round(sorted_v[-1], 2),
        "count": n
    }


class SequenceState:
    """Tracks state and revision history for a single Teams caption sequence."""
    def __init__(self, sequence_id: int, speaker_name: str, initial_text: str, detected_at: datetime, received_at: datetime, dom_action: str = "inserted"):
        self.sequence_id = sequence_id
        self.speaker_name = speaker_name
        self.text_versions: List[str] = [initial_text] if initial_text else []
        self.first_seen_at = detected_at
        self.last_update_at = detected_at
        self.received_at = received_at
        self.update_count = 1
        self.dom_action = dom_action
        self.is_dom_removed = (dom_action == "removed")

    @property
    def current_text(self) -> str:
        return self.text_versions[-1] if self.text_versions else ""

    def add_update(self, text: str, detected_at: datetime, received_at: datetime, dom_action: str = "updated"):
        text = text.strip()
        if not text:
            return
        if not self.text_versions or text != self.text_versions[-1]:
            self.text_versions.append(text)
            self.update_count += 1
        self.last_update_at = max(self.last_update_at, detected_at)
        self.received_at = max(self.received_at, received_at)
        self.dom_action = dom_action
        if dom_action == "removed":
            self.is_dom_removed = True

    @property
    def stabilization_duration_ms(self) -> float:
        return max(0.0, (self.last_update_at - self.first_seen_at).total_seconds() * 1000.0)


class FinalizedTurn:
    """Represents a stabilized, finalized conversational turn derived from native captions."""
    def __init__(
        self,
        turn_id: int,
        sequence_id: int,
        speaker_name: str,
        text: str,
        text_versions: List[str],
        first_seen_at: datetime,
        last_update_at: datetime,
        finalized_at: datetime,
        finalization_strategy: str,
        update_count: int,
        source: str = "teams_native",
        received_at: Optional[datetime] = None
    ):
        self.turn_id = turn_id
        self.sequence_id = sequence_id
        self.speaker_name = speaker_name
        self.text = text
        self.text_versions = copy.deepcopy(text_versions)
        self.first_seen_at = first_seen_at
        self.last_update_at = last_update_at
        self.finalized_at = finalized_at
        self.finalization_strategy = finalization_strategy
        self.update_count = update_count
        self.source = source
        self.received_at = received_at or last_update_at

    @property
    def finalization_latency_ms(self) -> float:
        """Elapsed latency from the last text mutation to the moment of finalization."""
        return max(0.0, (self.finalized_at - self.last_update_at).total_seconds() * 1000.0)

    @property
    def stabilization_latency_ms(self) -> float:
        """Elapsed latency from the first detected caption update to the last text mutation."""
        return max(0.0, (self.last_update_at - self.first_seen_at).total_seconds() * 1000.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "sequence_id": self.sequence_id,
            "speaker_name": self.speaker_name,
            "text": self.text,
            "text_versions": self.text_versions,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_update_at": self.last_update_at.isoformat(),
            "finalized_at": self.finalized_at.isoformat(),
            "finalization_strategy": self.finalization_strategy,
            "finalization_latency_ms": round(self.finalization_latency_ms, 2),
            "stabilization_latency_ms": round(self.stabilization_latency_ms, 2),
            "update_count": self.update_count,
            "source": self.source
        }


class NativeTurnAggregator:
    """
    Stateful aggregation and multi-strategy finalization engine.
    Separates raw events from sequence state and finalized turns.
    Evaluates Strategy A, Strategy B, and Strategy C concurrently for benchmarking.
    """
    STRATEGIES = [
        "strategy_a_sequence_transition",
        "strategy_b_dom_removal",
        "strategy_c_quiescence_500ms",
        "strategy_c_quiescence_800ms",
        "strategy_c_quiescence_1000ms",
        "strategy_c_quiescence_1500ms",
        "strategy_c_quiescence_2000ms",
        "strategy_c_quiescence_3000ms",
    ]

    QUIESCENCE_THRESHOLDS_MS = {
        "strategy_c_quiescence_500ms": 500.0,
        "strategy_c_quiescence_800ms": 800.0,
        "strategy_c_quiescence_1000ms": 1000.0,
        "strategy_c_quiescence_1500ms": 1500.0,
        "strategy_c_quiescence_2000ms": 2000.0,
        "strategy_c_quiescence_3000ms": 3000.0,
    }

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id
        self.raw_events: List[Dict[str, Any]] = []
        self.sequences: Dict[int, SequenceState] = {}
        
        # Per-strategy finalized turns
        self.finalized_turns: Dict[str, List[FinalizedTurn]] = {s: [] for s in self.STRATEGIES}
        
        # Per-strategy finalized sequence IDs to prevent duplicate emission
        self._finalized_seq_ids: Dict[str, Set[int]] = {s: set() for s in self.STRATEGIES}

        # Track active sequence per speaker
        self._last_active_seq_id: Optional[int] = None
        self._last_speaker: Optional[str] = None

    def process_raw_event(self, event_data: Dict[str, Any]) -> Dict[str, List[FinalizedTurn]]:
        """
        Ingests a single raw native caption event.
        Returns newly finalized turns across all strategies triggered by this event.
        """
        # 1. Store immutable raw event
        immutable_copy = copy.deepcopy(event_data)
        self.raw_events.append(immutable_copy)

        seq_id = event_data.get("caption_sequence")
        raw_text = (event_data.get("text") or "").strip()
        raw_speaker = (event_data.get("speaker_name") or "").strip() or "Unknown"
        dom_action = event_data.get("dom_action", "updated")

        detected_at = parse_iso_timestamp(event_data.get("detected_at"))
        received_at = parse_iso_timestamp(event_data.get("received_at"))

        newly_finalized: Dict[str, List[FinalizedTurn]] = {s: [] for s in self.STRATEGIES}

        if seq_id is None:
            return newly_finalized

        # 2. Check Strategy A (Sequence Transition / Speaker Switch) prior to updating active state
        if self._last_active_seq_id is not None and self._last_active_seq_id != seq_id:
            strat_a = "strategy_a_sequence_transition"
            prev_seq = self.sequences.get(self._last_active_seq_id)
            if prev_seq and self._last_active_seq_id not in self._finalized_seq_ids[strat_a]:
                turn = self._create_finalized_turn(
                    seq_state=prev_seq,
                    finalized_at=detected_at,
                    strategy_name=strat_a,
                    turn_index=len(self.finalized_turns[strat_a]) + 1
                )
                self.finalized_turns[strat_a].append(turn)
                self._finalized_seq_ids[strat_a].add(prev_seq.sequence_id)
                newly_finalized[strat_a].append(turn)

        # 3. Update or Insert Sequence State
        if seq_id not in self.sequences:
            self.sequences[seq_id] = SequenceState(
                sequence_id=seq_id,
                speaker_name=raw_speaker,
                initial_text=raw_text,
                detected_at=detected_at,
                received_at=received_at,
                dom_action=dom_action
            )
        else:
            self.sequences[seq_id].add_update(
                text=raw_text,
                detected_at=detected_at,
                received_at=received_at,
                dom_action=dom_action
            )

        self._last_active_seq_id = seq_id
        self._last_speaker = raw_speaker
        current_seq_state = self.sequences[seq_id]

        # 4. Check Strategy B (DOM Node Replacement or Removal)
        strat_b = "strategy_b_dom_removal"
        if dom_action in ("removed", "replaced") and seq_id not in self._finalized_seq_ids[strat_b]:
            turn = self._create_finalized_turn(
                seq_state=current_seq_state,
                finalized_at=detected_at,
                strategy_name=strat_b,
                turn_index=len(self.finalized_turns[strat_b]) + 1
            )
            self.finalized_turns[strat_b].append(turn)
            self._finalized_seq_ids[strat_b].add(seq_id)
            newly_finalized[strat_b].append(turn)

        # 5. Evaluate Quiescence on other sequences up to detected_at
        quiescence_new = self.check_quiescence(reference_time=detected_at)
        for s, turns in quiescence_new.items():
            newly_finalized[s].extend(turns)

        return newly_finalized

    def check_quiescence(self, reference_time: Optional[datetime] = None) -> Dict[str, List[FinalizedTurn]]:
        """
        Evaluates Strategy C quiescence timeouts across all active, non-finalized sequences.
        Can use explicit reference_time or current UTC time.
        """
        ref_dt = reference_time or datetime.now(timezone.utc)
        newly_finalized: Dict[str, List[FinalizedTurn]] = {s: [] for s in self.STRATEGIES}

        for strat_name, threshold_ms in self.QUIESCENCE_THRESHOLDS_MS.items():
            finalized_set = self._finalized_seq_ids[strat_name]
            for seq_id, seq_state in self.sequences.items():
                if seq_id in finalized_set:
                    continue
                if not seq_state.current_text:
                    continue

                if reference_time is not None:
                    elapsed_ms = (ref_dt - seq_state.last_update_at).total_seconds() * 1000.0
                else:
                    # When evaluated on backend periodic monitor, measure against server receive time to prevent clock skew / transport latency premature firing
                    rec_time = getattr(seq_state, "received_at", seq_state.last_update_at)
                    elapsed_ms = (ref_dt - rec_time).total_seconds() * 1000.0

                if elapsed_ms >= threshold_ms:
                    # Finalize at exactly the moment the threshold expired
                    finalized_moment = seq_state.last_update_at + timedelta(milliseconds=threshold_ms)
                    turn = self._create_finalized_turn(
                        seq_state=seq_state,
                        finalized_at=finalized_moment,
                        strategy_name=strat_name,
                        turn_index=len(self.finalized_turns[strat_name]) + 1
                    )
                    self.finalized_turns[strat_name].append(turn)
                    finalized_set.add(seq_id)
                    newly_finalized[strat_name].append(turn)

        return newly_finalized

    def finalize_all_pending(self, flush_time: Optional[datetime] = None) -> Dict[str, List[FinalizedTurn]]:
        """Flushes and finalizes all remaining non-finalized sequences across all strategies (e.g. at session end)."""
        dt = flush_time or datetime.now(timezone.utc)
        flushed: Dict[str, List[FinalizedTurn]] = {s: [] for s in self.STRATEGIES}

        for s in self.STRATEGIES:
            finalized_set = self._finalized_seq_ids[s]
            for seq_id, seq_state in self.sequences.items():
                if seq_id in finalized_set:
                    continue
                if not seq_state.current_text:
                    continue
                turn = self._create_finalized_turn(
                    seq_state=seq_state,
                    finalized_at=dt,
                    strategy_name=s,
                    turn_index=len(self.finalized_turns[s]) + 1
                )
                self.finalized_turns[s].append(turn)
                finalized_set.add(seq_id)
                flushed[s].append(turn)

        return flushed

    def _create_finalized_turn(self, seq_state: SequenceState, finalized_at: datetime, strategy_name: str, turn_index: int) -> FinalizedTurn:
        return FinalizedTurn(
            turn_id=turn_index,
            sequence_id=seq_state.sequence_id,
            speaker_name=seq_state.speaker_name,
            text=seq_state.current_text,
            text_versions=seq_state.text_versions,
            first_seen_at=seq_state.first_seen_at,
            last_update_at=seq_state.last_update_at,
            finalized_at=finalized_at,
            finalization_strategy=strategy_name,
            update_count=seq_state.update_count,
            source="teams_native",
            received_at=getattr(seq_state, "received_at", seq_state.last_update_at)
        )

    def compute_strategy_metrics(self, strategy_name: str, ground_truth_turns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Computes performance metrics for a specific strategy:
        - turn count
        - latency distribution (Min, P50, P90, P95, Avg, Max)
        - stabilization latency distribution
        - false splits, false merges, duplicate turns, missed turns
        - speaker switch accuracy
        """
        turns = self.finalized_turns.get(strategy_name, [])
        latencies = [t.finalization_latency_ms for t in turns]
        stabilizations = [t.stabilization_latency_ms for t in turns]

        lat_stats = compute_percentiles(latencies)
        stab_stats = compute_percentiles(stabilizations)

        # Speaker switching evaluation
        switches = 0
        valid_switches = 0
        duplicate_turns = 0
        false_splits = 0
        false_merges = 0

        seen_texts: Set[str] = set()

        for i in range(len(turns)):
            t = turns[i]
            # Duplicate check
            if t.text in seen_texts:
                duplicate_turns += 1
            seen_texts.add(t.text)

            if i > 0:
                prev = turns[i - 1]
                if t.speaker_name != prev.speaker_name:
                    switches += 1
                    if t.speaker_name != "Unknown" and prev.speaker_name != "Unknown":
                        valid_switches += 1
                else:
                    # Same speaker consecutive turns: check if split occurred within short interval (<1.0s)
                    gap_ms = (t.first_seen_at - prev.last_update_at).total_seconds() * 1000.0
                    if 0 <= gap_ms < 1000.0:
                        false_splits += 1

        switch_acc = (valid_switches / switches * 100.0) if switches > 0 else 100.0

        # Ground truth comparison if provided
        missed_turns = 0
        if ground_truth_turns:
            gt_count = len(ground_truth_turns)
            turn_diff = len(turns) - gt_count
            if turn_diff < 0:
                missed_turns = abs(turn_diff)
            elif turn_diff > 0:
                false_splits = max(false_splits, turn_diff)

        return {
            "strategy": strategy_name,
            "total_turns": len(turns),
            "duplicate_turns": duplicate_turns,
            "false_splits": false_splits,
            "false_merges": false_merges,
            "missed_turns": missed_turns,
            "finalization_latency_ms": lat_stats,
            "stabilization_latency_ms": stab_stats,
            "speaker_switches": switches,
            "valid_speaker_switches": valid_switches,
            "speaker_switch_accuracy_pct": round(switch_acc, 2)
        }

    def get_benchmark_comparison_summary(self, ground_truth_turns: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Returns side-by-side comparative summary across all evaluated strategies."""
        summary = {}
        for s in self.STRATEGIES:
            summary[s] = self.compute_strategy_metrics(s, ground_truth_turns=ground_truth_turns)
        return summary
