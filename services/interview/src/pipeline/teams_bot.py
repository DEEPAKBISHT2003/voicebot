import asyncio
import os
import sys
from playwright.async_api import async_playwright
from loguru import logger

# Configuration defaults
BACKEND_WS_BASE = os.getenv("BACKEND_WS_BASE", "ws://localhost:8000")

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
                // Protocol-level privacy guard: disable outgoing audio tracks so bot mic sends 100% silence
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

INTERCEPT_JS = """
(async () => {
    // Re-evaluation guard to prevent multiple injections in the same frame context
    if (window.__teams_audio_intercept_injected__) return;
    window.__teams_audio_intercept_injected__ = true;

    console.log("[TeamsBot] Injecting WebRTC audio interceptor with shared mixer...");
    const wsUrl = "%WS_URL%";
    let socket = null;
    
    // Initialize AudioContext at 16kHz for Deepgram-compatible output
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const capturedTrackIds = new Set();
    const capturedStreams = new Set();
    
    // ── Shared Mixer: single ScriptProcessor that all audio sources connect to ──
    let sharedProcessor = null;
    
    function initSharedProcessor() {
        if (sharedProcessor) return;
        
        // Open a single WebSocket for all mixed audio output
        console.log("[TeamsBot] Opening audio streaming WebSocket to:", wsUrl);
        socket = new WebSocket(wsUrl);
        socket.onopen = () => console.log("[TeamsBot] Interceptor WebSocket connected.");
        socket.onclose = () => {
            console.log("[TeamsBot] Interceptor WebSocket closed.");
            socket = null;
        };
        socket.onerror = (e) => console.error("[TeamsBot] Interceptor WebSocket error:", e);
        
        // Create a single shared processor node for mixing
        sharedProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
        
        sharedProcessor.onaudioprocess = (e) => {
            const inputData = e.inputBuffer.getChannelData(0);
            
            // Convert Float32Array to Int16 PCM Mono bytes
            const outputData = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputData[i]));
                outputData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(outputData.buffer);
            }
        };
        
        // Route through a silent GainNode to prevent host speaker echo
        // The processor needs to be connected to destination to keep firing,
        // but we set gain to 0.0 so no actual sound plays on host machine.
        const silentGain = audioCtx.createGain();
        silentGain.gain.value = 0.0;
        sharedProcessor.connect(silentGain);
        silentGain.connect(audioCtx.destination);
        
        console.log("[TeamsBot] Shared audio mixer initialized (silent output).");
    }
    
    function captureAudioStream(stream) {
        if (!stream || stream.getAudioTracks().length === 0) return;
        if (capturedStreams.has(stream.id)) return;
        capturedStreams.add(stream.id);
        
        console.log("[TeamsBot] Capturing WebRTC audio track from stream:", stream.id);
        
        // Ensure the shared mixer is ready
        initSharedProcessor();
        
        try {
            // Connect this audio source to the shared mixer processor
            // Web Audio API automatically sums (mixes) all connected inputs
            const source = audioCtx.createMediaStreamSource(stream);
            source.connect(sharedProcessor);
            console.log("[TeamsBot] Audio source connected to shared mixer:", stream.id);
        } catch (err) {
            console.error("[TeamsBot] Failed to bind AudioContext source:", err);
        }
    }

    // Intercept WebRTC Peer Connections and force recvonly for outgoing audio
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

    // Intercept incoming WebRTC Peer Connections for transcript capture
    const origSetRemoteDescription = RTCPeerConnection.prototype.setRemoteDescription;
    RTCPeerConnection.prototype.setRemoteDescription = function(desc) {
        this.addEventListener('track', (e) => {
            if (e.track && e.track.kind === 'audio') {
                // Deduplicate based on persistent WebRTC track-level ID
                if (capturedTrackIds.has(e.track.id)) return;
                capturedTrackIds.add(e.track.id);
                
                const stream = e.streams[0] || new MediaStream([e.track]);
                captureAudioStream(stream);
            }
        });
        return origSetRemoteDescription.apply(this, [desc]);
    };
    
    // Periodically search for existing DOM audio elements as a fallback
    setInterval(() => {
        document.querySelectorAll('audio, video').forEach(el => {
            if (el.srcObject) {
                el.srcObject.getAudioTracks().forEach(track => {
                    if (!capturedTrackIds.has(track.id)) {
                        capturedTrackIds.add(track.id);
                        captureAudioStream(el.srcObject);
                    }
                });
            }
        });
    }, 2000);
})();
"""

async def periodic_injector(page, ws_url):
    formatted_js = INTERCEPT_JS.replace("%WS_URL%", ws_url)
    logger.info("[TeamsBot] Started background periodic JS interceptor injector.")
    while True:
        try:
            # Inject into the main page
            await page.evaluate(formatted_js)
            
            # Inject into all loaded frames
            for frame in page.frames:
                try:
                    await frame.evaluate(formatted_js)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(3.0)

async def run_bot(meeting_url: str, session_id: str):
    ws_url = f"{BACKEND_WS_BASE}/api/ws/interview/{session_id}?mode=observer"
    logger.info(f"[TeamsBot] Connecting Playwright bot to meeting: {meeting_url}")
    logger.info(f"[TeamsBot] Streaming back to: {ws_url}")
    
    async with async_playwright() as p:
        # Launch Chromium with media stream bypass arguments
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--disable-features=BlockInsecurePrivateNetworkRequests"
            ]
        )
        
        # Open context granting microphone permission for WebRTC stack initialization
        context = await browser.new_context(
            permissions=["microphone"]
        )
        
        page = await context.new_page()
        await page.add_init_script(CAMERA_BLOCK_JS)
        page.on("console", lambda msg: logger.info(f"[BrowserConsole] {msg.type}: {msg.text}"))
        
        # Navigate to Teams Meeting Link
        await page.goto(meeting_url)
        
        # Start background periodic JS interceptor injector
        injector_task = asyncio.create_task(periodic_injector(page, ws_url))
        
        await asyncio.sleep(5.0) # Allow landing page to load fully
        
        # Save landing page screenshot for diagnosis
        debug_dir = os.path.join(os.getcwd(), "backend", "storage", "copilots", session_id)
        os.makedirs(debug_dir, exist_ok=True)
        try:
            await page.screenshot(path=os.path.join(debug_dir, "teams_bot_landing.png"))
            logger.info(f"[TeamsBot] Saved landing page screenshot to session directory.")
        except Exception as se:
            logger.warning(f"[TeamsBot] Failed to save landing screenshot: {se}")
        
        # Automate Teams UI Guest Selection Flow
        try:
            logger.info("[TeamsBot] Selecting Web Join option...")
            # Click "Join on the web instead" or "Continue on this browser" button
            web_join_button = page.locator("button:has-text('Join on the web'), button:has-text('Continue on this browser'), button:has-text('Continue in this browser'), [aria-label*='Join on the web'], [data-tid='join-on-web']")
            await web_join_button.first.click(timeout=10000)
            await asyncio.sleep(5.0) # Wait for prep room to load
        except Exception as e:
            logger.warning(f"[TeamsBot] Bypassing Web Join select step (already on lobby page or redirected): {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_join_redirect.png"))
            except Exception:
                pass

        # Injects WebRTC interception JS code into page initialization
        formatted_js = INTERCEPT_JS.replace("%WS_URL%", ws_url)
        await page.add_init_script(formatted_js)
        try:
            await page.evaluate(formatted_js)
            logger.info("[TeamsBot] WebRTC interceptor evaluated immediately on page context.")
        except Exception as ee:
            logger.warning(f"[TeamsBot] Direct evaluation of interceptor script skipped/failed: {ee}")
        
        # Enter guest name in name field
        try:
            logger.info("[TeamsBot] Waiting for credentials page to load (can take up to 30-45s)...")
            name_input = page.locator("input[placeholder*='Enter name'], input[id*='username'], input[placeholder*='name'], input[placeholder*='Name'], input[aria-label*='name'], input[aria-label*='Name'], input[type='text']")
            
            # Wait for name field to be loaded/visible
            await name_input.first.wait_for(state="visible", timeout=45000)
            
            # Ensure Video Camera is toggled OFF for privacy
            try:
                camera_toggle = page.locator("[aria-label*='camera' i], [aria-label*='video' i], [data-tid*='video']").first
                if await camera_toggle.is_visible(timeout=3000):
                    label = (await camera_toggle.get_attribute("aria-label") or "").lower()
                    camera_is_on = "turn camera off" in label or ("camera" in label and "turn camera on" not in label)
                    if camera_is_on:
                        await camera_toggle.click()
                        logger.info("[TeamsBot] Video camera toggled OFF.")
                    else:
                        logger.info(f"[TeamsBot] Camera already OFF (label: '{label}').")
            except Exception as ce:
                logger.warning(f"[TeamsBot] Could not verify/toggle video camera button: {ce}")

            # Ensure Microphone is toggled OFF (Muted) in UI for privacy using exact Fluent UI signatures
            try:
                mic_switch = page.locator(
                    "input[data-cid*='toggle-mute'], "
                    "input[data-tid='toggle-mute'], "
                    "input[title*='Mute mic' i], "
                    "input[title*='Unmute mic' i], "
                    "[role='switch'][data-tid*='toggle-mute']"
                ).first
                if await mic_switch.is_visible(timeout=5000):
                    data_cid = (await mic_switch.get_attribute("data-cid") or "").lower()
                    title = (await mic_switch.get_attribute("title") or "").lower()
                    is_checked = await mic_switch.is_checked()
                    
                    # Mic is ON if data-cid is toggle-mute-true, title contains "mute mic" (not unmute), or is_checked is True
                    mic_is_on = "toggle-mute-true" in data_cid or ("mute mic" in title and "unmute" not in title) or is_checked
                    if mic_is_on:
                        await mic_switch.click(force=True)
                        logger.info("[TeamsBot] Clicked Fluent UI mic switch OFF (Muted).")
                    else:
                        logger.info(f"[TeamsBot] Fluent UI mic switch already OFF (data-cid='{data_cid}', title='{title}').")
                else:
                    # Fallback locator if explicit switch element is not found
                    fallback_mic = page.locator("[data-tid*='toggle-mute'], [data-tid*='mute']").first
                    if await fallback_mic.is_visible(timeout=2000):
                        await fallback_mic.click(force=True)
                        logger.info("[TeamsBot] Microphone fallback button clicked.")
            except Exception as me:
                logger.warning(f"[TeamsBot] Could not verify/toggle microphone button: {me}")

            await name_input.first.fill("AI Copilot Teammate", timeout=10000)
            
            # Click "Join Now" or "Join" button
            join_button = page.locator("button:has-text('Join now'), button:has-text('Join'), button[data-tid='prejoin-join-button'], button:has-text('Join meeting')")
            await join_button.first.wait_for(state="visible", timeout=10000)
            await join_button.first.click(timeout=10000)
            logger.info("[TeamsBot] Join request submitted. Waiting in lobby...")
            await asyncio.sleep(5.0)
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_lobby.png"))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[TeamsBot] Failed to automate input names/joining: {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_join_failed.png"))
                logger.info("[TeamsBot] Saved join failure screenshot to session directory.")
            except Exception:
                pass
            
        # Keep connection open until script is cancelled
        try:
            in_meeting_muted = False
            while True:
                await asyncio.sleep(3)
                # Keep active check of the browser window health
                if page.is_closed():
                    logger.warning("[TeamsBot] Teams browser page closed. Exiting...")
                    break

                # In-meeting top toolbar microphone mute check across all frames
                if not in_meeting_muted:
                    all_frames = [page] + list(page.frames)
                    for frame in all_frames:
                        try:
                            in_meeting_mic = frame.locator("button[data-tid='microphone-button'], button[aria-label*='Mute microphone' i], button[aria-label='Mute' i]").first
                            if await in_meeting_mic.is_visible(timeout=500):
                                label = (await in_meeting_mic.get_attribute("aria-label") or "").lower()
                                pressed = (await in_meeting_mic.get_attribute("aria-pressed") or "").lower()
                                if pressed == "true" or ("mute" in label and "unmute" not in label):
                                    await in_meeting_mic.click()
                                    in_meeting_muted = True
                                    logger.info("[TeamsBot] In-meeting top toolbar microphone clicked OFF (Muted).")
                                    break
                                elif "unmute" in label or pressed == "false":
                                    in_meeting_muted = True
                                    logger.info(f"[TeamsBot] In-meeting microphone already muted (label: '{label}').")
                                    break
                        except Exception:
                            pass
        except asyncio.CancelledError:
            logger.info("[TeamsBot] Stopping Teams observer bot.")
        finally:
            injector_task.cancel()
            await context.close()
            await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python teams_bot.py <meeting_url> <session_id>")
        sys.exit(1)
        
    m_url = sys.argv[1]
    s_id = sys.argv[2]
    
    asyncio.run(run_bot(m_url, s_id))
