import pytest
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.services.unified_qa_worker import UnifiedQAWorker


def load_final_captions(file_path: str):
    """Loads raw Teams captions and extracts the latest/final text per caption_sequence."""
    final_by_seq = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            seq = item.get("caption_sequence")
            spk = item.get("speaker_name")
            txt = item.get("text", "").strip()
            if seq and spk and txt:
                final_by_seq[seq] = (spk, txt)
    
    return [final_by_seq[seq] for seq in sorted(final_by_seq.keys())]


@pytest.fixture
def mock_repo(tmp_path):
    repo = MagicMock(spec=CopilotRepository)
    repo.directory = str(tmp_path)
    repo.save_session = AsyncMock()
    return repo


@pytest.fixture
def mock_unified_qa_worker():
    """Mock UnifiedQAWorker returning valid deterministic evaluations."""
    worker = MagicMock(spec=UnifiedQAWorker)
    
    async def mock_execute(*args, **kwargs):
        return {
            "qa_id": "mock_qa",
            "accuracy_evaluation": {
                "relevance": 88,
                "technical": 85,
                "resume_match": 85,
                "completeness": 82
            },
            "accuracy_score": 85,
            "intelligence": {
                "current_topic": "System Architecture / MCP",
                "covered_skills": ["Python", "FastAPI", "MCP"],
                "remaining_skills": ["Database Tuning"]
            },
            "assistance": {
                "recommended_next_topic": "Security & Auth",
                "interview_notes": ["Candidate explained protocol clearly"],
                "current_candidate_understanding": "Demonstrates solid engineering foundations"
            },
            "suggestions": [
                "Can you elaborate on your MCP implementation trade-offs?",
                "How do you handle tool authorization in MCP?"
            ],
            "duration_seconds": 0.45,
            "estimated_tokens": 320
        }
    
    worker.execute = AsyncMock(side_effect=mock_execute)
    worker.process_completed_qa = AsyncMock(side_effect=mock_execute)
    return worker


@pytest.mark.asyncio
async def test_end_to_end_raw_captions_interview_2_replay(mock_repo, mock_unified_qa_worker):
    """
    End-to-End Test: Replays raw Teams captions from Interview #2 (INC-2026-0924-02).
    Captions contain NO pre-assigned speaker_role.
    Proves:
    1. Ankit Kumar is assigned interviewer, Deepak Bisht is assigned candidate.
    2. Zero role inversion.
    3. Zero dropped early turns.
    4. Confirmed Q&A pairs have Ankit Kumar as questioner and Deepak Bisht as answerer.
    5. UnifiedQAWorker executes on confirmed pairs.
    """
    caption_file = "interviews/dc8ed615-69b0-4390-9b07-4ff2ab9a073a/native_captions.jsonl"
    assert os.path.exists(caption_file), f"Caption file {caption_file} not found"

    raw_turns = load_final_captions(caption_file)
    assert len(raw_turns) >= 15

    resume_text = "Deepak Kumar\nSenior AI & Backend Developer\nPython, FastAPI, Microservices, LLMs"
    jd_text = "Senior AI Software Engineer\nRequirements: FastAPI, MCP protocol, distributed systems"

    session = CopilotSessionEngine(
        session_id="e2e_interview_2_replay",
        resume=resume_text,
        jd=jd_text,
        repo=mock_repo
    )
    session.unified_qa_worker = mock_unified_qa_worker

    # Feed raw turns sequentially into session.add_message with NO speaker_role
    for seq, (spk, txt) in enumerate(raw_turns[:15], start=1):
        await session.add_message(
            speaker=spk,
            text=txt,
            turn_id=seq,
            allow_merge=False,
            is_final=True
        )

    # Allow FSM transitions and async background tasks to complete
    await session.qa_fsm.finalize_current_qa()
    await asyncio.sleep(0.05)

    # 1. Assert role assignment correctness
    assert session.session_speaker_roles.get("Ankit Kumar") == "interviewer", "Ankit Kumar must be Interviewer"
    assert session.session_speaker_roles.get("Deepak Bisht") == "candidate", "Deepak Bisht must be Candidate"

    # 2. Assert zero dropped early turns
    assert len(session._unresolved_role_buffer) == 0, "Unresolved role buffer must be completely flushed"

    # 3. Assert confirmed QA pairs
    assert len(session.confirmed_qa_pairs) >= 2, f"Expected >= 2 QA pairs, found {len(session.confirmed_qa_pairs)}"

    # Check Pair 1: Introduction question
    intro_qa = next((qa for qa in session.confirmed_qa_pairs if "introduce yourself" in qa["question"].lower()), None)
    assert intro_qa is not None, "Introduction question must be captured"
    assert "ai developer" in intro_qa["answer"].lower() or "deepak" in intro_qa["answer"].lower()

    # Verify speaker attribution on transcript for Pair 1
    q1_turn = [t for t in session.transcript if t["turn_id"] == intro_qa["question_turn_id"]][0]
    assert q1_turn["speaker"] == "Ankit Kumar"
    assert q1_turn["speaker_role"] == "interviewer"

    a1_turns = [t for t in session.transcript if t["turn_id"] in intro_qa["answer_turn_ids"]]
    assert all(t["speaker"] == "Deepak Bisht" for t in a1_turns)
    assert all(t["speaker_role"] == "candidate" for t in a1_turns)

    # Check Pair 2: MCP question
    mcp_qa = next((qa for qa in session.confirmed_qa_pairs if "mcp" in qa["question"].lower()), None)
    assert mcp_qa is not None, "MCP question must be captured"
    assert "protocol" in mcp_qa["answer"].lower() or "usb port" in mcp_qa["answer"].lower()

    q2_turn = [t for t in session.transcript if t["turn_id"] == mcp_qa["question_turn_id"]][0]
    assert q2_turn["speaker"] == "Ankit Kumar"
    assert q2_turn["speaker_role"] == "interviewer"

    # 4. Assert UnifiedQAWorker executed
    assert mock_unified_qa_worker.process_completed_qa.call_count >= 2


@pytest.mark.asyncio
async def test_end_to_end_raw_captions_interview_1_replay(mock_repo, mock_unified_qa_worker):
    """
    End-to-End Test: Replays raw Teams captions from Interview #1.
    Verifies that the remediation also preserves 100% parity on Interview #1.
    """
    caption_file = "interviews/f3af41d9-8b40-42e6-9f4e-62757c02b207/native_captions.jsonl"
    assert os.path.exists(caption_file), f"Caption file {caption_file} not found"

    raw_turns = load_final_captions(caption_file)
    assert len(raw_turns) >= 10

    resume_text = "Deepak Kumar\nSenior Backend Architect\nPython, Distributed Systems"
    jd_text = "Lead Software Engineer"

    session = CopilotSessionEngine(
        session_id="e2e_interview_1_replay",
        resume=resume_text,
        jd=jd_text,
        repo=mock_repo
    )
    session.unified_qa_worker = mock_unified_qa_worker

    # Feed raw turns (initial 15 turns)
    for seq, (spk, txt) in enumerate(raw_turns[:15], start=1):
        await session.add_message(
            speaker=spk,
            text=txt,
            turn_id=seq,
            allow_merge=False,
            is_final=True
        )

    await session.qa_fsm.finalize_current_qa()
    await asyncio.sleep(0.05)

    # Assert roles
    assert session.session_speaker_roles.get("Ankit Kumar") == "interviewer"
    assert session.session_speaker_roles.get("Deepak Bisht") == "candidate"

    # Assert zero dropped turns
    assert len(session._unresolved_role_buffer) == 0

    # Assert QA pair captured
    assert len(session.confirmed_qa_pairs) >= 1
    qa1 = session.confirmed_qa_pairs[0]
    assert "introduce yourself" in qa1["question"].lower()

    q1_turn = [t for t in session.transcript if t["turn_id"] == qa1["question_turn_id"]][0]
    assert q1_turn["speaker"] == "Ankit Kumar"
    assert q1_turn["speaker_role"] == "interviewer"

    assert mock_unified_qa_worker.process_completed_qa.call_count >= 1
