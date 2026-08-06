"""
Teams Bot — Playwright headless browser that joins a Teams meeting and
streams audio to the Copilot Service via a Python-level WebSocket.

Architecture (PulseAudio virtual sink approach):
    Candidate speaks in Microsoft Teams
        ↓
    Chromium (Playwright) — headless browser in the meeting
        ↓
    Teams WebRTC decodes incoming audio
        ↓
    Chromium routes audio output → PulseAudio VirtualSink
        ↓
    PulseAudio Monitor Source (VirtualSink.monitor) captures playback
        ↓
    Python sounddevice reads 16kHz mono Int16 PCM from monitor
        ↓
    Python WebSocket → Copilot Service → Deepgram STT
        ↓
    Live Transcript → AI Copilot

KEY: Do NOT use --use-fake-device-for-media-stream.
That flag replaces Chromium's real audio pipeline with a null device,
which means Teams WebRTC audio never reaches PulseAudio VirtualSink.
Use --use-fake-ui-for-media-stream ONLY — it silently grants permissions
without replacing the real audio output pipeline.
"""

import asyncio
import os
import sys
import threading
import numpy as np
import sounddevice as sd
import websockets
from playwright.async_api import async_playwright
from loguru import logger

# ── Configuration ──────────────────────────────────────────────────────────────
BACKEND_WS_BASE = os.getenv("COPILOT_WS_BASE", os.getenv("BACKEND_WS_BASE", "ws://localhost:8001"))

# PulseAudio virtual sink — must match entrypoint.sh sink_name
PULSE_MONITOR_DEVICE = "VirtualSink.monitor"

# Audio capture settings — must match Deepgram STT expectations
SAMPLE_RATE = 16000   # Hz — Deepgram expects 16kHz
CHANNELS    = 1       # Mono
CHUNK_SIZE  = 4096    # samples per chunk (~256ms at 16kHz)
DTYPE       = "int16" # 16-bit signed PCM

# ── Camera block JS ────────────────────────────────────────────────────────────
CAMERA_BLOCK_JS = """
(() => {
    if (window.__camera_block_injected__) return;
    window.__camera_block_injected__ = true;
    console.log("[TeamsBot] Injecting media device video blocker and microphone silencer...");
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async function(constraints) {
                if (constraints && constraints.video) {
                    console.log("[TeamsBot] Intercepted camera request.");
                    if (!constraints.audio) {
                        console.log("[TeamsBot] Video-only request: throwing NotAllowedError.");
                        throw new DOMException("Permission denied", "NotAllowedError");
                    } else {
                        console.log("[TeamsBot] Audio + Video request: disabling video track.");
                        constraints.video = false;
                    }
                }
                const stream = await originalGetUserMedia(constraints);
                if (stream && stream.getAudioTracks) {
                    stream.getAudioTracks().forEach(track => {
                        track.enabled = false;
                        console.log("[TeamsBot] Outgoing microphone track disabled for privacy.");
                    });
                }
                return stream;
            };
        }
    } catch (e) {
        console.error("[TeamsBot] Failed to inject media device video blocker:", e);
    }
})();
"""

# ── WebRTC recvonly enforcer JS ────────────────────────────────────────────────
RECVONLY_JS = """
(() => {
    if (window.__recvonly_injected__) return;
    window.__recvonly_injected__ = true;

    if (RTCPeerConnection.prototype.addTransceiver) {
        const origAddTransceiver = RTCPeerConnection.prototype.addTransceiver;
        RTCPeerConnection.prototype.addTransceiver = function(trackOrKind, init) {
            if (trackOrKind === 'audio' || (trackOrKind && trackOrKind.kind === 'audio')) {
                init = init || {};
                init.direction = 'recvonly';
                console.log("[TeamsBot] Forced WebRTC audio transceiver to recvonly.");
            }
            return origAddTransceiver.apply(this, [trackOrKind, init]);
        };
    }

    if (RTCPeerConnection.prototype.addTrack) {
        const origAddTrack = RTCPeerConnection.prototype.addTrack;
        RTCPeerConnection.prototype.addTrack = function(track, ...streams) {
            if (track && track.kind === 'audio') {
                track.enabled = false;
                console.log("[TeamsBot] Muted outgoing audio track in addTrack.");
            }
            return origAddTrack.apply(this, [track, ...streams]);
        };
    }
})();
"""


# ── PulseAudio audio capture + WebSocket streaming ────────────────────────────

class AudioStreamer:
    """
    Reads audio from PulseAudio VirtualSink.monitor in a background thread
    and pushes Int16 PCM chunks into an asyncio queue for WebSocket delivery.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(f"[AudioStreamer] Started capture from PulseAudio device: {PULSE_MONITOR_DEVICE}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("[AudioStreamer] Stopped.")

    def _capture_loop(self):
        """Blocking audio capture loop running in a dedicated thread."""
        try:
            device_index = self._find_pulse_monitor()
            if device_index is None:
                logger.error(
                    f"[AudioStreamer] PulseAudio device '{PULSE_MONITOR_DEVICE}' not found. "
                    f"Available: {sd.query_devices()}"
                )
                return

            logger.info(f"[AudioStreamer] Using device index {device_index}: {PULSE_MONITOR_DEVICE}")

            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                device=device_index,
                blocksize=CHUNK_SIZE,
            ) as stream:
                logger.info("[AudioStreamer] PulseAudio stream opened. Streaming audio...")
                chunk_count = 0
                while not self._stop_event.is_set():
                    raw_data, overflowed = stream.read(CHUNK_SIZE)
                    if overflowed:
                        logger.debug("[AudioStreamer] Buffer overflow.")
                    try:
                        self.loop.call_soon_threadsafe(
                            self.queue.put_nowait, bytes(raw_data)
                        )
                        chunk_count += 1
                        if chunk_count % 500 == 0:
                            # Check audio level to detect silence vs real audio
                            samples = np.frombuffer(raw_data, dtype=np.int16)
                            rms = np.sqrt(np.mean(samples.astype(float) ** 2))
                            logger.info(f"[AudioStreamer] {chunk_count} chunks sent. RMS level: {rms:.1f}")
                    except asyncio.QueueFull:
                        try:
                            self.queue.get_nowait()
                            self.loop.call_soon_threadsafe(
                                self.queue.put_nowait, bytes(raw_data)
                            )
                        except Exception:
                            pass

        except Exception as e:
            logger.error(f"[AudioStreamer] Capture loop error: {e}")

    def _find_pulse_monitor(self) -> int | None:
        """Find the sounddevice index for VirtualSink.monitor."""
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if PULSE_MONITOR_DEVICE in dev.get("name", "") and dev.get("max_input_channels", 0) > 0:
                return i
        # Fallback: try any pulse device
        for i, dev in enumerate(devices):
            if "pulse" in dev.get("name", "").lower() and dev.get("max_input_channels", 0) > 0:
                logger.warning(f"[AudioStreamer] Exact match not found, using fallback: {dev['name']}")
                return i
        return None


async def stream_audio_to_server(
    ws_url: str,
    streamer: AudioStreamer,
    stop_event: asyncio.Event,
):
    """Consumes audio chunks from AudioStreamer and sends them via WebSocket."""
    retry_delay = 1.0
    while not stop_event.is_set():
        try:
            logger.info(f"[AudioStreamer] Connecting WebSocket to: {ws_url}")
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=5,
            ) as ws:
                logger.info("[AudioStreamer] WebSocket connected. Streaming audio chunks...")
                retry_delay = 1.0
                while not stop_event.is_set():
                    try:
                        chunk = await asyncio.wait_for(streamer.queue.get(), timeout=2.0)
                        await ws.send(chunk)
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed as cc:
                        logger.warning(f"[AudioStreamer] WebSocket closed: {cc}. Reconnecting...")
                        break
        except Exception as e:
            if stop_event.is_set():
                break
            logger.error(f"[AudioStreamer] WebSocket error: {e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 15.0)

    logger.info("[AudioStreamer] WebSocket streaming stopped.")


# ── Playwright periodic recvonly injector ─────────────────────────────────────

async def periodic_injector(page):
    """Re-injects recvonly JS periodically to handle Teams SPA navigations."""
    while True:
        try:
            await page.evaluate(RECVONLY_JS)
            for frame in page.frames:
                try:
                    await frame.evaluate(RECVONLY_JS)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(3.0)


# ── Main bot entry point ────────────────────────────────────────────────────────

async def run_bot(meeting_url: str, session_id: str):
    ws_url = f"{BACKEND_WS_BASE}/api/ws/copilot/{session_id}?mode=audio_stream"
    logger.info(f"[TeamsBot] Connecting Playwright bot to meeting: {meeting_url}")
    logger.info(f"[TeamsBot] Will stream audio via Python WebSocket to: {ws_url}")

    loop = asyncio.get_running_loop()
    streamer = AudioStreamer(loop=loop)
    stop_event = asyncio.Event()

    async with async_playwright() as p:
        pulse_socket = os.getenv("PULSE_SERVER", "unix:/tmp/pulse/native")
        display = os.getenv("DISPLAY", ":99")

        browser = await p.chromium.launch(
            # Use non-headless with Xvfb virtual display
            # This forces Chromium to initialize its full audio pipeline through PulseAudio
            # headless=True causes Chromium to skip PulseAudio audio output routing entirely
            headless=False,
            args=[
                # --use-fake-ui-for-media-stream: silently grants mic/camera permission
                # prompts without showing a browser dialog.
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--disable-features=BlockInsecurePrivateNetworkRequests,ProtocolHandlerRoundTrip",
                "--no-default-browser-check",
                "--disable-component-update",
                "--disable-extensions",
                "--disable-dev-shm-usage",
                "--enable-unsafe-swiftshader",
                # Disable GPU rendering (Xvfb uses software rendering)
                "--disable-gpu",
                # Route Chromium audio through PulseAudio
                "--alsa-output-device=pulse",
                "--alsa-input-device=pulse",
                "--alsa-output-device=pulse",
                "--alsa-input-device=pulse",
                # Ensure audio renderer process is started (headless may skip it otherwise)
                "--audio-output-channels=2",
                "--disable-audio-output=false",
            ],
            env={
                **os.environ,
                # Point Chromium PulseAudio client at our VirtualSink socket
                "PULSE_SERVER": pulse_socket,
                "XDG_RUNTIME_DIR": "/tmp/pulse",
                "DISPLAY": display,
            }
        )

        context = await browser.new_context(
            permissions=["microphone", "camera"],
            ignore_https_errors=True,
        )

        async def block_protocol_handler(route):
            if route.request.url.startswith("msteams:") or \
               route.request.url.startswith("ms-teams:"):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**", block_protocol_handler)

        page = await context.new_page()
        await page.add_init_script(CAMERA_BLOCK_JS)
        await page.add_init_script(RECVONLY_JS)
        page.on("console", lambda msg: logger.info(f"[BrowserConsole] {msg.type}: {msg.text}"))
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))

        # Build Teams web lobby URL
        if "?" in meeting_url:
            web_url = meeting_url + "&skipAppLaunch=1&anon=true&launchAgent=join_launcher_web&lightExperience=true"
        else:
            web_url = meeting_url + "?skipAppLaunch=1&anon=true&launchAgent=join_launcher_web&lightExperience=true"

        logger.info(f"[TeamsBot] Navigating to web lobby URL: {web_url}")
        await page.goto(web_url, wait_until="domcontentloaded", timeout=60000)

        injector_task = asyncio.create_task(periodic_injector(page))

        # ── Dismiss "no audio/video" dialog immediately after navigation ──────
        # Poll for 10s after navigation — dialog appears during redirect phase.
        # Dismissing it does NOT affect WebRTC audio RECEPTION — Teams still
        # plays incoming audio through Chromium → PulseAudio VirtualSink.
        logger.info("[TeamsBot] Checking for 'no audio/video' dialog (10s)...")
        for _ in range(10):
            try:
                no_av_btn = page.locator("button:has-text('Continue without audio or video')")
                if await no_av_btn.is_visible(timeout=1000):
                    await no_av_btn.click(timeout=2000)
                    logger.info("[TeamsBot] Dismissed 'no audio/video' dialog.")
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)

        debug_dir = os.path.join(os.getcwd(), "interviews", session_id)
        os.makedirs(debug_dir, exist_ok=True)
        try:
            await page.screenshot(path=os.path.join(debug_dir, "teams_bot_landing.png"))
            logger.info("[TeamsBot] Saved landing page screenshot to session directory.")
        except Exception as se:
            logger.warning(f"[TeamsBot] Failed to save landing screenshot: {se}")

        logger.info("[TeamsBot] Waiting for page to settle after redirects (5s)...")
        await asyncio.sleep(5.0)
        current_url = page.url
        logger.info(f"[TeamsBot] Current URL after settle: {current_url}")

        try:
            await page.screenshot(path=os.path.join(debug_dir, "teams_bot_after_redirect.png"))
            logger.info("[TeamsBot] Saved post-redirect screenshot.")
        except Exception as se:
            logger.warning(f"[TeamsBot] Could not save post-redirect screenshot: {se}")

        # ── Check for Web Join button ───────────────────────────────────────
        try:
            logger.info("[TeamsBot] Looking for Web Join option...")
            web_join_button = page.locator(
                "button:has-text('Join on the web'), "
                "button:has-text('Continue on this browser'), "
                "button:has-text('Continue in this browser'), "
                "button:has-text('Join in a browser'), "
                "[aria-label*='Join on the web'], "
                "[data-tid='join-on-web'], "
                "a:has-text('Join on the web'), "
                "a:has-text('Continue in this browser')"
            )
            if await web_join_button.first.is_visible(timeout=5000):
                await web_join_button.first.click(timeout=10000)
                logger.info("[TeamsBot] Clicked Web Join button.")
                await asyncio.sleep(5.0)
            else:
                logger.info("[TeamsBot] No Web Join button found — already on lobby page.")
        except Exception as e:
            logger.warning(f"[TeamsBot] Web Join step skipped: {e}")

        try:
            await page.evaluate(RECVONLY_JS)
            logger.info("[TeamsBot] WebRTC recvonly interceptor evaluated on page context.")
        except Exception as ee:
            logger.warning(f"[TeamsBot] Recvonly script evaluation skipped: {ee}")

        # ── Handle "no audio/video" dialog if it appears ───────────────────
        # This dialog does NOT prevent Teams from receiving/playing incoming audio.
        # Teams WebRTC audio playback still routes through Chromium → PulseAudio.
        # Clicking "Continue without audio or video" only means the bot won't
        # send outgoing audio/video — which is exactly what we want.
        try:
            no_av_btn = page.locator("button:has-text('Continue without audio or video')")
            if await no_av_btn.is_visible(timeout=4000):
                await no_av_btn.click(timeout=3000)
                logger.info("[TeamsBot] Handled 'no audio/video' dialog — bot joins as listener only (correct behavior).")
                await asyncio.sleep(1.0)
            else:
                logger.info("[TeamsBot] No audio/video dialog — Teams found audio devices, joining normally.")
        except Exception:
            pass

        # ── Lobby: fill name, mute mic/cam, join ───────────────────────────
        try:
            logger.info("[TeamsBot] Waiting for lobby name input (up to 45s)...")
            name_input = page.locator(
                "input[data-tid='prejoin-display-name-input'], "
                "input[placeholder='Type your name'], "
                "input.fui-Input__input, "
                "input[placeholder*='Type your name' i], "
                "input[placeholder*='Enter name' i], "
                "input[placeholder*='your name' i], "
                "input[aria-label*='Type your name' i], "
                "input[aria-label*='Enter name' i], "
                "input[aria-label*='name' i], "
                "input[type='text']"
            )
            target_name_input = name_input.first
            await target_name_input.wait_for(state="visible", timeout=45000)

            # Toggle camera OFF
            try:
                camera_toggle = page.locator(
                    "[aria-label*='camera' i], [aria-label*='video' i], [data-tid*='video']"
                ).first
                if await camera_toggle.is_visible(timeout=3000):
                    label = (await camera_toggle.get_attribute("aria-label") or "").lower()
                    camera_is_on = "turn camera off" in label or (
                        "camera" in label and "turn camera on" not in label
                    )
                    if camera_is_on:
                        await camera_toggle.click()
                        logger.info("[TeamsBot] Video camera toggled OFF.")
                    else:
                        logger.info(f"[TeamsBot] Camera already OFF (label: '{label}').")
            except Exception as ce:
                logger.warning(f"[TeamsBot] Could not verify/toggle camera: {ce}")

            # Toggle microphone OFF
            try:
                mic_switch = page.locator(
                    "input[data-cid*='toggle-mute'], "
                    "input[data-tid='toggle-mute'], "
                    "input[title*='Mute mic' i], "
                    "[role='switch'][data-tid*='toggle-mute']"
                ).first
                if await mic_switch.is_visible(timeout=5000):
                    data_cid = (await mic_switch.get_attribute("data-cid") or "").lower()
                    title = (await mic_switch.get_attribute("title") or "").lower()
                    is_checked = await mic_switch.is_checked()
                    mic_is_on = (
                        "toggle-mute-true" in data_cid
                        or ("mute mic" in title and "unmute" not in title)
                        or is_checked
                    )
                    if mic_is_on:
                        await mic_switch.click(force=True)
                        logger.info("[TeamsBot] Clicked Fluent UI mic switch OFF (Muted).")
                    else:
                        logger.info("[TeamsBot] Mic switch already OFF.")
                else:
                    fallback_mic = page.locator("[data-tid*='toggle-mute'], [data-tid*='mute']").first
                    if await fallback_mic.is_visible(timeout=2000):
                        await fallback_mic.click(force=True)
                        logger.info("[TeamsBot] Microphone fallback button clicked.")
            except Exception as me:
                logger.warning(f"[TeamsBot] Could not verify/toggle microphone: {me}")

            # Fill display name
            try:
                await target_name_input.click(timeout=3000, force=True)
                await target_name_input.fill("AI Copilot Teammate", timeout=5000)
            except Exception as fe:
                logger.warning(f"[TeamsBot] Fill failed ({fe}); using JS value assignment...")
                await target_name_input.evaluate(
                    "(el, val) => { el.value = val; "
                    "el.dispatchEvent(new Event('input', {bubbles: true})); "
                    "el.dispatchEvent(new Event('change', {bubbles: true})); }",
                    "AI Copilot Teammate",
                )
            try:
                await target_name_input.dispatch_event("input")
                await target_name_input.dispatch_event("change")
            except Exception:
                pass
            logger.info("[TeamsBot] Filled guest display name input field.")

            # Click Join button
            join_button = page.locator(
                "button#prejoin-join-button, "
                "button[data-tid='prejoin-join-button'], "
                "button[aria-label='Join now'], "
                "button[aria-label*='Join now' i], "
                "button:has-text('Join now'), "
                "button:has-text('Join meeting'), "
                "button:has-text('Join')"
            )
            target_join_button = join_button.first
            try:
                await target_join_button.wait_for(state="visible", timeout=5000)
                await target_join_button.click(timeout=5000, force=True)
                logger.info("[TeamsBot] Join request submitted via Join button click.")
            except Exception as jbe:
                logger.warning(f"[TeamsBot] Direct click failed ({jbe}); trying JS click...")
                try:
                    await target_join_button.evaluate("el => el.click()")
                    logger.info("[TeamsBot] Join request submitted via JS click.")
                except Exception:
                    await target_name_input.press("Enter")
                    logger.info("[TeamsBot] Join request submitted via Enter key.")

            logger.info("[TeamsBot] Waiting in lobby...")
            await asyncio.sleep(5.0)
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_lobby.png"))
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[TeamsBot] Failed to automate lobby flow: {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_join_failed.png"))
            except Exception:
                pass

        # ── Wait for lobby admission ───────────────────────────────────────
        logger.info("[TeamsBot] Waiting for lobby admission (up to 120s)...")
        admitted = False
        for _ in range(40):
            try:
                in_meeting_indicators = page.locator(
                    "button[data-tid='microphone-button'], "
                    "button[data-tid='camera-button'], "
                    "button[aria-label='Leave' i], "
                    "button[aria-label*='Leave meeting' i], "
                    "[data-tid='call-controls']"
                )
                if await in_meeting_indicators.first.is_visible(timeout=2000):
                    logger.info("[TeamsBot] Lobby admission confirmed — in-meeting controls visible.")
                    admitted = True
                    break
            except Exception:
                pass
            await asyncio.sleep(3.0)

        if not admitted:
            logger.warning("[TeamsBot] Was not admitted within 120s. Starting audio capture anyway.")

        # Give WebRTC audio time to stabilize after admission
        logger.info("[TeamsBot] Waiting for Teams WebRTC audio to stabilize (3s)...")
        await asyncio.sleep(3.0)

        # Verify PulseAudio is receiving Chromium audio before starting capture
        try:
            import subprocess
            sink_result = subprocess.run(
                ["pactl", "-s", "unix:/tmp/pulse/native", "list", "sink-inputs"],
                capture_output=True, text=True, timeout=5
            )
            if "chromium" in sink_result.stdout.lower() or "headless" in sink_result.stdout.lower():
                logger.info("[TeamsBot] Confirmed: Chromium audio is flowing into PulseAudio VirtualSink.")
            elif sink_result.stdout.strip():
                logger.info(f"[TeamsBot] PulseAudio has active sink inputs: {len(sink_result.stdout.splitlines())} lines")
            else:
                logger.warning("[TeamsBot] No active PulseAudio sink inputs — Chromium may not be routing audio yet.")
        except Exception as ve:
            logger.warning(f"[TeamsBot] PulseAudio verification failed: {ve}")

        logger.info("[TeamsBot] Starting PulseAudio monitor capture...")
        streamer.start()

        streaming_task = asyncio.create_task(
            stream_audio_to_server(ws_url, streamer, stop_event)
        )
        logger.info("[TeamsBot] Audio streaming task started. Bot is now live.")

        # ── Keep alive: enforce mute/camera off ────────────────────────────
        try:
            in_meeting_muted = False
            in_meeting_camera_off = False

            while True:
                await asyncio.sleep(3)

                if page.is_closed():
                    logger.warning("[TeamsBot] Teams browser page closed. Exiting...")
                    break

                all_frames = [page] + list(page.frames)

                if not in_meeting_camera_off:
                    for frame in all_frames:
                        try:
                            cam_btn = frame.locator(
                                "button[data-tid='camera-button'], "
                                "button[aria-label*='camera' i]"
                            ).first
                            if await cam_btn.is_visible(timeout=500):
                                label = (await cam_btn.get_attribute("aria-label") or "").lower()
                                pressed = (await cam_btn.get_attribute("aria-pressed") or "").lower()
                                if pressed == "true" or "turn camera off" in label:
                                    await cam_btn.click()
                                    in_meeting_camera_off = True
                                    logger.info("[TeamsBot] In-meeting camera clicked OFF.")
                                    break
                                elif "turn camera on" in label or pressed == "false":
                                    in_meeting_camera_off = True
                                    logger.info(f"[TeamsBot] In-meeting camera already OFF (label: '{label}').")
                                    break
                        except Exception:
                            pass

                if not in_meeting_muted:
                    for frame in all_frames:
                        try:
                            mic_btn = frame.locator(
                                "button[data-tid='microphone-button'], "
                                "button[aria-label*='Mute microphone' i], "
                                "button[aria-label='Mute' i]"
                            ).first
                            if await mic_btn.is_visible(timeout=500):
                                label = (await mic_btn.get_attribute("aria-label") or "").lower()
                                pressed = (await mic_btn.get_attribute("aria-pressed") or "").lower()
                                if pressed == "true" or ("mute" in label and "unmute" not in label):
                                    await mic_btn.click()
                                    in_meeting_muted = True
                                    logger.info("[TeamsBot] In-meeting microphone clicked OFF (Muted).")
                                    break
                                elif "unmute" in label or pressed == "false":
                                    in_meeting_muted = True
                                    logger.info("[TeamsBot] In-meeting microphone already muted.")
                                    break
                        except Exception:
                            pass

        except asyncio.CancelledError:
            logger.info("[TeamsBot] Stopping Teams observer bot.")
        finally:
            stop_event.set()
            streamer.stop()
            streaming_task.cancel()
            injector_task.cancel()
            try:
                await streaming_task
            except asyncio.CancelledError:
                pass
            await context.close()
            await browser.close()
            logger.info("[TeamsBot] Browser closed. Bot exited cleanly.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python teams_bot.py <meeting_url> <session_id>")
        sys.exit(1)

    m_url = sys.argv[1]
    s_id = sys.argv[2]

    asyncio.run(run_bot(m_url, s_id))
