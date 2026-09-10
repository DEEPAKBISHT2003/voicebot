import asyncio
import os
import sys

# Ensure src directory and project root are in sys.path for direct script execution
_current_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(_src_dir)))

for _p in [_src_dir, _project_root]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import (
    BACKEND_HOST,
    BACKEND_WS_BASE,
    BOT_DISPLAY_NAME,
    BOT_HEADLESS,
    BOT_PREJOIN_TIMEOUT_MS,
    BOT_ROLE,
    BrowserConfig,
    COPILOT_PORT,
    LOCAL_AUDIO_WS_BASE,
    MIA_INITIAL_BUFFER_MS,
    MIA_JOIN_ONLY,
    MIA_RECOVERY_BUFFER_MS,
    USE_SHARED_NAMESPACE,
    config,
)
from teams import TeamsBrowser, TeamsMeeting
from audio import AudioBridge, UNIFIED_BROWSER_AUDIO_JS, periodic_injector, start_localhost_proxy
from loguru import logger


async def run_bot(meeting_url: str, session_id: str):
    """Orchestrates TeamsBrowser, TeamsMeeting, and AudioBridge for live meeting sessions."""
    bot_role = BOT_ROLE
    browser_ws_url = BrowserConfig.get_ws_url(session_id, bot_role)

    logger.info(f"[AudioWS] target URL: {browser_ws_url} (bot_role={bot_role})")
    logger.info(f"[TeamsBot] Connecting Playwright bot to meeting: {meeting_url}")
    logger.info(f"[TeamsBot] Streaming audio back via CSP-compliant WebSocket: {browser_ws_url}")

    # Set up Audio and WebSocket bridge
    audio_bridge = AudioBridge(browser_ws_url=browser_ws_url)
    await audio_bridge.setup_proxy_if_needed(use_shared_namespace=USE_SHARED_NAMESPACE)

    formatted_unified_js = audio_bridge.get_formatted_js()

    async with TeamsBrowser(
        headless=BOT_HEADLESS,
        browser_ws_url=browser_ws_url,
        backend_ws_base=BACKEND_WS_BASE,
    ) as teams_browser:
        init_script = None if MIA_JOIN_ONLY else formatted_unified_js
        page = await teams_browser.create_page(init_script=init_script)

        meeting = TeamsMeeting(
            page=page,
            meeting_url=meeting_url,
            session_id=session_id,
            bot_display_name=BOT_DISPLAY_NAME,
            prejoin_timeout_ms=BOT_PREJOIN_TIMEOUT_MS,
            mia_join_only=MIA_JOIN_ONLY,
            browser_ws_url=browser_ws_url,
            unified_browser_audio_js=formatted_unified_js,
        )

        # 1. Navigate to Teams meeting and select web join option
        await meeting.open_and_select_web()

        # 2. Start background periodic JS interceptor injector only if not MIA_JOIN_ONLY
        if not MIA_JOIN_ONLY:
            audio_bridge.start_injector(page)

        # 3. Handle pre-join credentials and join request
        await meeting.handle_prejoin_and_join()

        # 4. Run main meeting polling and lifecycle loop
        try:
            await meeting.run_lifecycle_loop()
        except asyncio.CancelledError:
            logger.info("[TeamsBot] Stopping Teams observer bot.")
        finally:
            audio_bridge.stop_injector()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python teams_bot.py <meeting_url> <session_id>")
        sys.exit(1)

    m_url = sys.argv[1]
    s_id = sys.argv[2]

    asyncio.run(run_bot(m_url, s_id))
