from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from loguru import logger

from services.copilot.src.api.deps import get_copilot_repo, get_copilot_sessions
from services.copilot.src.services.repository import CopilotRepository
from services.copilot.src.services.pdf_report_generator import generate_report_pdf

router = APIRouter(tags=["Reports"])


@router.get("/reports/{session_id}/pdf")
@router.get("/copilot/{session_id}/pdf")
async def download_report_pdf(
    session_id: str,
    repo: CopilotRepository = Depends(get_copilot_repo),
    active_sessions: Dict[str, Any] = Depends(get_copilot_sessions),
):
    """
    Generates and downloads an ATS-style, recruiter-ready PDF dossier
    from existing finalized evaluation data (zero LLM calls / zero recalculations).
    Excludes raw transcripts and speaker timeline turns.
    """
    final_report: Optional[Dict[str, Any]] = None
    candidate_name: Optional[str] = None
    interviewer: Optional[str] = None

    # 1. Check in-memory active sessions
    if session_id in active_sessions:
        sess = active_sessions[session_id]
        final_report = sess.get("final_report")
        candidate_name = sess.get("candidate_name")
        interviewer = sess.get("interviewer")

    # 2. Check persisted database session
    if not final_report:
        try:
            db_session = await repo.load_session(session_id)
            if db_session:
                final_report = db_session.get("final_report")
                candidate_name = candidate_name or db_session.get("candidate_name")
                interviewer = interviewer or db_session.get("interviewer")
        except FileNotFoundError:
            pass

    if not final_report:
        logger.warning(f"[ReportPDF] No final report found for session: {session_id}")
        raise HTTPException(
            status_code=404,
            detail=f"Final evaluation report not found or not yet finalized for session: '{session_id}'"
        )

    try:
        pdf_bytes = generate_report_pdf(
            final_report=final_report,
            session_id=session_id,
            candidate_name=candidate_name,
            interviewer=interviewer,
        )
    except Exception as e:
        logger.error(f"[ReportPDF] Error generating PDF for session {session_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate PDF report: {str(e)}"
        )

    filename = f"candidate-report-{session_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/pdf",
        },
    )
