import pytest
import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from services.copilot.src.services.deterministic_role_classifier import DeterministicRoleClassifier
from services.copilot.src.engine.session import CopilotSessionEngine
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.services.qa_state_machine import QAState


@pytest.fixture
def mock_fallback_identifier():
    mock = MagicMock()
    mock.identify_roles = AsyncMock(return_value={"speakers": {}})
    return mock


@pytest.fixture
def classifier(mock_fallback_identifier):
    return DeterministicRoleClassifier(fallback_identifier=mock_fallback_identifier)


@pytest.mark.asyncio
async def test_1_deepak_kumar_vs_ankit_kumar(classifier):
    """
    Test 1: Deepak Kumar (Resume Candidate) vs Ankit Kumar & Deepak Bisht.
    Surname 'Kumar' alone must NEVER crown Ankit Kumar as candidate.
    With candidate address 'OK. Deepak, can you please introduce yourself?',
    Deepak Bisht is resolved as Candidate (0.95) and Ankit Kumar as Interviewer (0.90).
    """
    resume = "Deepak Kumar\nSenior Software Engineer\nPython, Distributed Systems"
    transcript = [
        {"turn_id": 1, "speaker": "Deepak Bisht", "text": "Hi am I audible"},
        {"turn_id": 2, "speaker": "Ankit Kumar", "text": "Yes, I can hear you."},
        {"turn_id": 3, "speaker": "Ankit Kumar", "text": "OK. Deepak, can you please introduce yourself?"}
    ]

    # Test both participant orderings
    for participants in [["Ankit Kumar", "Deepak Bisht"], ["Deepak Bisht", "Ankit Kumar"]]:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume
        )
        speakers = res["speakers"]
        assert speakers["Deepak Bisht"]["role"] == "candidate", f"Failed for ordering {participants}"
        assert speakers["Deepak Bisht"]["confidence"] >= 0.80
        assert speakers["Ankit Kumar"]["role"] == "interviewer", f"Failed for ordering {participants}"
        assert speakers["Ankit Kumar"]["confidence"] >= 0.80


@pytest.mark.asyncio
async def test_2_singh_vs_singh(classifier):
    """
    Test 2: Singh vs Singh.
    Resume Candidate: Harpreet Singh.
    Participants: Gurpreet Singh (Interviewer) and Harpreet Singh (Candidate).
    Exact full-name match (1.0) must cleanly assign Harpreet Singh and infer Gurpreet Singh as Interviewer.
    """
    resume = "Harpreet Singh\nStaff ML Engineer\nNLP, PyTorch"
    transcript = [
        {"turn_id": 1, "speaker": "Gurpreet Singh", "text": "Hello, welcome to the interview."},
        {"turn_id": 2, "speaker": "Harpreet Singh", "text": "Thank you, glad to be here."}
    ]

    for participants in [["Gurpreet Singh", "Harpreet Singh"], ["Harpreet Singh", "Gurpreet Singh"]]:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume
        )
        speakers = res["speakers"]
        assert speakers["Harpreet Singh"]["role"] == "candidate"
        assert speakers["Harpreet Singh"]["confidence"] == 1.0
        assert speakers["Gurpreet Singh"]["role"] == "interviewer"
        assert speakers["Gurpreet Singh"]["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_3_sharma_vs_sharma(classifier):
    """
    Test 3: Sharma vs Sharma.
    Resume Candidate: Rohit Sharma.
    Participants: Amit Sharma (Interviewer) and Rohit Sharma (Candidate).
    """
    resume = "Rohit Sharma\nDevOps Architect\nKubernetes, AWS"
    transcript = [
        {"turn_id": 1, "speaker": "Amit Sharma", "text": "Can you hear me Rohit?"},
        {"turn_id": 2, "speaker": "Rohit Sharma", "text": "Yes, loud and clear."}
    ]

    for participants in [["Amit Sharma", "Rohit Sharma"], ["Rohit Sharma", "Amit Sharma"]]:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume
        )
        speakers = res["speakers"]
        assert speakers["Rohit Sharma"]["role"] == "candidate"
        assert speakers["Rohit Sharma"]["confidence"] == 1.0
        assert speakers["Amit Sharma"]["role"] == "interviewer"
        assert speakers["Amit Sharma"]["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_4_same_surname_different_first_names_with_addressing(classifier):
    """
    Test 4: Same surname, different first names with partial display name.
    Resume Candidate: Vikram Patel.
    Participants: Rajesh Patel (Interviewer) and Vikram (Candidate).
    Interviewer addresses candidate: 'Vikram, can you walk me through your resume?'
    """
    resume = "Vikram Patel\nFrontend Lead\nReact, TypeScript"
    transcript = [
        {"turn_id": 1, "speaker": "Rajesh Patel", "text": "Good morning. Vikram, can you walk me through your resume?"},
        {"turn_id": 2, "speaker": "Vikram", "text": "Sure, I have 8 years of experience building web applications."}
    ]

    for participants in [["Rajesh Patel", "Vikram"], ["Vikram", "Rajesh Patel"]]:
        res = await classifier.identify_roles(
            transcript=transcript,
            participants=participants,
            resume=resume
        )
        speakers = res["speakers"]
        assert speakers["Vikram"]["role"] == "candidate"
        assert speakers["Vikram"]["confidence"] >= 0.80
        assert speakers["Rajesh Patel"]["role"] == "interviewer"
        assert speakers["Rajesh Patel"]["confidence"] >= 0.80


@pytest.mark.asyncio
async def test_5_exact_candidate_name_present(classifier):
    """
    Test 5: Exact candidate name present in participants.
    Full-name match receives highest confidence (1.0) and beats any partial match.
    """
    resume = "Deepak Kumar\nSenior Backend Engineer"
    participants = ["Ankit Kumar", "Deepak Kumar"]

    res = await classifier.identify_roles(
        transcript=[],
        participants=participants,
        resume=resume
    )
    speakers = res["speakers"]
    assert speakers["Deepak Kumar"]["role"] == "candidate"
    assert speakers["Deepak Kumar"]["confidence"] == 1.0
    assert speakers["Ankit Kumar"]["role"] == "interviewer"
    assert speakers["Ankit Kumar"]["confidence"] == 0.85


@pytest.mark.asyncio
async def test_6_candidate_addressed_by_interviewer(classifier):
    """
    Test 6: Candidate addressed by interviewer pattern.
    Even with minimal resume tokens, direct interviewer question address identifies candidate.
    """
    resume = "Candidate Resume\nCandidate Name: Priya Nair"
    transcript = [
        {"turn_id": 1, "speaker": "John Doe", "text": "Hi Priya, can you please tell us about yourself?"},
        {"turn_id": 2, "speaker": "Priya", "text": "Hi John, sure thing."}
    ]
    participants = ["John Doe", "Priya"]

    res = await classifier.identify_roles(
        transcript=transcript,
        participants=participants,
        resume=resume
    )
    speakers = res["speakers"]
    assert speakers["Priya"]["role"] == "candidate"
    assert speakers["Priya"]["confidence"] >= 0.90
    assert speakers["John Doe"]["role"] == "interviewer"
    assert speakers["John Doe"]["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_7_different_participant_ordering_invariance(classifier):
    """
    Test 7: Proves strict invariance to participant array permutation.
    """
    resume = "Deepak Kumar\nSoftware Engineer"
    transcript = [
        {"turn_id": 1, "speaker": "Ankit Kumar", "text": "OK. Deepak, can you please introduce yourself?"},
        {"turn_id": 2, "speaker": "Deepak Bisht", "text": "Yes, I am a software engineer."}
    ]

    res_1 = await classifier.identify_roles(
        transcript=transcript,
        participants=["Ankit Kumar", "Deepak Bisht"],
        resume=resume
    )
    res_2 = await classifier.identify_roles(
        transcript=transcript,
        participants=["Deepak Bisht", "Ankit Kumar"],
        resume=resume
    )

    assert res_1["speakers"]["Deepak Bisht"]["role"] == res_2["speakers"]["Deepak Bisht"]["role"] == "candidate"
    assert res_1["speakers"]["Ankit Kumar"]["role"] == res_2["speakers"]["Ankit Kumar"]["role"] == "interviewer"
    assert res_1["speakers"]["Deepak Bisht"]["confidence"] == res_2["speakers"]["Deepak Bisht"]["confidence"]
    assert res_1["speakers"]["Ankit Kumar"]["confidence"] == res_2["speakers"]["Ankit Kumar"]["confidence"]


def test_8_pythonhashseed_invariance():
    """
    Test 8: Runs role classifier in subprocesses with different PYTHONHASHSEED values (0, 42, 1337).
    Asserts output is identical across all hash seeds.
    """
    script = """
import asyncio
from services.copilot.src.services.deterministic_role_classifier import DeterministicRoleClassifier

async def main():
    classifier = DeterministicRoleClassifier()
    resume = "Deepak Kumar\\nSoftware Engineer"
    transcript = [
        {"turn_id": 1, "speaker": "Ankit Kumar", "text": "OK. Deepak, can you please introduce yourself?"},
        {"turn_id": 2, "speaker": "Deepak Bisht", "text": "Yes, I am a software engineer."}
    ]
    # Use a set to simulate non-deterministic speaker collection
    spk_set = {"Ankit Kumar", "Deepak Bisht"}
    res = await classifier.identify_roles(
        transcript=transcript,
        participants=list(spk_set),
        resume=resume
    )
    print(f"{res['speakers']['Deepak Bisht']['role']}:{res['speakers']['Ankit Kumar']['role']}")

asyncio.run(main())
"""
    seeds = ["0", "42", "1337", "999999"]
    outputs = []
    python_exe = sys.executable

    for seed in seeds:
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = "."
        result = subprocess.run(
            [python_exe, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True
        )
        outputs.append(result.stdout.strip())

    # All outputs must be candidate:interviewer
    assert len(set(outputs)) == 1, f"Hash seeds produced differing results: {outputs}"
    assert outputs[0] == "candidate:interviewer"


@pytest.mark.asyncio
async def test_early_turn_buffering_and_replay_into_fsm(tmp_path):
    """
    Test C: Initial Role Resolution & Buffering.
    Verifies that early turns with unknown roles are buffered and NOT dropped.
    Once roles resolve (at Turn 3), buffered turns (Turn 1, Turn 2) are replayed
    into QA FSM with their updated roles, capturing questions correctly.
    """
    mock_repo = MagicMock(spec=CopilotRepository)
    mock_repo.directory = str(tmp_path)
    mock_repo.save_session = AsyncMock()

    session = CopilotSessionEngine(
        session_id="test_buffering_sess",
        resume="Deepak Kumar\nSenior Backend Engineer",
        repo=mock_repo
    )

    completed_qas = []

    async def on_qa(rec):
        completed_qas.append(rec)

    session.qa_fsm.on_qa_completed = on_qa

    # Turn 0: Deepak Bisht speaks greeting. Roles not yet resolved.
    t0 = await session.add_message("Deepak Bisht", "Yeah, hi, can you hear me?", allow_merge=False, is_final=True)
    assert len(session._unresolved_role_buffer) == 1
    assert session.qa_fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # Turn 1: Ankit Kumar speaks greeting. Roles still not resolved (Deepak Bisht has 0.70, Ankit Kumar has 0.20, no addressing).
    t1 = await session.add_message("Ankit Kumar", "Yes, I can hear you clearly.", allow_merge=False, is_final=True)
    assert len(session._unresolved_role_buffer) == 2
    assert session.qa_fsm.get_state() == QAState.WAITING_FOR_QUESTION

    # Turn 2: Ankit Kumar asks question addressing candidate: "OK. Deepak, can you please introduce yourself?"
    # Addressing pattern fires! Roles resolve synchronously!
    t2 = await session.add_message("Ankit Kumar", "OK. Deepak, can you please introduce yourself?", allow_merge=False, is_final=True)

    # Roles must now be fully resolved!
    assert session.session_speaker_roles["Deepak Bisht"] == "candidate"
    assert session.session_speaker_roles["Ankit Kumar"] == "interviewer"

    # Buffer must be completely flushed!
    assert len(session._unresolved_role_buffer) == 0

    # FSM must have processed the question from Ankit Kumar!
    assert session.qa_fsm.get_state() == QAState.QUESTION_CAPTURED
    assert "OK. Deepak, can you please introduce yourself" in session.qa_fsm.current_question["text"]
    assert session.qa_fsm.current_question["speaker"] == "Ankit Kumar"

    # Turn 3: Deepak Bisht answers
    await session.add_message("Deepak Bisht", "I am Deepak with 6 years of backend experience in Python and microservices.", allow_merge=False, is_final=True)
    assert session.qa_fsm.get_state() == QAState.CANDIDATE_ANSWERING

    # Finalize to complete Q&A
    await session.qa_fsm.finalize_current_qa()
    assert len(completed_qas) == 1
    assert "OK. Deepak, can you please introduce yourself" in completed_qas[0]["question"]
    assert "I am Deepak with 6 years of backend experience" in completed_qas[0]["answer"]

    # Verify speaker attribution on transcript turns
    q_turn_id = completed_qas[0]["question_turn_id"]
    a_turn_id = completed_qas[0]["answer_turn_ids"][0]

    q_turn = [t for t in session.transcript if t["turn_id"] == q_turn_id][0]
    a_turn = [t for t in session.transcript if t["turn_id"] == a_turn_id][0]

    assert q_turn["speaker"] == "Ankit Kumar"
    assert q_turn["speaker_role"] == "interviewer"
    assert a_turn["speaker"] == "Deepak Bisht"
    assert a_turn["speaker_role"] == "candidate"
