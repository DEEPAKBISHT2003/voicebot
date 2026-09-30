import io
import pytest
import pypdf
from typing import Dict, Any

from services.copilot.src.services.pdf_report_generator import generate_report_pdf


@pytest.fixture
def sample_final_report() -> Dict[str, Any]:
    return {
        "session_id": "35ff9ee4-7851-4fc3-afe5-0f7d4af6bf44",
        "evaluated_at": "2026-09-30T14:31:00Z",
        "is_finalized": True,
        "overall_score": 45,
        "qa_accuracy_average": 43,
        "holistic_competency": {
            "score": 51,
            "dimensions": {
                "technical_depth": {
                    "score": 50,
                    "summary": "Broad exposure to AI engineering concepts, but requires deeper systems design depth."
                },
                "practical_experience": {
                    "score": 48,
                    "summary": "1.5 years experience with FastAPI, WebSockets, and RAG pipelines."
                },
                "problem_solving": {
                    "score": 55,
                    "summary": "Demonstrated sound reasoning on test case validation under high background noise."
                },
                "communication_clarity": {
                    "score": 52,
                    "summary": "Concise verbal style with structured articulation."
                }
            }
        },
        "strengths": [
            "1.5 years of production AI engineering experience",
            "Exposure to multi-agent workflows",
            "Practical experience with MCP servers"
        ],
        "development_areas": [
            "Shallow protocol-level MCP understanding",
            "Limited architectural tradeoff discussion"
        ],
        "jd_analysis": {
            "summary": "The candidate matches initial AI prototyping requirements but lacks senior systems rigor.",
            "covered_skills": ["Python", "FastAPI", "RAG Pipelines", "Prompt Engineering"],
            "remaining_skills": ["Kubernetes", "Kafka", "Distributed Cache Design"]
        },
        "resume_validation": {
            "summary": "Key medical AI projects verified during conversational screening.",
            "verified_projects": [
                "M.A.R.Y - AI-Powered Medical Assistant",
                "Prior Authorization NLP Pipeline"
            ],
            "unverified_projects": [
                "Enterprise Legal RAG System"
            ]
        },
        "conversation_summary": "Candidate introduced himself and detailed his experiences with AI copilot platforms and reliability testing. Demonstrated good baseline knowledge of RAG architectures.",
        "observer_notes": [
            "Candidate answered promptly without excessive hesitation.",
            "Suggested follow-up on distributed message queues in the next round."
        ],
        "question_analysis": [
            {
                "pair_id": "Q1_A2",
                "question": "Can you walk me through a specific AI feature or API you built at Appzlogic?",
                "answer": "I implemented different RAG projects, fine-tuning, and function calling with tool selection.",
                "accuracy_score": 85,
                "observations": "Candidate provided concrete implementation details regarding retrieval chunking and tool routing."
            },
            {
                "pair_id": "Q3_A4",
                "question": "How did you validate and test the reliability claims mentioned in your resume?",
                "answer": "We tested the platform using real-world scenarios including extreme background noise.",
                "accuracy_score": 32,
                "observations": "Validation explanation lacked formal statistical metrics and automated regression test harnesses."
            }
        ],
        # Raw transcript included in report dict to verify it is explicitly EXCLUDED from generated PDF
        "transcript": [
            {"turn_id": 1, "speaker": "Ankit", "text": "Raw transcript turn 1 that MUST NEVER appear in PDF output."},
            {"turn_id": 2, "speaker": "Deepak", "text": "Raw transcript turn 2 that MUST NEVER appear in PDF output."}
        ]
    }


def test_generate_pdf_structure(sample_final_report):
    """
    Verifies that the generated PDF contains all required report sections,
    headers, footers, KPI metrics, and is a valid multi-page PDF.
    """
    pdf_bytes = generate_report_pdf(
        final_report=sample_final_report,
        session_id="35ff9ee4-7851-4fc3-afe5-0f7d4af6bf44",
        candidate_name="Deepak Bisht",
        interviewer="Ankit Kumar"
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF-")

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1

    all_text = " ".join(p.extract_text() for p in reader.pages)

    # 1. Cover Header
    assert "CANDIDATE TECHNICAL ASSESSMENT DOSSIER" in all_text
    assert "35ff9ee4-7851-4fc3-afe5-0f7d4af6bf44" in all_text
    assert "Deepak Bisht" in all_text
    assert "Ankit Kumar" in all_text
    assert "Verified & Finalized" in all_text

    # 2. Executive Score Summary
    assert "EXECUTIVE SCORE SUMMARY" in all_text
    assert "OVERALL CANDIDATE SCORE" in all_text
    assert "45%" in all_text
    assert "43%" in all_text
    assert "51%" in all_text

    # 3. Holistic Competency Dimensions
    assert "HOLISTIC COMPETENCY DIMENSIONS" in all_text
    assert "Technical Depth — 50%" in all_text
    assert "Practical Experience — 48%" in all_text
    assert "Problem Solving — 55%" in all_text
    assert "Communication Clarity — 52%" in all_text

    # 4. Strengths
    assert "CANDIDATE STRENGTHS" in all_text
    assert "1.5 years of production AI engineering experience" in all_text
    assert "Exposure to multi-agent workflows" in all_text

    # 5. Development Areas & Gaps
    assert "DEVELOPMENT AREAS & GAPS" in all_text
    assert "Shallow protocol-level MCP understanding" in all_text

    # 6. JD Skill Requirements Matrix
    assert "JD SKILL REQUIREMENTS MATRIX" in all_text
    assert "Discussed / Covered" in all_text
    assert "Remaining / Unassessed" in all_text
    assert "FastAPI" in all_text
    assert "Kubernetes" in all_text

    # 7. Resume Experience Validation
    assert "RESUME EXPERIENCE VALIDATION" in all_text
    assert "Verified Projects" in all_text
    assert "Unverified Projects" in all_text
    assert "M.A.R.Y" in all_text

    # 8. Conversation Summary
    assert "CONVERSATION SUMMARY" in all_text
    assert "Candidate introduced himself and detailed his experiences" in all_text

    # 9. Interview Observer Notes
    assert "INTERVIEW OBSERVER NOTES" in all_text
    assert "Candidate answered promptly without excessive hesitation" in all_text

    # 10. Detailed Question-by-Question Analysis
    assert "DETAILED QUESTION-BY-QUESTION ANALYSIS" in all_text
    assert "Question 1" in all_text
    assert "85%" in all_text
    assert "Candidate provided concrete implementation details" in all_text

    # Running header & footer
    assert "Page 1 of" in all_text
    assert "Generated by Appz Meeting Observer" in all_text
    assert "Confidential Hiring Evaluation" in all_text


def test_generate_pdf_excludes_raw_transcripts(sample_final_report):
    """
    Verifies that raw transcript lines, turn data, and timestamps
    are strictly excluded from the PDF output.
    """
    pdf_bytes = generate_report_pdf(
        final_report=sample_final_report,
        session_id="35ff9ee4-7851-4fc3-afe5-0f7d4af6bf44"
    )

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    all_text = " ".join(p.extract_text() for p in reader.pages)

    assert "MUST NEVER appear in PDF output" not in all_text
    assert "Complete Interview Transcript" not in all_text
    assert "Live Transcript" not in all_text
    assert "Speaker Timeline" not in all_text


def test_generate_pdf_large_qa_pairs(sample_final_report):
    """
    Acceptance Criteria Scenario 2: 50+ Q&A pairs.
    Verifies that the PDF generates smoothly across multiple pages without
    truncation or styling crashes.
    """
    large_report = dict(sample_final_report)
    large_qas = []
    for i in range(50):
        large_qas.append({
            "pair_id": f"Q{i+1}_A{i+1}",
            "question": f"Technical Interview Question #{i+1}: Can you explain concurrency model #{i+1}?",
            "answer": f"Candidate answer for question #{i+1}: In our implementation we leveraged asynchronous event loops.",
            "accuracy_score": 60 + (i % 35),
            "observations": f"Evaluation observation for technical question #{i+1}."
        })
    large_report["question_analysis"] = large_qas

    pdf_bytes = generate_report_pdf(
        final_report=large_report,
        session_id="large-session-test-id",
        candidate_name="Performance Test Candidate"
    )

    assert isinstance(pdf_bytes, bytes)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    # 50 Q&A pairs should produce at least 8-15 pages
    assert len(reader.pages) >= 8

    # Verify first and last questions exist in document
    all_text = " ".join(p.extract_text() for p in reader.pages)
    assert "Question 1" in all_text
    assert "Technical Interview Question #50" in all_text
    assert "Page 1 of" in all_text


def test_generate_pdf_empty_sections_graceful_fallback():
    """
    Verifies that sparse or empty reports (e.g. initial incomplete session)
    generate valid PDFs with safe fallback messages rather than crashing.
    """
    sparse_report = {
        "session_id": "sparse-sess-1",
        "evaluated_at": None,
        "is_finalized": False,
        "overall_score": None,
        "qa_accuracy_average": None,
        "holistic_competency": None,
        "strengths": [],
        "development_areas": [],
        "jd_analysis": None,
        "resume_validation": None,
        "conversation_summary": None,
        "observer_notes": [],
        "question_analysis": []
    }

    pdf_bytes = generate_report_pdf(
        final_report=sparse_report,
        session_id="sparse-sess-1"
    )

    assert isinstance(pdf_bytes, bytes)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    all_text = reader.pages[0].extract_text()

    assert "Draft Evaluation" in all_text
    assert "N/A" in all_text
    assert "No specific strengths documented" in all_text
    assert "No development areas highlighted" in all_text
    assert "No confirmed Q&A pairs recorded" in all_text


def test_pdf_api_endpoint_200_and_headers(sample_final_report):
    """
    Verifies that GET /api/reports/{session_id}/pdf returns status 200,
    media_type application/pdf, and Content-Disposition header with filename.
    """
    from starlette.testclient import TestClient
    from services.main import app

    session_id = "test-endpoint-sess-id"

    # Inject into app.state.copilot_sessions in-memory map
    app.state.copilot_sessions[session_id] = {
        "session_id": session_id,
        "candidate_name": "Test Candidate",
        "interviewer": "Test Interviewer",
        "final_report": sample_final_report,
        "is_active": False
    }

    try:
        with TestClient(app) as client:
            res = client.get(f"/api/reports/{session_id}/pdf")
            assert res.status_code == 200
            assert "application/pdf" in res.headers.get("content-type", "")
            expected_header = f'attachment; filename="candidate-report-{session_id}.pdf"'
            assert res.headers.get("content-disposition") == expected_header
            assert len(res.content) > 1000
            assert res.content.startswith(b"%PDF-")

            # Verify alias /api/copilot/{session_id}/pdf works identically
            res_alias = client.get(f"/api/copilot/{session_id}/pdf")
            assert res_alias.status_code == 200
            assert res_alias.content.startswith(b"%PDF-")
    finally:
        app.state.copilot_sessions.pop(session_id, None)


def test_pdf_api_endpoint_404_not_found():
    """
    Verifies that GET /api/reports/{session_id}/pdf returns 404 when session
    does not exist or has not been finalized.
    """
    from starlette.testclient import TestClient
    from services.main import app

    with TestClient(app) as client:
        res = client.get("/api/reports/00000000-0000-0000-0000-000000000000/pdf")
        assert res.status_code == 404
        assert "Final evaluation report not found" in res.json().get("detail", "")

