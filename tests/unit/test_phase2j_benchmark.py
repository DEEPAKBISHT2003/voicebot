"""
Unit Tests for Phase 2J Two-Speaker Benchmark Analytics and Turn Aggregation.
File: tests/unit/test_phase2j_benchmark.py
"""

import math
import pytest
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any


def compute_percentiles(values: List[float]) -> Dict[str, float]:
    """Computes Min, Median (P50), P90, P95, Avg, Max from a list of float latencies."""
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


def aggregate_raw_events_to_turns(raw_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aggregates rolling native caption events into distinct conversation turns
    grouped by caption_sequence and contiguous speaker blocks.
    """
    turns: List[Dict[str, Any]] = []
    current_seq_events: Dict[int, List[Dict[str, Any]]] = {}

    for evt in raw_events:
        seq = evt.get("caption_sequence")
        if seq is None:
            continue
        current_seq_events.setdefault(seq, []).append(evt)

    for seq in sorted(current_seq_events.keys()):
        evts = current_seq_events[seq]
        speaker = evts[0].get("speaker_name", "Unknown")
        text_versions = [e.get("text", "").strip() for e in evts if e.get("text")]
        
        # Deduplicate consecutive identical text versions
        dedup_texts = []
        for t in text_versions:
            if not dedup_texts or t != dedup_texts[-1]:
                dedup_texts.append(t)
                
        final_text = dedup_texts[-1] if dedup_texts else ""
        first_seen = evts[0].get("detected_at")
        last_update = evts[-1].get("detected_at")
        
        # Stabilization duration: elapsed time between first seen and final update in sequence
        try:
            t0 = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
            stabilization_ms = max(0.0, (t1 - t0).total_seconds() * 1000.0)
        except Exception:
            stabilization_ms = 0.0

        turns.append({
            "turn_id": len(turns) + 1,
            "caption_sequence": seq,
            "speaker_name": speaker,
            "text_versions": dedup_texts,
            "final_text": final_text,
            "update_count": len(evts),
            "first_seen_at": first_seen,
            "last_update_at": last_update,
            "stabilization_ms": round(stabilization_ms, 2),
            "finality": "benchmark_derived"
        })

    return turns


def evaluate_speaker_switching(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluates speaker switching dynamics and accuracy across aggregated turns."""
    if not turns:
        return {
            "total_turns": 0,
            "distinct_speakers": [],
            "speaker_switches": 0,
            "switches_valid": 0,
            "switch_accuracy_pct": 0.0
        }

    distinct_speakers = list(dict.fromkeys(t["speaker_name"] for t in turns if t["speaker_name"] != "Unknown"))
    switches = 0
    valid_switches = 0

    for i in range(1, len(turns)):
        prev_spk = turns[i - 1]["speaker_name"]
        curr_spk = turns[i]["speaker_name"]
        if curr_spk != prev_spk:
            switches += 1
            if curr_spk != "Unknown" and prev_spk != "Unknown":
                valid_switches += 1

    accuracy = (valid_switches / switches * 100.0) if switches > 0 else 100.0

    return {
        "total_turns": len(turns),
        "distinct_speakers": distinct_speakers,
        "speaker_switches": switches,
        "switches_valid": valid_switches,
        "switch_accuracy_pct": round(accuracy, 2)
    }


def compute_wer(reference: str, hypothesis: str) -> float:
    """Computes standard Word Error Rate (Levenshtein distance on word tokens)."""
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    d = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = min(
                    d[i - 1][j] + 1,      # deletion
                    d[i][j - 1] + 1,      # insertion
                    d[i - 1][j - 1] + 1   # substitution
                )

    return min(1.0, d[len(ref_words)][len(hyp_words)] / len(ref_words))


# -------------------------------------------------------------
# Unit Tests
# -------------------------------------------------------------

def test_percentile_computation():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    stats = compute_percentiles(latencies)
    assert stats["min"] == 10.0
    assert stats["p50"] == 55.0
    assert stats["p90"] == 91.0
    assert stats["p95"] == 95.5
    assert stats["avg"] == 55.0
    assert stats["max"] == 100.0


def test_percentile_empty():
    stats = compute_percentiles([])
    assert stats["min"] == 0.0
    assert stats["count"] == 0


def test_aggregate_raw_events_to_turns():
    now = datetime.now(timezone.utc)
    t0 = now.isoformat()
    t1 = (now + timedelta(milliseconds=200)).isoformat()
    t2 = (now + timedelta(milliseconds=500)).isoformat()
    t3 = (now + timedelta(milliseconds=1200)).isoformat()

    events = [
        {"caption_sequence": 1, "speaker_name": "Deepak Bisht", "text": "Can you", "detected_at": t0},
        {"caption_sequence": 1, "speaker_name": "Deepak Bisht", "text": "Can you explain", "detected_at": t1},
        {"caption_sequence": 1, "speaker_name": "Deepak Bisht", "text": "Can you explain Python?", "detected_at": t2},
        {"caption_sequence": 2, "speaker_name": "Candidate User", "text": "I have 5 years of experience", "detected_at": t3},
    ]

    turns = aggregate_raw_events_to_turns(events)
    assert len(turns) == 2
    assert turns[0]["turn_id"] == 1
    assert turns[0]["speaker_name"] == "Deepak Bisht"
    assert turns[0]["final_text"] == "Can you explain Python?"
    assert turns[0]["update_count"] == 3
    assert turns[0]["stabilization_ms"] == 500.0
    assert turns[0]["finality"] == "benchmark_derived"

    assert turns[1]["turn_id"] == 2
    assert turns[1]["speaker_name"] == "Candidate User"
    assert turns[1]["final_text"] == "I have 5 years of experience"
    assert turns[1]["update_count"] == 1
    assert turns[1]["stabilization_ms"] == 0.0


def test_speaker_switching_evaluation():
    turns = [
        {"turn_id": 1, "speaker_name": "Deepak Bisht"},
        {"turn_id": 2, "speaker_name": "Candidate User"},
        {"turn_id": 3, "speaker_name": "Deepak Bisht"},
        {"turn_id": 4, "speaker_name": "Candidate User"},
    ]

    metrics = evaluate_speaker_switching(turns)
    assert metrics["total_turns"] == 4
    assert metrics["distinct_speakers"] == ["Deepak Bisht", "Candidate User"]
    assert metrics["speaker_switches"] == 3
    assert metrics["switches_valid"] == 3
    assert metrics["switch_accuracy_pct"] == 100.0


def test_speaker_switching_with_unknown():
    turns = [
        {"turn_id": 1, "speaker_name": "Deepak Bisht"},
        {"turn_id": 2, "speaker_name": "Unknown"},
        {"turn_id": 3, "speaker_name": "Candidate User"},
    ]
    metrics = evaluate_speaker_switching(turns)
    assert metrics["speaker_switches"] == 2
    assert metrics["switches_valid"] == 0
    assert metrics["switch_accuracy_pct"] == 0.0


def test_wer_computation():
    ref = "I have six years of microservices experience"
    hyp_exact = "I have six years of microservices experience"
    assert compute_wer(ref, hyp_exact) == 0.0

    hyp_sub = "I have six years of backend experience"
    wer = compute_wer(ref, hyp_sub)
    assert 0.0 < wer < 0.35
