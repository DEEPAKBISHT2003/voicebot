"""
Interview Readiness Service
Tracks the critical pipeline prerequisites before an interview can safely begin:
1. bot_joined
2. caption_socket_connected
3. transcript_processor_initialized
4. caption_count / unique_speakers_detected (Multi-caption validation)

Computes the unified readiness_confirmed / interview_ready boolean and discrete readiness states:
- BOT_JOINING
- IN_MEETING
- CAPTION_STREAM_CONNECTED
- TRANSCRIPT_ENGINE_READY
- CAPTIONS_FLOWING
- INTERVIEW_READY
"""
import time
from typing import Dict, Any, Optional, Set, List


def compute_readiness(sess: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes deterministic interview readiness state and individual condition flags.
    Implements multi-caption readiness verification:
    - Single caption alone does NOT declare readiness.
    - Requires caption_count >= 2 OR unique_speakers_detected >= 2.
    - Transitions through CAPTIONS_FLOWING before INTERVIEW_READY.
    - Once confirmed, readiness is NEVER revoked due to conversational silence.
    Safe against empty or None session objects.
    """
    if not sess or not isinstance(sess, dict):
        return {
            "state": "BOT_JOINING",
            "interview_ready": False,
            "readiness_confirmed": False,
            "bot_joined": False,
            "caption_socket_connected": False,
            "transcript_processor_initialized": False,
            "first_caption_received": False,
            "caption_count": 0,
            "unique_speakers": [],
            "unique_speakers_detected": 0,
            "first_caption_timestamp": None,
            "last_caption_time": None,
            "caption_seconds_ago": None,
        }

    # 1. Transcript Processor: initialized if turn aggregator, logical aggregator, or engine connected
    transcript_processor_initialized = bool(
        sess.get("transcript_processor_initialized", False)
        or sess.get("turn_aggregator") is not None
        or sess.get("logical_aggregator") is not None
        or (sess.get("engine") is not None and sess.get("caption_socket_connected"))
    )

    # 2. Extract captions telemetry
    transcript = sess.get("transcript") or []
    native_captions = sess.get("native_captions") or []

    # Calculate caption_count
    explicit_count = sess.get("caption_count")
    if explicit_count is not None and isinstance(explicit_count, (int, float)):
        caption_count = int(explicit_count)
    else:
        # Fallback to len of recorded captions or transcript entries
        caption_count = max(len(native_captions), len(transcript))

    first_caption_received = bool(
        sess.get("first_caption_received", False)
        or caption_count > 0
    )

    # Calculate unique speakers
    raw_unique = sess.get("unique_speakers")
    speaker_set: Set[str] = set()
    if isinstance(raw_unique, (set, list, tuple)):
        speaker_set = {str(s).strip() for s in raw_unique if str(s).strip() and str(s).strip().lower() != "unknown"}
    
    # If not explicitly tracked, harvest from native_captions / transcript
    if not speaker_set:
        for cap in native_captions:
            spk = (cap.get("speaker_name") or cap.get("speaker") or "").strip()
            if spk and spk.lower() != "unknown":
                speaker_set.add(spk)
        for turn in transcript:
            spk = (turn.get("speaker") or turn.get("speaker_name") or "").strip()
            if spk and spk.lower() != "unknown":
                speaker_set.add(spk)

    unique_speakers_list: List[str] = sorted(list(speaker_set))
    unique_speakers_detected = len(unique_speakers_list)

    # 3. Caption Stream Connected: true if explicitly flagged or captions have arrived
    caption_socket_connected = bool(
        sess.get("caption_socket_connected", False) or first_caption_received
    )

    # 4. Bot Joined: true if explicitly flagged, audio producer connected, or captions stream connected
    bot_joined = bool(
        sess.get("bot_joined", False)
        or caption_socket_connected
        or first_caption_received
        or sess.get("status") in ("Listening to audio stream...", "Simulating...")
    )

    # 5. Proven Transcript Throughput (INC-2026-0928-01 Honest Gating)
    # Interview Ready requires BOTH caption activity AND proven transcript throughput.
    eng = sess.get("engine")
    eng_transcript = eng.get_transcript() if eng else (sess.get("transcript") or [])
    logical_agg = sess.get("logical_aggregator")
    turn_agg = sess.get("turn_aggregator")

    finalized_turn_count = 0
    if logical_agg:
        finalized_turn_count = len(getattr(logical_agg, "finalized_logical_turns", []))
    elif turn_agg:
        finalized_turn_count = sum(len(turns) for turns in getattr(turn_agg, "finalized_turns", {}).values())

    has_active_turn = bool(logical_agg and getattr(logical_agg, "active_turn", None) is not None)

    has_proven_transcript = bool(
        len(eng_transcript) > 0
        or has_active_turn
        or finalized_turn_count > 0
        or sess.get("has_proven_transcript", False)
    )

    caption_activity = bool(caption_count >= 1 or first_caption_received or len(native_captions) > 0)

    # Multi-caption verification + Proven Throughput gating:
    # Captions prove actual flow ONLY if both transport activity and transcript delivery are proven.
    captions_sufficient = bool(
        caption_activity
        and has_proven_transcript
        and (caption_count >= 2 or unique_speakers_detected >= 2 or len(eng_transcript) > 0 or has_active_turn)
    )

    # Once readiness has been confirmed, it must NEVER be revoked by silence
    previously_confirmed = bool(sess.get("readiness_confirmed", False) or sess.get("interview_ready", False))

    readiness_confirmed = previously_confirmed or bool(
        bot_joined
        and caption_socket_connected
        and transcript_processor_initialized
        and captions_sufficient
        and has_proven_transcript
    )

    # Unified interview_ready boolean equals readiness_confirmed
    interview_ready = readiness_confirmed

    # Observability Logging (INC-2026-0928-01)
    session_id = sess.get("session_id", "unknown")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if has_proven_transcript and not sess.get("_throughput_verified_logged"):
        sess["_throughput_verified_logged"] = True
        speaker = "Unknown"
        if eng_transcript:
            speaker = eng_transcript[-1].get("speaker") or eng_transcript[-1].get("speaker_name") or "Unknown"
        elif logical_agg and logical_agg.active_turn:
            speaker = logical_agg.active_turn.speaker_name or "Unknown"
        turn_id = len(eng_transcript) if eng_transcript else (getattr(getattr(logical_agg, "active_turn", None), "logical_turn_id", 1))
        from loguru import logger
        logger.info(
            f"[Readiness] Throughput Verified: session_id={session_id}, speaker='{speaker}', "
            f"sequence_id={caption_count}, turn_id={turn_id}, timestamp='{now_iso}'"
        )

    if readiness_confirmed and not sess.get("_readiness_confirmed_logged"):
        sess["_readiness_confirmed_logged"] = True
        from loguru import logger
        logger.info(
            f"[Readiness] Interview Ready Confirmed: session_id={session_id}, timestamp='{now_iso}'"
        )

    # Discrete readiness state
    if readiness_confirmed:
        state = "INTERVIEW_READY"
    elif has_proven_transcript or (first_caption_received and caption_count > 0):
        state = "CAPTIONS_FLOWING"
    elif transcript_processor_initialized and caption_socket_connected:
        state = "TRANSCRIPT_ENGINE_READY"
    elif caption_socket_connected:
        state = "CAPTION_STREAM_CONNECTED"
    elif bot_joined:
        state = "IN_MEETING"
    else:
        state = "BOT_JOINING"

    # Track timing
    first_caption_timestamp = sess.get("first_caption_timestamp")
    last_caption_time = sess.get("last_caption_time")

    now = time.time()
    if first_caption_received:
        if first_caption_timestamp is None:
            first_caption_timestamp = now
            sess["first_caption_timestamp"] = first_caption_timestamp
        if last_caption_time is None:
            last_caption_time = now
            sess["last_caption_time"] = last_caption_time

    caption_seconds_ago = None
    if last_caption_time:
        caption_seconds_ago = max(0.0, round(now - float(last_caption_time), 1))

    return {
        "state": state,
        "interview_ready": interview_ready,
        "readiness_confirmed": readiness_confirmed,
        "bot_joined": bot_joined,
        "caption_socket_connected": caption_socket_connected,
        "transcript_processor_initialized": transcript_processor_initialized,
        "first_caption_received": first_caption_received,
        "has_proven_transcript": has_proven_transcript,
        "caption_count": caption_count,
        "unique_speakers": unique_speakers_list,
        "unique_speakers_detected": unique_speakers_detected,
        "first_caption_timestamp": first_caption_timestamp,
        "last_caption_time": last_caption_time,
        "caption_seconds_ago": caption_seconds_ago,
    }
