import os
import uuid
from typing import List, Dict, Any, Optional
from services.copilot.src.models.copilot import CopilotSessionModel
from services.copilot.src.core.config import Settings

class CopilotRepository:
    """Manages Copilot session metadata and filesystem resources."""
    def __init__(self, directory: str = Settings.DEFAULT_STORAGE_DIR):
        self.directory = os.path.join(directory, "copilots")
        os.makedirs(self.directory, exist_ok=True)

    async def create_session(
        self, 
        jd: str, 
        resume: str, 
        custom_prompt: str,
        session_id: str = None
    ) -> str:
        # Use provided session_id or generate a new one
        if session_id:
            sid_uuid = uuid.UUID(session_id)
        else:
            sid_uuid = uuid.uuid4()
        
        # Create session directory for local files
        session_dir = os.path.join(self.directory, str(sid_uuid))
        os.makedirs(session_dir, exist_ok=True)
        
        # Save JD file locally
        with open(os.path.join(session_dir, "jd.txt"), "w", encoding="utf-8") as f:
            f.write(jd)
            
        # Save Resume file locally
        with open(os.path.join(session_dir, "resume.txt"), "w", encoding="utf-8") as f:
            f.write(resume)
            
        # Save session metadata in database (skip if already exists)
        existing = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not existing:
            await CopilotSessionModel.create(
                session_id=sid_uuid,
                jd=jd,
                resume=resume,
                custom_prompt=custom_prompt,
                transcript=[]
            )
        return str(sid_uuid)

    async def save_session(self, session_id: str, data: dict) -> None:
        try:
            sid_uuid = uuid.UUID(session_id)
        except ValueError:
            from loguru import logger
            logger.warning(f"save_session: invalid UUID '{session_id}', skipping.")
            return

        update_fields = {}
        if "transcript" in data:
            update_fields["transcript"] = data["transcript"]
        if "final_report" in data:
            update_fields["final_report"] = data["final_report"]
        elif "intelligence" in data and "assistance" in data and data.get("is_finalized"):
            update_fields["final_report"] = {
                "transcript": data.get("transcript", []),
                "intelligence": data.get("intelligence", {}),
                "assistance": data.get("assistance", {})
            }

        # Phase 2S: Persist initial_suggestions to disk cache if provided
        if "initial_suggestions" in data:
            initial_suggs = data["initial_suggestions"]
            for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, "initial_suggestions.json"), "w", encoding="utf-8") as f:
                        import json
                        json.dump({
                            "session_id": str(sid_uuid),
                            "initial_suggestions": initial_suggs
                        }, f, indent=2)
                except Exception:
                    pass

        # Phase 2T: Persist dynamic_suggestions to disk cache if provided
        if "dynamic_suggestions" in data:
            dyn_suggs = data["dynamic_suggestions"]
            for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, "dynamic_suggestions.json"), "w", encoding="utf-8") as f:
                        import json
                        json.dump({
                            "session_id": str(sid_uuid),
                            "dynamic_suggestions": dyn_suggs
                        }, f, indent=2)
                except Exception:
                    pass

        # Phase 2V: Persist confirmed_qa_pairs with accuracy_score to disk cache if provided
        if "confirmed_qa_pairs" in data:
            qa_pairs = data["confirmed_qa_pairs"]
            for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, "confirmed_qa_pairs.json"), "w", encoding="utf-8") as f:
                        import json
                        json.dump({
                            "session_id": str(sid_uuid),
                            "confirmed_qa_pairs": qa_pairs
                        }, f, indent=2)
                except Exception:
                    pass

        # Phase 2X: Persist scenario_questions & verification_questions to disk cache if provided
        if "scenario_questions" in data and "verification_questions" in data:
            sc_q = data["scenario_questions"]
            ver_q = data["verification_questions"]
            if len(sc_q) == 5 and len(ver_q) == 5:
                for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                        with open(os.path.join(target_dir, "static_questions.json"), "w", encoding="utf-8") as f:
                            import json
                            json.dump({
                                "session_id": str(sid_uuid),
                                "scenario_questions": sc_q,
                                "verification_questions": ver_q
                            }, f, indent=2)
                    except Exception:
                        pass

        # Phase 1: Persist compact_profile to disk cache if provided
        if "compact_profile" in data:
            c_prof = data["compact_profile"]
            for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, "compact_profile.json"), "w", encoding="utf-8") as f:
                        import json
                        prof_data = c_prof if isinstance(c_prof, dict) else (c_prof.to_dict() if hasattr(c_prof, "to_dict") else vars(c_prof))
                        json.dump(prof_data, f, indent=2)
                except Exception:
                    pass

        if update_fields:
            await CopilotSessionModel.filter(session_id=sid_uuid).update(**update_fields)

    async def load_session(self, session_id: str) -> dict:
        try:
            sid_uuid = uuid.UUID(session_id)
        except ValueError:
            raise FileNotFoundError(f"Invalid session id format: '{session_id}'")
        session = await CopilotSessionModel.get_or_none(session_id=sid_uuid)
        if not session:
            raise FileNotFoundError(f"Copilot record not found for session: {session_id}")
        is_service_off = os.path.exists(os.path.join("interviews", str(sid_uuid), "service_off.flag")) or os.path.exists(os.path.join(self.directory, str(sid_uuid), "service_off.flag"))

        # Phase 2S: Load persisted initial suggestions if available
        initial_suggestions = []
        for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
            p = os.path.join(target_dir, "initial_suggestions.json")
            if os.path.exists(p):
                try:
                    import json
                    with open(p, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        if isinstance(s_data, dict):
                            initial_suggestions = s_data.get("initial_suggestions", [])
                        elif isinstance(s_data, list):
                            initial_suggestions = s_data
                    if initial_suggestions:
                        break
                except Exception:
                    pass

        if not initial_suggestions and isinstance(session.final_report, dict):
            initial_suggestions = session.final_report.get("assistance", {}).get("initial_suggestions", [])

        # Phase 2T: Load persisted dynamic suggestions if available
        dynamic_suggestions = []
        for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
            p = os.path.join(target_dir, "dynamic_suggestions.json")
            if os.path.exists(p):
                try:
                    import json
                    with open(p, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        if isinstance(s_data, dict):
                            dynamic_suggestions = s_data.get("dynamic_suggestions", [])
                        elif isinstance(s_data, list):
                            dynamic_suggestions = s_data
                    if dynamic_suggestions:
                        break
                except Exception:
                    pass

        if not dynamic_suggestions and isinstance(session.final_report, dict):
            dynamic_suggestions = session.final_report.get("assistance", {}).get("dynamic_suggestions", [])

        # Phase 2V: Load persisted confirmed_qa_pairs if available
        confirmed_qa_pairs = []
        for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
            p = os.path.join(target_dir, "confirmed_qa_pairs.json")
            if os.path.exists(p):
                try:
                    import json
                    with open(p, "r", encoding="utf-8") as f:
                        q_data = json.load(f)
                        if isinstance(q_data, dict):
                            confirmed_qa_pairs = q_data.get("confirmed_qa_pairs", [])
                        elif isinstance(q_data, list):
                            confirmed_qa_pairs = q_data
                    if confirmed_qa_pairs:
                        break
                except Exception:
                    pass

        # Phase 2X: Load persisted static scenario and verification questions if available
        scenario_questions = []
        verification_questions = []
        for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
            p = os.path.join(target_dir, "static_questions.json")
            if os.path.exists(p):
                try:
                    import json
                    with open(p, "r", encoding="utf-8") as f:
                        sq_data = json.load(f)
                        if isinstance(sq_data, dict):
                            sc_q = sq_data.get("scenario_questions", [])
                            ver_q = sq_data.get("verification_questions", [])
                            if len(sc_q) == 5 and len(ver_q) == 5:
                                scenario_questions = sc_q
                                verification_questions = ver_q
                                break
                except Exception:
                    pass

        if (not scenario_questions or not verification_questions) and isinstance(session.final_report, dict):
            assist = session.final_report.get("assistance", {})
            sc_q = assist.get("scenario_questions") or assist.get("suggested_practical_questions", [])
            ver_q = assist.get("verification_questions", [])
            if len(sc_q) == 5:
                scenario_questions = sc_q
            if len(ver_q) == 5:
                verification_questions = ver_q

        # Phase 1: Load persisted compact_profile if available
        compact_profile = None
        for target_dir in [os.path.join("interviews", str(sid_uuid)), os.path.join(self.directory, str(sid_uuid))]:
            p = os.path.join(target_dir, "compact_profile.json")
            if os.path.exists(p):
                try:
                    import json
                    with open(p, "r", encoding="utf-8") as f:
                        compact_profile = json.load(f)
                    if compact_profile:
                        break
                except Exception:
                    pass

        if compact_profile and not initial_suggestions:
            initial_suggestions = compact_profile.get("initial_questions", [])[:2]

        if compact_profile and (not scenario_questions or not verification_questions):
            if not scenario_questions:
                scenario_questions = compact_profile.get("scenario_questions", [])[:5]
            if not verification_questions:
                verification_questions = compact_profile.get("verification_questions", [])[:5]

        return {
            "session_id": str(session.session_id),
            "timestamp": session.timestamp.isoformat() if session.timestamp else None,
            "jd": session.jd,
            "resume": session.resume,
            "custom_prompt": session.custom_prompt,
            "transcript": session.transcript,
            "final_report": session.final_report,
            "initial_suggestions": initial_suggestions,
            "dynamic_suggestions": dynamic_suggestions,
            "confirmed_qa_pairs": confirmed_qa_pairs,
            "scenario_questions": scenario_questions,
            "verification_questions": verification_questions,
            "compact_profile": compact_profile,
            "service_off": is_service_off
        }

    async def list_sessions(self, limit: Optional[int] = None) -> List[dict]:
        # Fetch all session details ordered by timestamp
        query = CopilotSessionModel.all().order_by("-timestamp")
        if limit is not None and limit > 0:
            query = query.limit(limit)
        sessions = await query
        return [
            {
                "session_id": str(s.session_id),
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "jd": s.jd,
                "resume": s.resume,
                "custom_prompt": s.custom_prompt,
                "transcript": s.transcript,
                "final_report": s.final_report
            }
            for s in sessions
        ]
