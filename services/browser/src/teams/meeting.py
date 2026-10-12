import asyncio
import os
import time
import urllib.parse
from typing import Tuple, Dict, Any, Optional, List
from playwright.async_api import Page
from loguru import logger

try:
    import config
    from config import (
        ENABLE_AUTO_DISCONNECT_ALONE,
        AUTO_DISCONNECT_ALONE_GRACE_SECONDS,
        AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS,
        AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS,
    )
except ImportError:
    try:
        from services.browser.src import config
        from services.browser.src.config import (
            ENABLE_AUTO_DISCONNECT_ALONE,
            AUTO_DISCONNECT_ALONE_GRACE_SECONDS,
            AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS,
            AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS,
        )
    except ImportError:
        config = None
        ENABLE_AUTO_DISCONNECT_ALONE = True
        AUTO_DISCONNECT_ALONE_GRACE_SECONDS = 30.0
        AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS = 3
        AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS = 15.0


def is_bot_participant(name: str, bot_display_name: str) -> bool:
    """
    Determines whether a participant display name corresponds to the Appz Meeting Observer bot.
    Case-insensitive, normalizes whitespace, and matches known bot identifier tokens.
    """
    n = (name or "").strip().lower()
    b = (bot_display_name or "").strip().lower()
    if not n:
        return False
    if n == b or b in n or n in b:
        return True
    bot_tokens = [
        "meeting observer",
        "appz observer",
        "appzlogic observer",
        "appz interviewer",
        "appzlogic interviewer",
        "mia - appz",
        "mia - interviewer",
        "appz meeting",
        "observer",
        "mia",
    ]
    return any(token in n for token in bot_tokens)


def classify_participant_state(
    dom_data: Dict[str, Any],
    bot_display_name: str,
    last_speech_seconds_ago: float,
    recent_speech_window: float = 15.0,
) -> Tuple[str, int, Dict[str, Any]]:
    """
    Deterministically evaluates meeting participant presence from extracted DOM telemetry and speech safeguards.
    Returns:
        Tuple of (status, human_count, details)
        status values:
            - 'MEETING_ENDED': Teams confirmed meeting ended / call ended screen
            - 'HUMAN_PRESENT': >= 1 human participant reliably detected or recent speech detected
            - 'CONFIRMED_EMPTY': Bot is confirmed alone (0 humans)
            - 'UNCERTAIN': Stale, unavailable, or contradictory data (timer must reset/suspend)
    """
    # 1. Explicit meeting-ended screen detection
    if dom_data.get("isEnded"):
        return "MEETING_ENDED", 0, {"reason": "teams_ended_screen"}

    # 2. Recent speech safeguard: if speech/captions arrived recently, human is definitively present
    if last_speech_seconds_ago < recent_speech_window:
        return "HUMAN_PRESENT", 1, {
            "reason": "recent_speech_safeguard",
            "last_speech_seconds_ago": round(last_speech_seconds_ago, 2),
            "seconds_ago": round(last_speech_seconds_ago, 2),
        }

    # 3. If in-meeting indicators (hangup button, canvas) are missing, check if roster was found
    is_in_meeting = dom_data.get("isInMeeting", True)  # default True when inspecting participant DOM
    if not is_in_meeting:
        return "UNCERTAIN", -1, {"reason": "in_meeting_elements_missing"}

    # 4. Filter participant names from canvas/tiles against bot identity
    raw_names = dom_data.get("participantNames") or dom_data.get("rawNames", [])
    human_names = [n for n in raw_names if not is_bot_participant(n, bot_display_name)]
    bot_names = [n for n in raw_names if is_bot_participant(n, bot_display_name)]

    roster_badge_count = dom_data.get("rosterBadgeCount")
    roster_button_found = dom_data.get("rosterButtonFound", False)
    tile_count = dom_data.get("tileCount", 0)

    # 5. Check for human presence
    if len(human_names) > 0:
        return "HUMAN_PRESENT", len(human_names), {
            "humans": human_names,
            "source": "participant_tiles",
        }

    if roster_badge_count is not None and roster_badge_count > 1:
        return "HUMAN_PRESENT", roster_badge_count - 1, {
            "total_count": roster_badge_count,
            "humans_estimated": roster_badge_count - 1,
            "source": "roster_badge",
        }

    # 6. Check for confirmed empty (bot alone in meeting)
    # Case 0: Explicit Teams alone banner / notification detected ("You're the only one here", etc.)
    if dom_data.get("aloneBanner"):
        return "CONFIRMED_EMPTY", 0, {
            "reason": "teams_alone_banner_detected",
            "banner": dom_data.get("aloneBannerText", ""),
        }

    # Case A: Roster badge explicitly indicates exactly 1 participant (which is the bot itself)
    if roster_badge_count == 1:
        return "CONFIRMED_EMPTY", 0, {
            "reason": "roster_badge_equals_1",
            "bot_only": True,
            "bot_detected": bot_names,
        }

    # Case B: Participant tiles were successfully read and contain ONLY the bot (zero humans)
    if len(raw_names) > 0 and len(human_names) == 0 and len(bot_names) > 0:
        return "CONFIRMED_EMPTY", 0, {
            "reason": "tiles_contain_only_bot",
            "bot": bot_names,
        }

    # Case C: Roster button found with 0 badge and 0 tiles
    if roster_button_found and tile_count == 0 and roster_badge_count == 0:
        return "CONFIRMED_EMPTY", 0, {
            "reason": "roster_count_zero",
        }

    # Case D: Roster button found, zero tiles, no human participants, and no count > 1
    # (Teams Web displays no numeric badge when only 1 participant is present in the meeting)
    if roster_button_found and tile_count == 0 and len(human_names) == 0 and (roster_badge_count is None or roster_badge_count <= 1):
        return "CONFIRMED_EMPTY", 0, {
            "reason": "roster_found_zero_tiles_no_humans",
            "roster_badge_count": roster_badge_count,
        }

    # 7. Unreadable, incomplete, or ambiguous roster state: treat as UNCERTAIN
    # An unreadable roster must never be treated as proof that the meeting is empty.
    return "UNCERTAIN", -1, {
        "reason": "unreadable_or_missing_roster_data",
        "rosterButtonFound": roster_button_found,
        "rosterBadgeCount": roster_badge_count,
        "tileCount": tile_count,
    }



async def is_really_in_meeting(page: Page) -> Tuple[bool, str, Dict[str, bool]]:
    """
    Authoritative state detector distinguishing:
    PREJOIN, LOBBY, ADMITTING, IN_MEETING, DISCONNECTED
    Returns: (is_meeting: bool, state_name: str, evidence: dict)
    """
    logger.debug("[MIA STATE TIMING] is_really_in_meeting start")
    t0 = time.monotonic()

    evidence = {
        "prejoin_input": False,
        "prejoin_join_btn": False,
        "lobby_text": False,
        "admitting_text": False,
        "hangup_btn": False,
        "toolbar_mic": False,
        "roster_or_canvas": False,
        "ended_screen": False,
    }

    try:
        # Order frames: main_frame first, then any sub-frames
        main_f = page.main_frame
        ordered_frames = [main_f] + [f for f in page.frames if f != main_f]

        for frame in ordered_frames:
            try:
                # 1. In-meeting indicators (check first as they are most frequent during meetings)
                if not evidence["hangup_btn"]:
                    hangup_el = frame.locator(
                        "button#hangup-button, "
                        "button[id='hangup-button'], "
                        "button[title='Leave'], "
                        "button[title*='Leave' i], "
                        "button[data-tid='hangup-button'], "
                        "button[data-tid='leave-call-button'], "
                        "button[aria-label*='Leave' i], "
                        "button[aria-label*='Hang up' i]"
                    ).first
                    if await hangup_el.is_visible(timeout=50):
                        evidence["hangup_btn"] = True

                if not evidence["toolbar_mic"]:
                    mic_el = frame.locator(
                        "button#aria-key-toolbar-microphone, "
                        "button[data-tid='microphone-button'], "
                        "button[aria-label*='microphone' i], "
                        "button[title*='mic' i]"
                    ).first
                    if await mic_el.is_visible(timeout=50):
                        evidence["toolbar_mic"] = True

                if not evidence["roster_or_canvas"]:
                    roster_canvas = frame.locator(
                        "button[data-tid='roster-button'], "
                        "[data-tid='call-canvas'], "
                        ".calling-screen, "
                        "button[aria-label*='people' i], "
                        "button[title*='people' i]"
                    ).first
                    if await roster_canvas.is_visible(timeout=50):
                        evidence["roster_or_canvas"] = True

                # Early short-circuit: if we already have strong in-meeting evidence, skip remaining checks
                if evidence["hangup_btn"] or (evidence["toolbar_mic"] and evidence["roster_or_canvas"]):
                    break

                # 2. Prejoin indicators
                if not evidence["prejoin_input"]:
                    name_input = frame.locator(
                        "input[data-tid='prejoin-display-name-input'], input[placeholder*='Type your name' i]"
                    ).first
                    if await name_input.is_visible(timeout=50) and await name_input.is_enabled():
                        evidence["prejoin_input"] = True

                if not evidence["prejoin_join_btn"]:
                    prejoin_join_btn = frame.locator(
                        "button#prejoin-join-button, button[data-tid='prejoin-join-button']"
                    ).first
                    if await prejoin_join_btn.is_visible(timeout=50) and await prejoin_join_btn.is_enabled():
                        evidence["prejoin_join_btn"] = True

                # 3. Lobby / Admitting indicators
                if not evidence["lobby_text"]:
                    lobby_el = frame.locator(
                        "text='When the meeting starts', text='let people know you\\'re waiting', text='let you in soon', text='Waiting for someone', [data-tid*='lobby']"
                    ).first
                    if await lobby_el.is_visible(timeout=50):
                        evidence["lobby_text"] = True

                if not evidence["admitting_text"]:
                    admitting_el = frame.locator(
                        "text='Admitting...', text='Getting things ready', text='Joining...', [data-tid*='admitting']"
                    ).first
                    if await admitting_el.is_visible(timeout=50):
                        evidence["admitting_text"] = True

                # 4. Disconnected / ended screen
                if not evidence["ended_screen"]:
                    ended_el = frame.locator(
                        "[data-tid='call-ended'], button[data-tid='rejoin-button'], div:has-text('You left the meeting')"
                    ).first
                    if await ended_el.is_visible(timeout=50):
                        evidence["ended_screen"] = True
                        break
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[MIA STATE DETECT ERROR] {e}")

    # State Classification Logic
    in_meeting_signals = sum([evidence["hangup_btn"], evidence["toolbar_mic"], evidence["roster_or_canvas"]])

    if evidence["ended_screen"]:
        state = "DISCONNECTED"
        is_in = False
    elif evidence["hangup_btn"] or in_meeting_signals >= 2:
        state = "IN_MEETING"
        is_in = True
    elif evidence["prejoin_input"] or evidence["prejoin_join_btn"]:
        state = "PREJOIN"
        is_in = False
    elif evidence["lobby_text"]:
        state = "LOBBY"
        is_in = False
    elif evidence["admitting_text"]:
        state = "ADMITTING"
        is_in = False
    else:
        state = "JOINING"
        is_in = False

    duration = time.monotonic() - t0
    logger.debug(f"[MIA STATE TIMING] is_really_in_meeting duration={duration:.2f}s")
    return is_in, state, evidence


async def run_in_meeting_diagnostics(page: Page) -> None:
    """
    Asynchronous, non-blocking diagnostics and control for in-meeting mic & camera.
    Runs separately from the main lifecycle loop so state polling is never delayed.
    """
    logger.info("[MIA DIAGNOSTICS] started")
    diag_start = time.monotonic()
    try:
        all_frames = page.frames
        teams_muted = False
        track_enabled = True

        for frame in all_frames:
            try:
                in_meeting_mic = frame.locator(
                    "button#aria-key-toolbar-microphone, "
                    "button[data-tid='microphone-button'], "
                    "button[aria-label*='microphone' i], "
                    "button[aria-label*='mic' i], "
                    "button[data-tid='toggle-mute']"
                ).first
                if await in_meeting_mic.is_visible(timeout=300):
                    label = (await in_meeting_mic.get_attribute("aria-label") or "").lower()
                    pressed = (await in_meeting_mic.get_attribute("aria-pressed") or "").lower()
                    if "unmute" in label or pressed == "false":
                        teams_muted = True
                        logger.info(f"[MIA MIC DIAG] Teams mic MUTED in UI (label='{label}', pressed='{pressed}'). Unmuting...")
                        try:
                            await in_meeting_mic.evaluate("el => el.click()")
                            await in_meeting_mic.click(force=True)
                            await page.keyboard.press("Control+Shift+M")
                            logger.info("[TeamsBot] Triggered Teams mic UNMUTE via click + Ctrl+Shift+M shortcut.")
                        except Exception:
                            pass
                        break
                    else:
                        teams_muted = False
                        break
            except Exception:
                pass

        logger.info(f"[MIA MIC] pipecat_enabled=true track_enabled={track_enabled} teams_muted={teams_muted}")

        # In-meeting camera click disabled for Phase 5 (audio-only mode to prevent mid-call renegotiation)
        logger.debug("[MIA DIAGNOSTICS] In-meeting camera click bypassed (audio-only mode).")

    except Exception as de:
        logger.warning(f"[MIA DIAGNOSTICS] Error during diagnostics: {de}")
    finally:
        diag_dur = time.monotonic() - diag_start
        logger.info(f"[MIA DIAGNOSTICS] completed duration={diag_dur:.2f}s")


class TeamsMeeting:
    """Encapsulates MS Teams meeting automation flows including pre-join, admission, and lifecycle polling."""

    def __init__(
        self,
        page: Page,
        meeting_url: str,
        session_id: str,
        bot_display_name: str = "Mia - Appz Interviewer",
        prejoin_timeout_ms: int = 75000,
        mia_join_only: bool = False,
        browser_ws_url: str = "",
        unified_browser_audio_js: str = "",
    ):
        self.page = page
        self.meeting_url = meeting_url
        self.session_id = session_id
        self.bot_display_name = bot_display_name
        self.prejoin_timeout_ms = prejoin_timeout_ms
        self.mia_join_only = mia_join_only
        self.browser_ws_url = browser_ws_url
        self.unified_browser_audio_js = unified_browser_audio_js

        self.debug_dir = os.path.join(os.getcwd(), "interviews", session_id)
        os.makedirs(self.debug_dir, exist_ok=True)
        self.service_off_flag_path = os.path.join(self.debug_dir, "service_off.flag")
        self.captions_enabled = False

        # Auto-disconnect after alone configuration
        self.enable_auto_disconnect_alone = getattr(config, "ENABLE_AUTO_DISCONNECT_ALONE", ENABLE_AUTO_DISCONNECT_ALONE) if config else ENABLE_AUTO_DISCONNECT_ALONE
        self.alone_grace_period_seconds = float(getattr(config, "AUTO_DISCONNECT_ALONE_GRACE_SECONDS", AUTO_DISCONNECT_ALONE_GRACE_SECONDS) if config else AUTO_DISCONNECT_ALONE_GRACE_SECONDS)
        self.consecutive_empty_required = int(getattr(config, "AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS", AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS) if config else AUTO_DISCONNECT_CONSECUTIVE_EMPTY_OBSERVATIONS)
        self.recent_speech_window_seconds = float(getattr(config, "AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS", AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS) if config else AUTO_DISCONNECT_RECENT_SPEECH_WINDOW_SECONDS)

        # State tracking for alone detection
        self.alone_timer_start: Optional[float] = None
        self.consecutive_empty_count: int = 0
        self.last_speech_time: float = 0.0
        self.is_disconnecting: bool = False

        # Attach console listener to catch live caption / speech activity
        try:
            self.page.on("console", self._handle_browser_console_event)
        except Exception:
            pass

    def _handle_browser_console_event(self, msg: Any) -> None:
        """Tracks speech/caption activity from browser console messages."""
        try:
            txt = str(msg.text if hasattr(msg, "text") else msg)
            if "[MIA CAPTIONS] Captured" in txt or "caption_sequence" in txt:
                self.last_speech_time = time.monotonic()
        except Exception:
            pass

    def record_speech_activity(self) -> None:
        """Updates the last speech detection timestamp to prevent premature disconnect."""
        self.last_speech_time = time.monotonic()


    async def dismiss_media_prompt_if_present(self) -> bool:
        """Detects and dismisses the 'Continue without audio or video' confirmation modal if shown by Teams."""
        for target in [self.page] + self.page.frames:
            try:
                modal_btn = target.locator(
                    "button:has-text('Continue without audio or video'), "
                    "[data-tid='prejoin-dialog-continue-without-audio'], "
                    "button[aria-label*='Continue without audio or video' i], "
                    "[aria-label*='Continue without audio or video' i]"
                ).first
                if await modal_btn.is_visible(timeout=500):
                    logger.info("[TeamsBot] Detected 'Continue without audio or video' popup; clicking to dismiss...")
                    try:
                        await modal_btn.click(timeout=2000, force=True)
                    except Exception:
                        await modal_btn.evaluate("el => el.click()")
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                pass
        return False

    async def open_and_select_web(self) -> None:
        """Navigate to meeting URL, save landing screenshot, and select Web Join."""
        for goto_attempt in range(1, 4):
            try:
                logger.info(f"[TeamsBot] Opening meeting URL (attempt {goto_attempt}/3)...")
                await self.page.goto(self.meeting_url, timeout=35000)
                logger.info("[TeamsBot] Teams page loaded")
                break
            except Exception as ge:
                logger.warning(f"[TeamsBot] page.goto failed on attempt {goto_attempt}: {ge}")
                if goto_attempt == 3:
                    raise
                await asyncio.sleep(2.0)

        await asyncio.sleep(5.0)  # Allow landing page to load fully

        # Stage 01 screenshot
        try:
            await self.page.screenshot(path=os.path.join(self.debug_dir, "01_teams_loaded.png"))
            await self.page.screenshot(path=os.path.join(self.debug_dir, "teams_bot_landing.png"))
            logger.info("[TeamsBot] Saved 01_teams_loaded.png screenshot.")
        except Exception as se:
            logger.warning(f"[TeamsBot] Failed to save 01_teams_loaded screenshot: {se}")

        # Automate Teams UI Guest Selection Flow
        try:
            logger.info("[TeamsBot] Selecting Web Join option...")
            clicked = False
            for target in [self.page] + self.page.frames:
                try:
                    btn = target.locator(
                        "button[data-tid='joinOnWeb'], "
                        "[data-tid='joinOnWeb'], "
                        "button[aria-label*='Join meeting from this browser' i], "
                        "[aria-label*='Join meeting from this browser' i], "
                        "button:has-text('Continue on this browser'), "
                        "button:has-text('Join on the web'), "
                        "button:has-text('Continue in this browser'), "
                        "[aria-label*='Join on the web' i], "
                        "[data-tid='join-on-web']"
                    ).first
                    if await btn.is_visible(timeout=2000):
                        try:
                            await btn.click(timeout=5000, force=True)
                        except Exception:
                            await btn.evaluate("el => el.click()")
                        clicked = True
                        logger.info("[TeamsBot] Clicked 'Continue on this browser' button successfully.")
                        break
                except Exception:
                    pass

            if not clicked:
                # Fallback to direct locator wait & click
                web_join_button = self.page.locator(
                    "button[data-tid='joinOnWeb'], "
                    "[data-tid='joinOnWeb'], "
                    "button[aria-label*='Join meeting from this browser' i], "
                    "[aria-label*='Join meeting from this browser' i], "
                    "button:has-text('Continue on this browser'), "
                    "button:has-text('Join on the web'), "
                    "button:has-text('Continue in this browser'), "
                    "[aria-label*='Join on the web' i], "
                    "[data-tid='join-on-web']"
                )
                await web_join_button.first.click(timeout=10000, force=True)
                logger.info("[TeamsBot] Clicked 'Continue on this browser' via fallback locator.")
            await asyncio.sleep(5.0)  # Wait for prep room to load
        except Exception as e:
            logger.warning(f"[TeamsBot] Bypassing Web Join select step (already on lobby page or redirected): {e}")
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "teams_bot_join_redirect.png"))
            except Exception:
                pass

    async def handle_prejoin_and_join(self) -> None:
        """Handle passcode, prejoin name entry, camera/mic configuration, and submit join request."""
        # Injects WebRTC interception JS code into page initialization only if not MIA_JOIN_ONLY
        if not self.mia_join_only and self.unified_browser_audio_js:
            formatted_js = self.unified_browser_audio_js.replace("%WS_URL%", self.browser_ws_url)
            await self.page.add_init_script(formatted_js)
            try:
                await self.page.evaluate(formatted_js)
                logger.info("[TeamsBot] WebRTC interceptor evaluated immediately on page context.")
            except Exception as ee:
                logger.warning(f"[TeamsBot] Direct evaluation of interceptor script skipped/failed: {ee}")

        # Enter guest name in name field
        try:
            logger.info("[TeamsBot] Waiting for credentials page to load (can take up to 45-60s)...")

            # Stage 01: Pre-join detection with retries for Teams loading glitches
            start_wait = asyncio.get_event_loop().time()
            last_reclick_time = start_wait
            last_reload_time = start_wait
            max_wait_sec = 90.0
            target_name_input = None

            logger.info(f"[TeamsBot] Waiting for pre-join screen (timeout={max_wait_sec}s)...")

            while (asyncio.get_event_loop().time() - start_wait) < max_wait_sec:
                # 0. Check for Teams "Oops, app failed to load!" error page
                for target in [self.page] + self.page.frames:
                    try:
                        retry_btn = target.locator(
                            "button:has-text('Retry'), [aria-label*='Retry' i], button:has-text('Clear cache and retry')"
                        ).first
                        if await retry_btn.is_visible(timeout=300):
                            logger.warning("[TeamsBot] Detected Teams 'Oops, app failed to load!' screen. Clicking Retry...")
                            try:
                                await retry_btn.click(timeout=2000, force=True)
                            except Exception:
                                await retry_btn.evaluate("el => el.click()")
                            await asyncio.sleep(3.0)
                            break
                    except Exception:
                        pass

                # 1. Check for Teams Meeting Passcode input field across all frames
                for target in [self.page] + self.page.frames:
                    try:
                        passcode_input = target.locator(
                            "input[data-tid='meeting-passcode'], input[placeholder*='passcode' i], input[placeholder*='password' i]"
                        )
                        if await passcode_input.count() > 0 and await passcode_input.first.is_visible(timeout=300):
                            parsed = urllib.parse.urlparse(self.meeting_url)
                            params = urllib.parse.parse_qs(parsed.query)
                            passcode = params.get("p", [""])[0]
                            if passcode:
                                logger.info(f"[TeamsBot] Entering meeting passcode from URL: {passcode}")
                                await passcode_input.first.fill(passcode)
                                await passcode_input.first.press("Enter")
                                await asyncio.sleep(2.0)
                                break
                    except Exception:
                        pass

                # 2. Check for Name Input field across all frames
                for target in [self.page] + self.page.frames:
                    try:
                        name_locator = target.locator(
                            "input[data-tid='prejoin-display-name-input'], "
                            "input[placeholder='Type your name'], "
                            "input.fui-Input__input, "
                            "input[placeholder*='Type your name' i], "
                            "input[placeholder*='Enter name' i], "
                            "input[aria-label*='Type your name' i], "
                            "input[aria-label*='Enter name' i]"
                        )
                        if await name_locator.count() > 0 and await name_locator.first.is_visible(timeout=300):
                            target_name_input = name_locator.first
                            break
                    except Exception:
                        pass

                if target_name_input:
                    break

                # 3. If landing button is still present after 5s, re-click it in case initial click was missed
                now = asyncio.get_event_loop().time()
                if (now - start_wait) > 5.0 and (now - last_reclick_time) > 8.0:
                    for target in [self.page] + self.page.frames:
                        try:
                            web_btn = target.locator(
                                "button[data-tid='joinOnWeb'], "
                                "[data-tid='joinOnWeb'], "
                                "button[aria-label*='Join meeting from this browser' i], "
                                "[aria-label*='Join meeting from this browser' i], "
                                "button:has-text('Continue on this browser'), "
                                "button:has-text('Join on the web'), "
                                "button:has-text('Continue in this browser'), "
                                "[aria-label*='Join on the web' i], "
                                "[data-tid='join-on-web']"
                            ).first
                            if await web_btn.is_visible(timeout=300):
                                logger.info("[TeamsBot] 'Continue on this browser' still visible; re-clicking...")
                                try:
                                    await web_btn.click(timeout=2000, force=True)
                                except Exception:
                                    await web_btn.evaluate("el => el.click()")
                                last_reclick_time = now
                                break
                        except Exception:
                            pass

                await asyncio.sleep(1.5)

            if not target_name_input:
                raise TimeoutError(f"Pre-join name input not found within {max_wait_sec}s timeout.")

            logger.info("[TeamsBot] Pre-join screen detected")
            await self.dismiss_media_prompt_if_present()

            # Stage 02: Pre-join screen screenshot
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "02_prejoin.png"))
            except Exception:
                pass

            # Ensure Video Camera is toggled OFF for privacy
            for target in [self.page] + self.page.frames:
                try:
                    camera_toggle = target.locator("[aria-label*='camera' i], [aria-label*='video' i], [data-tid*='video']").first
                    if await camera_toggle.is_visible(timeout=1000):
                        label = (await camera_toggle.get_attribute("aria-label") or "").lower()
                        camera_is_on = "turn camera off" in label or ("camera" in label and "turn camera on" not in label)
                        if camera_is_on:
                            await camera_toggle.click()
                            logger.info("[TeamsBot] Video camera toggled OFF.")
                        else:
                            logger.info(f"[TeamsBot] Camera already OFF (label: '{label}').")
                        break
                except Exception:
                    pass

            # Ensure Microphone is toggled ON (Unmuted) so Teams WebRTC receives Mia's audio
            for target in [self.page] + self.page.frames:
                try:
                    mic_switch = target.locator(
                        "input[data-cid*='toggle-mute'], "
                        "input[data-tid='toggle-mute'], "
                        "input[title*='Mute mic' i], "
                        "input[title*='Unmute mic' i], "
                        "[role='switch'][data-tid*='toggle-mute']"
                    ).first
                    if await mic_switch.is_visible(timeout=1000):
                        data_cid = (await mic_switch.get_attribute("data-cid") or "").lower()
                        title = (await mic_switch.get_attribute("title") or "").lower()
                        label = (await mic_switch.get_attribute("aria-label") or "").lower()
                        is_checked = await mic_switch.is_checked() if await mic_switch.evaluate("e => e.tagName === 'INPUT'") else False

                        mic_is_off = "toggle-mute-false" in data_cid or ("unmute" in title) or ("unmute" in label) or not is_checked
                        if mic_is_off:
                            await mic_switch.click(force=True)
                            logger.info("[TeamsBot] Clicked Fluent UI mic switch ON (Unmuted).")
                        else:
                            logger.info("[TeamsBot] Fluent UI mic switch already ON (Unmuted).")
                        break
                except Exception:
                    pass

            # Fill name input field with fallbacks
            try:
                await target_name_input.click(timeout=3000, force=True)
                await target_name_input.fill(self.bot_display_name, timeout=5000)
            except Exception as fe:
                logger.warning(f"[TeamsBot] Playwright fill/click failed ({fe}); applying direct JS value assignment fallback...")
                await target_name_input.evaluate(
                    "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }",
                    self.bot_display_name,
                )

            try:
                await target_name_input.dispatch_event("input")
                await target_name_input.dispatch_event("change")
            except Exception:
                pass
            logger.info(f"[TeamsBot] Display name configured: {self.bot_display_name}")

            # Pre-join microphone switch check
            for frame in self.page.frames:
                try:
                    mic_switch = frame.locator(
                        "input[data-cid*='toggle-mute'], "
                        "input[data-tid='toggle-mute'], "
                        "input[title*='Mute mic' i], "
                        "input[title*='Unmute mic' i], "
                        "[role='switch'][data-tid*='toggle-mute']"
                    ).first
                    if await mic_switch.is_visible(timeout=300):
                        data_cid = (await mic_switch.get_attribute("data-cid") or "").lower()
                        title = (await mic_switch.get_attribute("title") or "").lower()
                        label = (await mic_switch.get_attribute("aria-label") or "").lower()
                        is_checked = await mic_switch.is_checked() if await mic_switch.evaluate("e => e.tagName === 'INPUT'") else False
                        if "toggle-mute-false" in data_cid or "unmute" in title or "unmute" in label or not is_checked:
                            await mic_switch.evaluate("el => el.click()")
                            await mic_switch.click(force=True)
                            logger.info("[TeamsBot] Clicked pre-join mic switch UNMUTED.")
                            break
                except Exception:
                    pass
            logger.info("[TeamsBot] Microphone configured")

            # Stage 03: Immediately before Join Now screenshot
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "03_before_join.png"))
            except Exception:
                pass

            # Click "Join Now" or "Join" button across all frames
            target_join_button = None
            for target in [self.page] + self.page.frames:
                try:
                    jb = target.locator(
                        "button#prejoin-join-button, "
                        "button[data-tid='prejoin-join-button'], "
                        "[id='prejoin-join-button'], "
                        "[data-tid='prejoin-join-button'], "
                        "button[aria-label='Join now'], "
                        "button[aria-label*='Join now' i], "
                        "button:has-text('Join now'), "
                        "button:has-text('Join meeting'), "
                        "button:has-text('Join')"
                    ).first
                    if await jb.is_visible(timeout=500):
                        target_join_button = jb
                        break
                except Exception:
                    pass

            if not target_join_button:
                target_join_button = self.page.locator("button#prejoin-join-button, button[data-tid='prejoin-join-button']").first

            logger.info("[TeamsBot] Join button detected")
            await self.dismiss_media_prompt_if_present()
            logger.info("[TeamsBot] Clicking Join Now")
            try:
                await target_join_button.wait_for(state="visible", timeout=5000)
                await target_join_button.click(timeout=5000, force=True)
                logger.info("[TeamsBot] Join request submitted via Join button click.")
            except Exception as jbe:
                logger.warning(f"[TeamsBot] Direct Join button click failed ({jbe}); attempting JS click & Enter key fallback...")
                try:
                    await target_join_button.evaluate("el => el.click()")
                    logger.info("[TeamsBot] Join request submitted via JS click.")
                except Exception:
                    await target_name_input.press("Enter")
                    logger.info("[TeamsBot] Join request submitted via Enter key press.")

            # If "Continue without audio or video" dialog popped up upon clicking Join, dismiss it and re-click Join
            if await self.dismiss_media_prompt_if_present():
                logger.info("[TeamsBot] Modal appeared after Join click; dismissed, re-submitting Join Now...")
                try:
                    await target_join_button.click(timeout=3000, force=True)
                except Exception:
                    await target_join_button.evaluate("el => el.click()")

            # Stage 04: Immediately after Join Now screenshot
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "04_after_join.png"))
            except Exception:
                pass

            # Stage 05: 5 seconds after Join screenshot
            logger.info("[TeamsBot] Waiting for meeting connection...")
            await asyncio.sleep(5.0)
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "05_after_join_5s.png"))
                await self.page.screenshot(path=os.path.join(self.debug_dir, "teams_bot_lobby.png"))
            except Exception:
                pass

            # Stage 06: Initial state assessment
            is_in, current_state, state_evidence = await is_really_in_meeting(self.page)
            logger.info(f"[MIA STATE] {current_state} | evidence={state_evidence}")
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "06_final_state.png"))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[TeamsBot] Failed to automate input names/joining: {e}")
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, "teams_bot_join_failed.png"))
                logger.info("[TeamsBot] Saved join failure screenshot to session directory.")
            except Exception:
                pass

    async def enable_native_captions(self, attempt: int = 1) -> bool:
        """
        Attempts to enable Teams Native Live Captions via the More menu after admission.
        Uses accessible, role, text, and data-tid selectors with full DOM logging and debug screenshots.
        """
        logger.info(f"[TeamsCaptions] Attempting to enable Teams Native Live Captions (attempt={attempt})...")
        try:
            # 1. Check if caption container already exists and has size
            container_visible = await self.page.evaluate('''() => {
                const c = document.querySelector('div[data-tid="closed-caption-renderer-wrapper"], div[aria-label="Live Captions"]');
                return !!(c && c.offsetHeight > 0);
            }''')
            if container_visible:
                logger.info("[TeamsCaptions] Caption container is already active and visible.")
                return True

            # 2. Locate More Actions button in main page or frames
            more_selectors = (
                "button#callingButtons-showMoreBtn, "
                "button[data-tid='more-actions-button'], "
                "button[aria-label*='More' i], "
                "button:has-text('More')"
            )
            more_btn = None
            target_page = self.page
            for frame in [self.page] + self.page.frames:
                try:
                    el = frame.locator(more_selectors).first
                    if await el.is_visible(timeout=500):
                        more_btn = el
                        target_page = frame
                        break
                except Exception:
                    pass

            if not more_btn:
                logger.warning(f"[TeamsCaptions] Could not find More button on attempt {attempt}.")
                return False

            # Click More button
            try:
                await more_btn.click(timeout=3000, force=True)
            except Exception:
                await more_btn.evaluate("el => el.click()")

            await asyncio.sleep(1.2)

            # Save debug screenshot of open menu
            try:
                await self.page.screenshot(path=os.path.join(self.debug_dir, f"debug_more_menu_attempt_{attempt}.png"))
            except Exception:
                pass

            # Inspect all menu items in popup
            menu_info = await self.page.evaluate('''() => {
                const all = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], button.fui-MenuItem, div.fui-MenuItem'));
                return all.map(el => ({
                    id: el.id,
                    text: (el.innerText || "").trim().replace(/\\n/g, " "),
                    ariaLabel: el.getAttribute('aria-label') || "",
                    dataTid: el.getAttribute('data-tid') || ""
                })).filter(x => x.text || x.ariaLabel || x.dataTid);
            }''')
            logger.info(f"[TeamsCaptions] Discovered {len(menu_info)} items in More menu: {[m['text'] for m in menu_info]}")

            # 3. Look for captions directly in open menu
            cc_selectors = (
                "[role*='menuitem']:has-text('Captions'), "
                "[role*='menuitem']:has-text('live captions'), "
                "[role*='menuitem']:has-text('Turn on live captions'), "
                "[role*='menuitem']:has-text('Show live captions'), "
                "button#closed-captions-button, "
                "[data-tid='closed-captions-button'], "
                "[aria-label*='captions' i]"
            )
            cc_el = target_page.locator(cc_selectors).first
            if await cc_el.is_visible(timeout=1000):
                logger.info("[TeamsCaptions] Found Live Captions toggle in menu. Clicking...")
                try:
                    await cc_el.click(timeout=3000, force=True)
                except Exception:
                    await cc_el.evaluate("el => el.click()")
                await asyncio.sleep(1.5)
                # Check if clicking opened a submenu (e.g. "Turn on live captions" inside Captions)
                try:
                    await self.page.screenshot(path=os.path.join(self.debug_dir, f"debug_after_cc_click_attempt_{attempt}.png"))
                except Exception:
                    pass
                submenu_items = await self.page.evaluate('''() => {
                    const all = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'));
                    return all.map(el => (el.innerText || "").trim()).filter(Boolean);
                }''')
                logger.info(f"[TeamsCaptions] Menu items after CC click: {submenu_items}")

                sub_turn_on = target_page.locator(
                    "[role*='menuitem']:has-text('Turn on'), "
                    "[role*='menuitem']:has-text('Turn on live captions'), "
                    "[role*='menuitem']:has-text('Turn on captions'), "
                    "[role*='menuitem']:has-text('English'), "
                    "[role*='menuitemcheckbox']"
                ).first
                if await sub_turn_on.is_visible(timeout=1000):
                    logger.info("[TeamsCaptions] Found submenu toggle item. Clicking...")
                    try:
                        await sub_turn_on.click(timeout=3000, force=True)
                    except Exception:
                        await sub_turn_on.evaluate("el => el.click()")
                    await asyncio.sleep(1.5)

                try:
                    await self.page.keyboard.press("Escape")
                except Exception:
                    pass
                return True

            # 4. If not directly in menu, check for "Language and speech" submenu
            lang_selectors = (
                "[role*='menuitem']:has-text('Language and speech'), "
                "[role*='menuitem']:has-text('Language'), "
                "[aria-label*='Language and speech' i]"
            )
            lang_el = target_page.locator(lang_selectors).first
            if await lang_el.is_visible(timeout=1000):
                logger.info("[TeamsCaptions] Found 'Language and speech' submenu. Opening...")
                try:
                    await lang_el.click(timeout=3000, force=True)
                except Exception:
                    await lang_el.evaluate("el => el.click()")
                await asyncio.sleep(1.0)

                # Now look for captions in submenu
                sub_cc = target_page.locator(cc_selectors).first
                if await sub_cc.is_visible(timeout=2000):
                    logger.info("[TeamsCaptions] Found Live Captions toggle in submenu! Clicking...")
                    try:
                        await sub_cc.click(timeout=3000, force=True)
                    except Exception:
                        await sub_cc.evaluate("el => el.click()")
                    await asyncio.sleep(2.0)
                    return True

            logger.warning(f"[TeamsCaptions] Neither Live Captions nor Language submenu found on attempt {attempt}.")
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        except Exception as e:
            logger.warning(f"[TeamsCaptions] Exception while attempting to enable captions: {e}")
            return False

    async def start_native_captions_task(self) -> None:
        """
        Background task started once IN_MEETING is confirmed:
        1. Waits for in-meeting toolbar to stabilize.
        2. Tries to enable Live Captions with retries.
        3. Injects native_captions.js with session parameters.
        """
        logger.info("[TeamsCaptions] Background native captions task started.")
        try:
            await asyncio.sleep(5.0)

            enabled = False
            for attempt in range(1, 6):
                logger.info(f"[TeamsCaptions] Enable captions attempt {attempt}/5...")
                enabled = await self.enable_native_captions(attempt=attempt)
                if enabled:
                    logger.info(f"[TeamsCaptions] Live Captions successfully enabled on attempt {attempt}!")
                    break
                await asyncio.sleep(3.5)

            # Read native_captions.js template
            js_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "browser_js", "native_captions.js")
            )
            if not os.path.exists(js_path):
                logger.error(f"[TeamsCaptions] native_captions.js not found at {js_path}")
                return

            with open(js_path, "r", encoding="utf-8") as f:
                js_content = f.read()

            # Determine WS and HTTP URLs
            ws_base = self.browser_ws_url.split("?")[0] if self.browser_ws_url else f"ws://localhost:8000/api/ws/copilot/{self.session_id}"
            if not ws_base.endswith(self.session_id):
                ws_base = f"ws://localhost:8000/api/ws/copilot/{self.session_id}"
            ws_url = f"{ws_base}?mode=native_captions"

            http_base = ws_base.replace("ws://", "http://").replace("wss://", "https://")
            http_url = http_base.replace("/api/ws/copilot/", "/api/copilot/") + "/native-captions"

            formatted_js = (
                js_content
                .replace("%SESSION_ID%", self.session_id)
                .replace("%WS_URL%", ws_url)
                .replace("%HTTP_URL%", http_url)
            )

            # Inject into page and all frames
            logger.info(f"[TeamsCaptions] Injecting native_captions.js into page (ws={ws_url})...")
            await self.page.evaluate(formatted_js)
            for frame in self.page.frames:
                if frame == self.page.main_frame:
                    continue
                try:
                    await frame.evaluate(formatted_js)
                except Exception:
                    pass

            self.captions_enabled = True
            logger.info("[TeamsCaptions] Native captions injection completed.")

        except Exception as e:
            logger.warning(f"[TeamsCaptions] Error in start_native_captions_task: {e}")

    async def detect_participants(self) -> Tuple[str, int, Dict[str, Any]]:
        """
        Inspects the DOM for participant indicators and correlates with speech activity across all frames.
        Returns: (state, count, details)
        where state is one of "CONFIRMED_EMPTY", "HUMAN_PRESENT", "UNCERTAIN".
        """
        try:
            # 1. Extract DOM participant indicators using JavaScript evaluation across all frames
            js_script = """
            (() => {
                let isEnded = false;
                let aloneBanner = false;
                let aloneBannerText = "";
                let rosterButtonFound = false;
                let rosterBadgeCount = -1;
                let rawNames = [];
                let tileCount = 0;

                // 0. Check for explicit call ended screen
                const endedEl = document.querySelector("[data-tid='call-ended'], button[data-tid='rejoin-button'], div[data-tid='calling-ended-screen']");
                if (endedEl) {
                    isEnded = true;
                }

                // 1. Check for alone in call banner / notification
                const aloneSelectors = [
                    "[data-tid='calling-empty-meeting-notification']",
                    "[data-tid='alone-in-call-message']",
                    "[data-tid='empty-meeting-notification']"
                ];
                for (const sel of aloneSelectors) {
                    const el = document.querySelector(sel);
                    if (el && (el.offsetWidth > 0 || el.offsetHeight > 0)) {
                        aloneBanner = true;
                        aloneBannerText = (el.innerText || sel).trim();
                        break;
                    }
                }
                if (!aloneBanner) {
                    const allDivs = document.querySelectorAll("div, p, span, h2, h3");
                    for (const el of allDivs) {
                        const txt = (el.textContent || "").trim();
                        if (
                            txt === "You're the only one here" ||
                            txt.includes("You're the only one here") ||
                            txt.includes("Looks like everyone else left") ||
                            txt === "You are the only person in this meeting" ||
                            txt.includes("Waiting for others to join")
                        ) {
                            if (el.offsetWidth > 0 || el.offsetHeight > 0) {
                                aloneBanner = true;
                                aloneBannerText = txt;
                                break;
                            }
                        }
                    }
                }

                // 2. Look for roster/people button and its badge/label count
                const rosterBtn = document.querySelector("button[data-tid='roster-button'], button#roster-button, button[aria-label*='people' i], button[aria-label*='show participants' i], button[aria-label*='participants' i]");
                if (rosterBtn) {
                    rosterButtonFound = true;
                    // Check text content or badge element
                    const badge = rosterBtn.querySelector(".badge, [data-tid='roster-badge'], span");
                    const txt = (badge ? badge.textContent : rosterBtn.textContent) || "";
                    const m = txt.match(/\\d+/);
                    if (m) {
                        rosterBadgeCount = parseInt(m[0], 10);
                    } else {
                        const aria = rosterBtn.getAttribute("aria-label") || "";
                        const m2 = aria.match(/\\d+/);
                        if (m2) {
                            rosterBadgeCount = parseInt(m2[0], 10);
                        }
                    }
                }

                // 3. Look for participant video/avatar tiles
                const tiles = document.querySelectorAll("[data-tid='video-tile'], [data-tid='participant-tile'], .participant-tile, [data-cid='roster-participant'], [data-tid='calling-participant-stream'], [data-tid='stream-tile'], [data-tid='avatar-tile']");
                tileCount = tiles.length;
                tiles.forEach(t => {
                    const aria = t.getAttribute("aria-label") || "";
                    const nameEl = t.querySelector("[data-tid='participant-name'], .participant-name, span");
                    const name = (nameEl ? nameEl.textContent : "") || aria;
                    if (name.trim()) {
                        rawNames.push(name.trim());
                    }
                });

                // Also check if native captions recorded recent speech timestamp in window
                const lastCaptionTime = window.__miaLastCaptionTime || 0;

                return {
                    isEnded,
                    aloneBanner,
                    aloneBannerText,
                    rosterButtonFound,
                    rosterBadgeCount,
                    rawNames,
                    tileCount,
                    lastCaptionTime
                };
            })()
            """

            dom_data = {
                "isEnded": False,
                "aloneBanner": False,
                "aloneBannerText": "",
                "rosterButtonFound": False,
                "rosterBadgeCount": -1,
                "rawNames": [],
                "tileCount": 0,
                "lastCaptionTime": 0,
                "isInMeeting": True,
            }

            main_f = self.page.main_frame
            ordered_frames = [main_f] + [f for f in self.page.frames if f != main_f]

            for frame in ordered_frames:
                try:
                    f_res = await frame.evaluate(js_script)
                    if not f_res or not isinstance(f_res, dict):
                        continue

                    if f_res.get("isEnded"):
                        dom_data["isEnded"] = True

                    if f_res.get("aloneBanner"):
                        dom_data["aloneBanner"] = True
                        dom_data["aloneBannerText"] = f_res.get("aloneBannerText", "")

                    if f_res.get("rosterButtonFound"):
                        dom_data["rosterButtonFound"] = True

                    bc = f_res.get("rosterBadgeCount", -1)
                    if bc >= 0:
                        dom_data["rosterBadgeCount"] = max(dom_data["rosterBadgeCount"], bc)

                    dom_data["tileCount"] += f_res.get("tileCount", 0)

                    names = f_res.get("rawNames", [])
                    if names:
                        dom_data["rawNames"].extend(names)

                    lct = f_res.get("lastCaptionTime", 0)
                    if lct and lct > dom_data["lastCaptionTime"]:
                        dom_data["lastCaptionTime"] = lct
                except Exception as frame_eval_err:
                    logger.debug(f"[AutoDisconnect] Frame evaluation skipped: {frame_eval_err}")

            # Deduplicate collected participant names
            if dom_data["rawNames"]:
                dom_data["rawNames"] = list(dict.fromkeys(dom_data["rawNames"]))

            # Check window lastCaptionTime if present to sync self.last_speech_time
            last_cap_time = dom_data.get("lastCaptionTime", 0)
            if last_cap_time and last_cap_time > 0:
                # Convert JS Date.now() ms to monotonic or compare elapsed
                cap_seconds_ago = (time.time() - (last_cap_time / 1000.0))
                if 0 <= cap_seconds_ago < self.recent_speech_window_seconds:
                    self.last_speech_time = time.monotonic() - cap_seconds_ago

            # Calculate seconds since last observed speech activity
            last_speech_seconds_ago = (time.monotonic() - self.last_speech_time) if self.last_speech_time > 0 else 999999.0

            return classify_participant_state(
                dom_data=dom_data,
                bot_display_name=self.bot_display_name,
                last_speech_seconds_ago=last_speech_seconds_ago,
                recent_speech_window=self.recent_speech_window_seconds
            )
        except Exception as e:
            logger.warning(f"[AutoDisconnect] Error during detect_participants: {e}")
            return "UNCERTAIN", -1, {"error": str(e)}

    async def trigger_auto_disconnect(self, reason: str = "bot_alone_30s") -> None:
        """
        Executes unified service-off teardown when the bot is confirmed alone.
        Guaranteed to follow the exact same teardown mechanism as manual Disconnect:
        1. Leaves Teams meeting immediately via leave_meeting (clicks Leave/hangup).
        2. Writes service_off.flag to session directory.
        3. Informs backend via /service-off-internal endpoint to trigger background report finalization.
        4. Closes meeting page to sever WebRTC and media session.
        """
        if self.is_disconnecting:
            logger.info(f"[AutoDisconnect] Already disconnecting for session {self.session_id}; skipping duplicate trigger.")
            return

        self.is_disconnecting = True
        logger.warning(f"[AutoDisconnect] TRIGGERED: {reason} for session {self.session_id}. Initiating unified teardown...")

        # 1. Gracefully click Teams Leave button FIRST (must not wait on backend or block)
        try:
            logger.info("[AutoDisconnect] Step 1: Leaving Teams meeting via Leave button...")
            left = await self.leave_meeting()
            logger.info(f"[AutoDisconnect] leave_meeting result: {left}")
        except Exception as le:
            logger.warning(f"[AutoDisconnect] Error during leave_meeting: {le}")

        # 2. Write the isolated service_off.flag to session directory
        try:
            flag_path = self.service_off_flag_path
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write("service_off")
            logger.info(f"[AutoDisconnect] Successfully created service_off flag at: {flag_path}")
        except Exception as fe:
            logger.warning(f"[AutoDisconnect] Error writing service_off flag: {fe}")

        # 3. Inform backend via /service-off-internal endpoint to trigger background report finalization
        backend_base = os.getenv("BACKEND_HTTP_URL", os.getenv("BACKEND_URL", "http://localhost:8000"))
        try:
            import httpx
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.post(
                    f"{backend_base}/api/copilot/{self.session_id}/service-off-internal",
                    json={"session_id": self.session_id, "reason": reason}
                )
                logger.info(f"[AutoDisconnect] Backend service-off-internal response: {resp.status_code}")
        except Exception as be:
            logger.warning(f"[AutoDisconnect] Could not reach backend /service-off-internal ({be}); flag file will handle finalization fallback.")

        # 4. Explicitly close meeting page to terminate WebSockets and WebRTC connections
        try:
            if self.page:
                is_closed_val = False
                if callable(getattr(self.page, "is_closed", None)):
                    is_closed_val = bool(self.page.is_closed())
                if not is_closed_val and hasattr(self.page, "close"):
                    close_res = self.page.close()
                    if asyncio.iscoroutine(close_res):
                        await close_res
                    logger.info("[AutoDisconnect] Closed meeting page successfully.")
        except Exception as pe:
            logger.debug(f"[AutoDisconnect] Page close notice: {pe}")

    async def leave_meeting(self) -> bool:
        """
        Attempts to gracefully leave the Teams meeting by locating and clicking the Leave / Hang up button.
        Returns True if the button was located and clicked, False otherwise.
        """
        logger.info("[TeamsBot] SERVICE OFF: Attempting to click Teams Leave button...")
        try:
            # Teardown native captions observer and WebSocket
            try:
                await self.page.evaluate("window.__stopMiaCaptions__ && window.__stopMiaCaptions__()")
                for frame in self.page.frames:
                    if frame == self.page.main_frame:
                        continue
                    try:
                        await frame.evaluate("window.__stopMiaCaptions__ && window.__stopMiaCaptions__()")
                    except Exception:
                        pass
                logger.info("[TeamsCaptions] Cleaned up native captions observer on meeting leave.")
            except Exception:
                pass

            # Check if meeting is already disconnected
            is_in, state_name, _ = await is_really_in_meeting(self.page)
            if state_name == "DISCONNECTED":
                logger.info("[TeamsBot] Meeting is already disconnected. No need to click Leave.")
                return True

            hangup_selector = (
                "button#hangup-button, "
                "button[id='hangup-button'], "
                "button[title='Leave'], "
                "button[title*='Leave' i], "
                "button[data-tid='hangup-button'], "
                "button[data-tid='leave-call-button'], "
                "button[data-tid*='hangup' i], "
                "button[data-tid*='leave' i], "
                "button[aria-label*='Leave' i], "
                "button[aria-label*='Hang up' i], "
                "button:has-text('Leave'), "
                "button:has-text('Hang up'), "
                "div[role='button']:has-text('Leave'), "
                "[role='button'][aria-label*='Leave' i], "
                "[role='button'][aria-label*='Hang up' i]"
            )

            main_f = self.page.main_frame
            ordered_frames = [main_f] + [f for f in self.page.frames if f != main_f]

            button_clicked = False
            for frame in ordered_frames:
                try:
                    hangup_el = frame.locator(hangup_selector).first
                    if await hangup_el.is_visible(timeout=1000):
                        try:
                            await hangup_el.click(timeout=3000, force=True)
                        except Exception:
                            await hangup_el.evaluate("el => el.click()")
                        logger.info("[TeamsBot] Clicked Teams Leave button successfully.")
                        button_clicked = True
                        break
                except Exception as frame_err:
                    logger.debug(f"[TeamsBot] Frame leave attempt skipped: {frame_err}")

            if button_clicked:
                # Check for possible confirmation popup/dialog
                confirm_leave_selector = (
                    "button[data-tid='confirm-leave-button'], "
                    "button[data-tid*='leave-confirm' i], "
                    "button:has-text('Leave meeting'), "
                    "button:has-text('Leave call'), "
                    "button:has-text('Confirm')"
                )
                for frame in ordered_frames:
                    try:
                        confirm_el = frame.locator(confirm_leave_selector).first
                        if await confirm_el.is_visible(timeout=500):
                            await confirm_el.click(timeout=1000, force=True)
                            logger.info("[TeamsBot] Clicked confirmation Leave button.")
                            break
                    except Exception:
                        pass
                return True

            logger.warning("[TeamsBot] Could not find visible Leave button in any frame.")
            return False
        except Exception as e:
            logger.error(f"[TeamsBot] Exception during leave_meeting: {e}")
            return False

    async def run_lifecycle_loop(self) -> None:
        """Main Teams Meeting Lifecycle & Polling Loop."""
        consecutive_in_meeting = 0
        consecutive_signals_lost = 0
        has_been_admitted = False
        ws_triggered = False
        diagnostics_launched = False
        captions_task_launched = False
        last_screenshot_state = None

        while True:
            await asyncio.sleep(1.5)

            # Check for dedicated Service Off trigger
            if os.path.exists(self.service_off_flag_path):
                logger.info("[TeamsBot] Service Off flag detected. Initiating graceful meeting departure...")
                try:
                    await self.leave_meeting()
                except Exception as lme:
                    logger.warning(f"[TeamsBot] Error during leave_meeting on Service Off: {lme}")
                try:
                    if self.page:
                        is_closed_val = False
                        if callable(getattr(self.page, "is_closed", None)):
                            is_closed_val = bool(self.page.is_closed())
                        if not is_closed_val and hasattr(self.page, "close"):
                            close_res = self.page.close()
                            if asyncio.iscoroutine(close_res):
                                await close_res
                except Exception:
                    pass
                await asyncio.sleep(1.0)
                break

            is_in, current_state, evidence = await is_really_in_meeting(self.page)

            if current_state == "IN_MEETING":
                consecutive_in_meeting += 1
                consecutive_signals_lost = 0
            else:
                consecutive_in_meeting = 0
                if has_been_admitted:
                    consecutive_signals_lost += 1

            # Check if IN_MEETING has remained stable for >= 2 consecutive checks
            if consecutive_in_meeting >= 2:
                has_been_admitted = True
                if self.mia_join_only:
                    logger.info("[MIA JOIN ONLY PASS] Confirmed REAL IN_MEETING state! Audio pipeline isolated.")
                elif not ws_triggered:
                    logger.info("[MIA STATE] WebSocket gate condition satisfied")
                    logger.info("[MIA WEBSOCKET GATE] Meeting admission confirmed & stable! Triggering WebSocket connection...")
                    try:
                        await self.page.evaluate("window.__connectMiaWebSocket__ && window.__connectMiaWebSocket__()")
                        for frame in self.page.frames:
                            try:
                                await frame.evaluate("window.__connectMiaWebSocket__ && window.__connectMiaWebSocket__()")
                            except Exception:
                                pass
                        ws_triggered = True
                    except Exception as wse:
                        logger.warning(f"[TeamsBot] WebSocket trigger skipped/failed: {wse}")

            # Post-admission debounce: if bot was confirmed in meeting but signals stayed absent for >= 4 checks (~6s)
            if has_been_admitted and consecutive_signals_lost >= 4:
                if current_state != "DISCONNECTED":
                    logger.warning(
                        f"[TeamsBot] Post-admission in-meeting signals lost for {consecutive_signals_lost} consecutive checks "
                        f"(~{consecutive_signals_lost * 1.5:.1f}s). Classifying as DISCONNECTED."
                    )
                    current_state = "DISCONNECTED"

            logger.info(f"[MIA STATE] {current_state} consecutive={consecutive_in_meeting} (signals_lost={consecutive_signals_lost}, admitted={has_been_admitted}) | evidence={evidence}")

            # If still stuck on PREJOIN due to a modal, dismiss it and re-trigger Join
            if current_state == "PREJOIN":
                if await self.dismiss_media_prompt_if_present():
                    logger.info("[TeamsBot] Dismissed modal during lifecycle loop, re-clicking Join Now...")
                    for target in [self.page] + self.page.frames:
                        try:
                            jb = target.locator("button#prejoin-join-button, button[data-tid='prejoin-join-button'], button:has-text('Join now'), button:has-text('Join')").first
                            if await jb.is_visible(timeout=500):
                                await jb.click(force=True)
                                break
                        except Exception:
                            pass

            # Synchronize browser window.__miaState
            try:
                await self.page.evaluate(f"window.__miaState = '{current_state}'")
            except Exception:
                pass

            # Launch non-blocking background diagnostics on entering meeting
            if current_state == "IN_MEETING" and not diagnostics_launched:
                diagnostics_launched = True
                asyncio.create_task(run_in_meeting_diagnostics(self.page))

            # Launch non-blocking background native captions task once IN_MEETING
            if current_state == "IN_MEETING" and not captions_task_launched:
                captions_task_launched = True
                asyncio.create_task(self.start_native_captions_task())

            # Check if IN_MEETING has remained stable for >= 2 consecutive checks
            if consecutive_in_meeting >= 2:
                if self.mia_join_only:
                    logger.info("[MIA JOIN ONLY PASS] Confirmed REAL IN_MEETING state! Audio pipeline isolated.")
                elif not ws_triggered:
                    logger.info("[MIA STATE] WebSocket gate condition satisfied")
                    logger.info("[MIA WEBSOCKET GATE] Meeting admission confirmed & stable! Triggering WebSocket connection...")
                    try:
                        await self.page.evaluate("window.__connectMiaWebSocket__ && window.__connectMiaWebSocket__()")
                        for frame in self.page.frames:
                            try:
                                await frame.evaluate("window.__connectMiaWebSocket__ && window.__connectMiaWebSocket__()")
                            except Exception:
                                pass
                        ws_triggered = True
                    except Exception as wse:
                        logger.warning(f"[TeamsBot] WebSocket trigger skipped/failed: {wse}")

            # Capture screenshots for key state transitions (once per state)
            if current_state != last_screenshot_state:
                last_screenshot_state = current_state
                if current_state == "LOBBY":
                    try:
                        await self.page.screenshot(path=os.path.join(self.debug_dir, "07_lobby.png"))
                    except Exception:
                        pass
                elif current_state == "ADMITTING":
                    try:
                        await self.page.screenshot(path=os.path.join(self.debug_dir, "08_admitting.png"))
                    except Exception:
                        pass
                elif current_state == "IN_MEETING":
                    try:
                        await self.page.screenshot(path=os.path.join(self.debug_dir, "09_final_state.png"))
                    except Exception:
                        pass

            # Autonomous Shutdown Detection
            if current_state == "DISCONNECTED":
                logger.info("[TeamsBot] Teams meeting ended or disconnected. Exiting browser...")
                try:
                    await self.page.evaluate("window.__stopMiaCaptions__ && window.__stopMiaCaptions__()")
                except Exception:
                    pass
                break

            # -------------------------------------------------------------
            # Auto-Disconnect When Alone Monitoring (Configurable Grace Period)
            # -------------------------------------------------------------
            if self.enable_auto_disconnect_alone and has_been_admitted and current_state == "IN_MEETING":
                p_state, p_count, p_details = await self.detect_participants()

                if p_state == "HUMAN_PRESENT":
                    if self.alone_timer_start is not None:
                        logger.info(f"[AutoDisconnect] Human participant detected ({p_details.get('humans') or 'activity'}); resetting alone timer.")
                    self.alone_timer_start = None
                    self.consecutive_empty_count = 0

                elif p_state == "UNCERTAIN":
                    # An unreadable or ambiguous roster must NOT be treated as empty.
                    # Suspend / reset grace timer until certainty is re-established.
                    if self.alone_timer_start is not None:
                        logger.info(f"[AutoDisconnect] Roster state became UNCERTAIN ({p_details}); resetting alone grace timer.")
                    self.alone_timer_start = None
                    self.consecutive_empty_count = 0

                elif p_state == "CONFIRMED_EMPTY":
                    self.consecutive_empty_count += 1
                    if self.consecutive_empty_count == 1:
                        logger.info(
                            f"[AutoDisconnect] Empty meeting observed (reason={p_details.get('reason')}). "
                            f"Debouncing check {self.consecutive_empty_count}/{self.consecutive_empty_required}..."
                        )
                    else:
                        logger.debug(
                            f"[AutoDisconnect] Empty observation count: {self.consecutive_empty_count}/{self.consecutive_empty_required} "
                            f"(details={p_details})"
                        )

                    # Only start the countdown after required consecutive empty observations (debounced)
                    if self.consecutive_empty_count >= self.consecutive_empty_required:
                        now_m = time.monotonic()
                        if self.alone_timer_start is None:
                            self.alone_timer_start = now_m
                            logger.warning(
                                f"[AutoDisconnect] [Alone Timer Started] Bot is confirmed alone in meeting. "
                                f"Initiating {self.alone_grace_period_seconds}s countdown before auto-disconnect."
                            )
                        else:
                            elapsed = now_m - self.alone_timer_start
                            remaining = max(0.0, self.alone_grace_period_seconds - elapsed)
                            logger.info(
                                f"[AutoDisconnect] Bot alone for {elapsed:.1f}s / {self.alone_grace_period_seconds}s "
                                f"({remaining:.1f}s remaining until auto-disconnect)."
                            )

                            if elapsed >= self.alone_grace_period_seconds:
                                logger.warning(
                                    f"[AutoDisconnect] Bot alone grace period of {self.alone_grace_period_seconds}s elapsed. "
                                    f"Triggering automatic disconnect..."
                                )
                                await self.trigger_auto_disconnect(reason=f"bot_alone_{int(self.alone_grace_period_seconds)}s")
                                await asyncio.sleep(1.0)
                                break

