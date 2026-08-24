"""HTTP Client for Copilot Service Communication"""

import httpx
from typing import Optional, Dict, Any
from loguru import logger


class CopilotClient:
    """Client for communicating with the Copilot service"""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or "http://localhost:8000"
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
    
    async def start_copilot_session(
        self,
        jd: str,
        resume: str,
        custom_prompt: str = "",
        interview_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Start a new copilot session"""
        try:
            response = await self.client.post(
                "/api/copilot/start",
                json={
                    "jd": jd,
                    "resume": resume,
                    "custom_prompt": custom_prompt,
                    "interview_session_id": interview_session_id
                }
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to start copilot session: {e}")
            raise
    
    async def stop_copilot_session(self, session_id: str) -> Dict[str, Any]:
        """Stop a copilot session"""
        try:
            response = await self.client.post(
                f"/api/copilot/{session_id}/stop"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to stop copilot session: {e}")
            raise
    
    async def get_copilot_status(self, session_id: str) -> Dict[str, Any]:
        """Get copilot session status"""
        try:
            response = await self.client.get(
                f"/api/copilot/{session_id}/status"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to get copilot status: {e}")
            raise
    
    async def add_transcript(
        self,
        session_id: str,
        speaker: str,
        text: str
    ) -> Dict[str, Any]:
        """Add transcript entry to copilot session"""
        try:
            response = await self.client.post(
                f"/api/copilot/{session_id}/transcript",
                json={"speaker": speaker, "text": text}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to add transcript: {e}")
            raise
    
    async def finalize_report(self, session_id: str) -> Dict[str, Any]:
        """Finalize copilot session report"""
        try:
            response = await self.client.post(
                f"/api/copilot/{session_id}/finalize"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to finalize copilot report: {e}")
            raise
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
