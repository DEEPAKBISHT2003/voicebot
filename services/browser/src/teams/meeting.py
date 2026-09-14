import asyncio
import os
import time
import urllib.parse
from typing import Tuple, Dict, Any, Optional
from playwright.async_api import Page
from loguru import logger


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

        # Ensure video camera is turned off in top toolbar
        for frame in all_frames:
            try:
                in_meeting_camera = frame.locator("button[data-tid='camera-button'], button[aria-label*='camera' i]").first
                if await in_meeting_camera.is_visible(timeout=300):
                    label = (await in_meeting_camera.get_attribute("aria-label") or "").lower()
                    pressed = (await in_meeting_camera.get_attribute("aria-pressed") or "").lower()
                    if pressed == "true" or ("turn camera off" in label):
                        await in_meeting_camera.click()
                        logger.info("[TeamsBot] In-meeting camera clicked OFF.")
                        break
                    elif "turn camera on" in label or pressed == "false":
                        break
            except Exception:
                pass

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
        bot_display_name: str = "Mia - AI Interviewer",
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
        logger.info("[TeamsBot] Opening meeting URL...")
        await self.page.goto(self.meeting_url)
        logger.info("[TeamsBot] Teams page loaded")

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

            target_name_input = None
            start_wait = asyncio.get_event_loop().time()
            max_wait_sec = self.prejoin_timeout_ms / 1000.0
            last_reclick_time = 0

            while (asyncio.get_event_loop().time() - start_wait) < max_wait_sec:
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

    async def run_lifecycle_loop(self) -> None:
        """Main Teams Meeting Lifecycle & Polling Loop."""
        consecutive_in_meeting = 0
        ws_triggered = False
        diagnostics_launched = False
        last_screenshot_state = None

        while True:
            await asyncio.sleep(1.5)
            is_in, current_state, evidence = await is_really_in_meeting(self.page)

            if current_state == "IN_MEETING":
                consecutive_in_meeting += 1
            else:
                consecutive_in_meeting = 0

            logger.info(f"[MIA STATE] {current_state} consecutive={consecutive_in_meeting} | evidence={evidence}")

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
                break
