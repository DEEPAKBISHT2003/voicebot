import os
import json
import asyncio
import datetime
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from loguru import logger
from openai import AsyncOpenAI
from services.copilot.src.core.config import Settings


def clean_json_loads(text: str) -> dict:
    """Safely parse JSON responses that may be wrapped in markdown codeblocks."""
    clean_text = text.strip()
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()
    return json.loads(clean_text)


class CompactProfile(BaseModel):
    """
    Consolidated pre-compiled interview context artifact.
    Contains candidate metadata, core skills, project claims,
    and pre-generated static interview questions.
    """
    session_id: str = ""
    candidate_name: str = ""
    target_role: str = ""
    core_skills: List[str] = Field(default_factory=list)
    project_claims: List[str] = Field(default_factory=list)
    initial_questions: List[str] = Field(default_factory=list)      # Exactly 2
    scenario_questions: List[str] = Field(default_factory=list)     # Exactly 5
    verification_questions: List[str] = Field(default_factory=list) # Exactly 5
    compiled_at: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SessionPreCompiler:
    """
    Phase 1: Pre-compiles the Job Description and Candidate Resume once per session.
    
    Consolidates:
      1. Candidate metadata extraction (name, target role, core skills, claims)
      2. Initial kick-off suggestions (Phase 2S, exactly 2 questions)
      3. Static scenario and verification questions (Phase 2X, exactly 5+5 questions)
    
    Into a SINGLE LLM inference, replacing multiple redundant document-parsing calls.
    Persists compact_profile.json, initial_suggestions.json, and static_questions.json
    for 100% backward compatibility with all existing consumers.
    """
    def __init__(
        self,
        api_key: str = Settings.DEEPSEEK_API_KEY,
        model: str = Settings.DEEPSEEK_MODEL,
        base_url: str = Settings.DEEPSEEK_BASE_URL,
        client: Optional[AsyncOpenAI] = None
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def compile_session(
        self,
        session_id: str,
        jd: str,
        resume: str,
        storage_dir: str = Settings.DEFAULT_STORAGE_DIR
    ) -> CompactProfile:
        """
        Executes pre-compilation with disk caching and strict validation.
        Guarantees:
        - Exactly 2 initial questions
        - Exactly 5 scenario questions
        - Exactly 5 verification questions
        - Saves compact_profile.json, initial_suggestions.json, static_questions.json
        """
        session_dir = os.path.join(storage_dir, str(session_id))
        os.makedirs(session_dir, exist_ok=True)
        profile_file = os.path.join(session_dir, "compact_profile.json")

        # 1. Disk Cache Check: Return existing artifact if present and valid
        if os.path.exists(profile_file):
            try:
                with open(profile_file, "r", encoding="utf-8") as pf:
                    data = json.load(pf)
                    profile = CompactProfile(**data)
                    if (
                        len(profile.initial_questions) >= 2
                        and len(profile.scenario_questions) == 5
                        and len(profile.verification_questions) == 5
                    ):
                        logger.info(f"[PreCompiler] Loaded valid cached CompactProfile for session {session_id}")
                        return profile
            except Exception as load_err:
                logger.warning(f"[PreCompiler] Error reading cached compact_profile.json: {load_err}")

        # 2. Check fallback to legacy static_questions.json and initial_suggestions.json if already exist
        init_file = os.path.join(session_dir, "initial_suggestions.json")
        static_file = os.path.join(session_dir, "static_questions.json")
        cached_initial = []
        cached_scenario = []
        cached_verify = []

        if os.path.exists(init_file):
            try:
                with open(init_file, "r", encoding="utf-8") as inf:
                    idata = json.load(inf)
                    cached_initial = idata.get("initial_suggestions", [])
            except Exception:
                pass

        if os.path.exists(static_file):
            try:
                with open(static_file, "r", encoding="utf-8") as stf:
                    sdata = json.load(stf)
                    cached_scenario = sdata.get("scenario_questions", [])
                    cached_verify = sdata.get("verification_questions", [])
            except Exception:
                pass

        if len(cached_initial) >= 2 and len(cached_scenario) == 5 and len(cached_verify) == 5:
            profile = CompactProfile(
                session_id=str(session_id),
                candidate_name=self._extract_candidate_name_fallback(resume),
                target_role=self._extract_target_role_fallback(jd),
                core_skills=self._extract_skills_fallback(jd, resume),
                project_claims=self._extract_claims_fallback(resume),
                initial_questions=cached_initial[:2],
                scenario_questions=cached_scenario[:5],
                verification_questions=cached_verify[:5],
            )
            self._persist_artifacts(session_dir, session_id, profile)
            logger.info(f"[PreCompiler] Synthesized CompactProfile from legacy cached artifacts for session {session_id}")
            return profile

        # 3. Handle Empty Inputs with Deterministic Fallbacks
        clean_jd = (jd or "").strip()
        clean_resume = (resume or "").strip()
        if not clean_jd and not clean_resume:
            logger.warning(f"[PreCompiler] Session {session_id} has both empty JD and Resume. Generating default profile.")
            default_profile = self._build_default_profile(session_id)
            self._persist_artifacts(session_dir, session_id, default_profile)
            return default_profile

        # 4. Single Consolidated LLM Call
        prompt = f"""You are an elite AI technical interview copilot systems architect.
Your job is to analyze the Target Job Description and Candidate Resume below to pre-compile the session profile and generate all initial interview questions in a single structured JSON response.

Job Description:
\"\"\"
{clean_jd}
\"\"\"

Candidate Resume:
\"\"\"
{clean_resume}
\"\"\"

Requirements:
1. "candidate_name": The candidate's full name extracted accurately from the resume. If missing or unclear, output "Candidate".
2. "target_role": The target job title/role extracted from the Job Description.
3. "core_skills": List of 8 to 15 key technical competencies, tools, frameworks, and domain skills required by the JD and mentioned in the resume.
4. "project_claims": List of 3 to 6 major projects, systems, or achievements claimed in the candidate's resume.
5. "initial_questions": EXACTLY 2 insightful kick-off questions for the interviewer to begin the conversation, referencing the candidate's background and the role requirements.
6. "scenario_questions": EXACTLY 5 practical, scenario-based architecture, coding, or system design questions directly relevant to the JD and candidate resume.
7. "verification_questions": EXACTLY 5 probing, specific questions designed to verify the authenticity and depth of project experiences listed on the candidate's resume.

Output MUST be a single valid JSON object matching this schema:
{{
  "candidate_name": "string",
  "target_role": "string",
  "core_skills": ["string"],
  "project_claims": ["string"],
  "initial_questions": ["string", "string"],
  "scenario_questions": ["string", "string", "string", "string", "string"],
  "verification_questions": ["string", "string", "string", "string", "string"]
}}

Respond ONLY with valid JSON. Do not include markdown fences or extraneous commentary.
"""
        try:
            logger.info(f"[PreCompiler] Submitting consolidated pre-compilation request for session {session_id}...")
            chat_completion = await asyncio.wait_for(
                self.client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.model,
                    response_format={"type": "json_object"},
                    temperature=0.2
                ),
                timeout=30.0
            )
            raw_content = chat_completion.choices[0].message.content or "{}"
            data = clean_json_loads(raw_content)

            # Extract & sanitize candidate name
            cand_name = str(data.get("candidate_name") or "").strip()
            if not cand_name or cand_name.lower() in ("unknown", "n/a", "none"):
                cand_name = self._extract_candidate_name_fallback(clean_resume)

            target_role = str(data.get("target_role") or "").strip()
            if not target_role:
                target_role = self._extract_target_role_fallback(clean_jd)

            core_skills = [s.strip() for s in data.get("core_skills", []) if s and str(s).strip()]
            if not core_skills:
                core_skills = self._extract_skills_fallback(clean_jd, clean_resume)

            project_claims = [p.strip() for p in data.get("project_claims", []) if p and str(p).strip()]

            # Enforce exact counts for question sets
            initial_q = [q.strip() for q in data.get("initial_questions", []) if q and str(q).strip()]
            scenario_q = [q.strip() for q in data.get("scenario_questions", []) if q and str(q).strip()]
            verify_q = [q.strip() for q in data.get("verification_questions", []) if q and str(q).strip()]

            initial_q = self._enforce_count(initial_q, 2, "initial")
            scenario_q = self._enforce_count(scenario_q, 5, "scenario")
            verify_q = self._enforce_count(verify_q, 5, "verification")

            profile = CompactProfile(
                session_id=str(session_id),
                candidate_name=cand_name,
                target_role=target_role,
                core_skills=core_skills,
                project_claims=project_claims,
                initial_questions=initial_q,
                scenario_questions=scenario_q,
                verification_questions=verify_q,
                compiled_at=datetime.datetime.now().isoformat()
            )

            # Persist all 3 artifacts for 100% backward compatibility
            self._persist_artifacts(session_dir, session_id, profile)
            logger.info(
                f"[PreCompiler] Pre-compilation successful for session {session_id}: "
                f"name='{profile.candidate_name}', role='{profile.target_role}', "
                f"skills={len(profile.core_skills)}, initial={len(profile.initial_questions)}, "
                f"scenario={len(profile.scenario_questions)}, verify={len(profile.verification_questions)}"
            )
            return profile

        except Exception as err:
            logger.error(f"[PreCompiler] LLM pre-compilation failed for session {session_id} ({err}). Generating robust fallback profile.")
            fallback_profile = self._build_default_profile(session_id, clean_jd, clean_resume)
            self._persist_artifacts(session_dir, session_id, fallback_profile)
            return fallback_profile

    def _persist_artifacts(self, session_dir: str, session_id: str, profile: CompactProfile) -> None:
        """Persists compact_profile.json, initial_suggestions.json, and static_questions.json."""
        os.makedirs(session_dir, exist_ok=True)

        # 1. compact_profile.json (Additive Phase 1 artifact)
        profile_path = os.path.join(session_dir, "compact_profile.json")
        try:
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"[PreCompiler] Error writing compact_profile.json: {e}")

        # 2. initial_suggestions.json (Exact Phase 2S contract)
        init_path = os.path.join(session_dir, "initial_suggestions.json")
        try:
            with open(init_path, "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": str(session_id),
                    "initial_suggestions": profile.initial_questions,
                    "generation_count": 1,
                    "generated_at": profile.compiled_at
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"[PreCompiler] Error writing initial_suggestions.json: {e}")

        # 3. static_questions.json (Exact Phase 2X contract)
        static_path = os.path.join(session_dir, "static_questions.json")
        try:
            with open(static_path, "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": str(session_id),
                    "scenario_questions": profile.scenario_questions,
                    "verification_questions": profile.verification_questions,
                    "generation_count": 1,
                    "generated_at": profile.compiled_at
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"[PreCompiler] Error writing static_questions.json: {e}")

    def _enforce_count(self, items: List[str], target_count: int, category: str) -> List[str]:
        """Slices or pads list to guarantee exact target count."""
        if len(items) >= target_count:
            return items[:target_count]

        fallbacks = {
            "initial": [
                "Could you introduce yourself and walk us through your most significant engineering achievement?",
                "What aspects of this role and technology stack are you most excited to discuss today?"
            ],
            "scenario": [
                "How would you design a highly available, fault-tolerant system to handle high concurrency?",
                "Can you walk through your approach to identifying and resolving production database performance bottlenecks?",
                "How do you approach automated testing and continuous integration across distributed microservices?",
                "Describe how you would design a secure authentication and authorization mechanism for external APIs.",
                "How would you structure zero-downtime database schema migrations for a large-scale application?"
            ],
            "verification": [
                "Can you elaborate on your specific personal contributions to the core architecture in your latest project?",
                "What were the most challenging technical trade-offs you encountered, and why did you choose that solution?",
                "How did you validate and test the reliability claims mentioned in your resume?",
                "Can you describe an unforeseen production failure you faced and the exact steps you took to debug it?",
                "What technical decisions in your past projects would you approach differently if designing them today?"
            ]
        }
        res = list(items)
        pool = fallbacks.get(category, fallbacks["scenario"])
        idx = 0
        while len(res) < target_count:
            candidate = pool[idx % len(pool)]
            if candidate not in res:
                res.append(candidate)
            else:
                res.append(f"{candidate} (Variant {len(res) + 1})")
            idx += 1
        return res[:target_count]

    def _extract_candidate_name_fallback(self, resume: str) -> str:
        """Heuristic candidate name extraction from first lines of resume."""
        if not resume:
            return "Candidate"
        for line in resume.strip().splitlines()[:5]:
            clean = line.strip().strip("#*:-_")
            words = clean.split()
            if 1 < len(words) <= 4 and all(w.replace(".", "").isalpha() for w in words):
                if not any(kw in clean.lower() for kw in ("resume", "curriculum", "email", "phone", "profile")):
                    return clean
        return "Candidate"

    def _extract_target_role_fallback(self, jd: str) -> str:
        """Heuristic target role extraction from JD."""
        if not jd:
            return "Software Engineer"
        for line in jd.strip().splitlines()[:5]:
            clean = line.strip().strip("#*:-_")
            if any(term in clean.lower() for term in ("engineer", "developer", "architect", "scientist", "analyst", "manager")):
                return clean[:60]
        return "Software Engineer"

    def _extract_skills_fallback(self, jd: str, resume: str) -> List[str]:
        """Heuristic tech skill extraction."""
        common = [
            "Python", "FastAPI", "React", "Node.js", "Docker", "Kubernetes",
            "PostgreSQL", "MongoDB", "AWS", "GCP", "Azure", "CI/CD",
            "Redis", "Microservices", "REST APIs", "GraphQL", "Git"
        ]
        combined = f"{jd} {resume}".lower()
        found = [s for s in common if s.lower() in combined]
        return found if len(found) >= 5 else common[:8]

    def _extract_claims_fallback(self, resume: str) -> List[str]:
        """Extracts candidate claims from resume bullet points."""
        claims = []
        if resume:
            for line in resume.strip().splitlines():
                clean = line.strip()
                if clean.startswith(("-", "•", "*")) and len(clean) > 25:
                    claims.append(clean.lstrip("-•* ").strip())
                    if len(claims) >= 5:
                        break
        return claims or [
            "Backend architecture development",
            "API design and implementation",
            "Database query optimization"
        ]

    def _build_default_profile(self, session_id: str, jd: str = "", resume: str = "") -> CompactProfile:
        """Builds valid deterministic default CompactProfile."""
        return CompactProfile(
            session_id=str(session_id),
            candidate_name=self._extract_candidate_name_fallback(resume),
            target_role=self._extract_target_role_fallback(jd),
            core_skills=self._extract_skills_fallback(jd, resume),
            project_claims=self._extract_claims_fallback(resume),
            initial_questions=self._enforce_count([], 2, "initial"),
            scenario_questions=self._enforce_count([], 5, "scenario"),
            verification_questions=self._enforce_count([], 5, "verification"),
            compiled_at=datetime.datetime.now().isoformat()
        )
