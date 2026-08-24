import asyncio
import os
import sys
from playwright.async_api import async_playwright
from loguru import logger

# Configuration defaults — points to Copilot Service WebSocket & Browser settings
BACKEND_WS_BASE = os.getenv("COPILOT_WS_BASE", os.getenv("BACKEND_WS_BASE", "ws://localhost:9001"))
LOCAL_AUDIO_WS_BASE = os.getenv("LOCAL_AUDIO_WS_BASE", "ws://localhost:9001")
# Shared namespace experiment: when True, skip AudioProxy and connect directly to FastAPI via localhost
USE_SHARED_NAMESPACE = os.getenv("USE_SHARED_NAMESPACE", "true").lower() == "true"
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "AI Copilot Teammate")
BOT_HEADLESS = os.getenv("BOT_HEADLESS", "true").lower() == "true"
BOT_PREJOIN_TIMEOUT_MS = int(os.getenv("BOT_PREJOIN_TIMEOUT_MS", "45000"))

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
    let frameCounter = 0;
    const pendingFrames = [];
    
    // Initialize AudioContext at 16kHz for Deepgram-compatible output
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const capturedTrackIds = new Set();
    const capturedStreams = new Set();
    
    // ── Shared Mixer: single ScriptProcessor that all audio sources connect to ──
    let sharedProcessor = null;
    
    function connectAudioWS() {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            return;
        }
        
        console.log("[AudioWS] creating connection to:", wsUrl);
        console.log("[AudioWS] connecting");
        try {
            socket = new WebSocket(wsUrl);
        } catch (wsErr) {
            console.error("[AudioWS] error creating WebSocket:", wsErr);
            return;
        }

        socket.onopen = () => {
            console.log("[AudioWS] open. Flushing pending frame buffer count:", pendingFrames.length);
            while (pendingFrames.length > 0 && socket.readyState === WebSocket.OPEN) {
                const item = pendingFrames.shift();
                frameCounter++;
                socket.send(item.buffer);
                console.log(`[AudioWS] sending audio frame #${frameCounter}, bytes=${item.byteLength}, sampleRate=16000, channels=1, timestamp=${item.timestamp}, readyState=${socket.readyState}`);
            }
        };

        socket.onclose = (e) => {
            console.log(`[AudioWS] closed: code=${e.code}, reason=${e.reason || 'none'}`);
            socket = null;
        };

        socket.onerror = (e) => {
            console.error("[AudioWS] error:", e);
        };
    }

    function initSharedProcessor() {
        if (sharedProcessor) return;
        
        if (audioCtx.state === 'suspended') {
            audioCtx.resume().then(() => {
                console.log("[TeamsBot] AudioContext resumed successfully.");
            }).catch(err => {
                console.error("[TeamsBot] Failed to resume AudioContext:", err);
            });
        }
        
        connectAudioWS();
        
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
            
            const payload = {
                buffer: outputData.buffer,
                byteLength: outputData.buffer.byteLength,
                timestamp: Date.now()
            };

            if (socket && socket.readyState === WebSocket.OPEN) {
                frameCounter++;
                socket.send(payload.buffer);
                if (frameCounter <= 5 || frameCounter % 100 === 0) {
                    console.log(`[AudioWS] sending audio frame #${frameCounter}, bytes=${payload.byteLength}, sampleRate=16000, channels=1, timestamp=${payload.timestamp}, readyState=${socket.readyState}`);
                }
            } else if (socket && socket.readyState === WebSocket.CONNECTING) {
                if (pendingFrames.length < 50) {
                    pendingFrames.push(payload);
                }
            } else if (!socket || socket.readyState === WebSocket.CLOSED) {
                connectAudioWS();
            }
        };
        
        // Route through a silent GainNode to prevent host speaker echo
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
        if (audioCtx && audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
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

async def start_localhost_proxy():
    host = "127.0.0.1"
    port = 8001
    target_host = os.getenv("BACKEND_HOST", "backend-services")
    target_port = int(os.getenv("COPILOT_PORT", "8001"))
    
    async def handle_client(client_reader, client_writer):
        frame_counter = 0
        try:
            initial_data = await client_reader.read(4096)
            if not initial_data:
                client_writer.close()
                return

            request_str = initial_data.decode("utf-8", errors="ignore")
            first_line = request_str.split("\r\n")[0] if request_str else ""
            path = first_line.split(" ")[1] if len(first_line.split(" ")) > 1 else "/api/ws/copilot"

            logger.info(f"[AudioProxy] Browser WebSocket connected: {path}")
            logger.info(f"[AudioProxy] Connecting to {target_host}:{target_port}")

            try:
                target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
                logger.info("[AudioProxy] Backend WebSocket connected")
            except Exception as conn_err:
                logger.error(f"[AudioProxy] Failed connecting to backend {target_host}:{target_port}: {conn_err}")
                client_writer.close()
                return

            target_writer.write(initial_data)
            await target_writer.drain()

            async def forward_browser_to_backend():
                nonlocal frame_counter
                try:
                    while True:
                        data = await client_reader.read(8192)
                        if not data:
                            break
                        frame_counter += 1
                        if frame_counter <= 5 or frame_counter % 100 == 0:
                            logger.info(f"[AudioProxy] Browser -> Backend binary frame #{frame_counter} bytes={len(data)}")
                        target_writer.write(data)
                        await target_writer.drain()
                except Exception:
                    pass
                finally:
                    logger.info("[AudioProxy] Browser WebSocket closed")
                    try:
                        target_writer.close()
                    except Exception:
                        pass

            async def forward_backend_to_browser():
                try:
                    while True:
                        data = await target_reader.read(8192)
                        if not data:
                            break
                        client_writer.write(data)
                        await client_writer.drain()
                except Exception:
                    pass
                finally:
                    logger.info("[AudioProxy] Backend WebSocket closed")
                    try:
                        client_writer.close()
                    except Exception:
                        pass

            await asyncio.gather(
                forward_browser_to_backend(),
                forward_backend_to_browser(),
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"[AudioProxy] Exception during proxy stream: {e}")
        finally:
            try:
                client_writer.close()
            except Exception:
                pass

    try:
        logger.info(f"[AudioProxy] Starting local WebSocket proxy on {host}:{port}")
        server = await asyncio.start_server(handle_client, host, port)
        return server
    except Exception as e:
        logger.info(f"[AudioProxy] Port {port} proxy note: {e}")
        return None

async def run_bot(meeting_url: str, session_id: str):
    # Shared namespace experiment: skip AudioProxy when containers share network namespace
    if USE_SHARED_NAMESPACE:
        logger.info("[TeamsBot] Shared namespace mode: AudioProxy BYPASSED — Chromium connects directly to FastAPI via localhost")
    else:
        logger.info("[TeamsBot] Legacy mode: Starting AudioProxy for CSP-compliant WebSocket relay")
        await start_localhost_proxy()
    
    # Target ws://localhost:9001 (or LOCAL_AUDIO_WS_BASE env) for in-browser JavaScript
    # Teams CSP allows ws://localhost:* — with shared namespace, this reaches FastAPI directly
    browser_ws_url = f"{LOCAL_AUDIO_WS_BASE}/api/ws/copilot/{session_id}?mode=audio_stream"
    logger.info(f"[AudioWS] target URL: {browser_ws_url}")
    logger.info(f"[TeamsBot] Connecting Playwright bot to meeting: {meeting_url}")
    logger.info(f"[TeamsBot] Streaming audio back via CSP-compliant WebSocket: {browser_ws_url}")
    
    formatted_intercept_js = INTERCEPT_JS.replace("%WS_URL%", browser_ws_url)

    async with async_playwright() as p:
        # Launch Chromium with media stream bypass arguments
        browser = await p.chromium.launch(
            headless=BOT_HEADLESS,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--ignore-certificate-errors",
                f"--unsafely-treat-insecure-origin-as-secure={browser_ws_url}",
                f"--unsafely-treat-insecure-origin-as-secure={BACKEND_WS_BASE}",
                "--disable-features=BlockInsecurePrivateNetworkRequests,BlockInsecurePrivateNetworkRequestsFromPrivateNetwork"
            ]
        )
        
        # Open context granting microphone and camera permissions for WebRTC stack initialization
        context = await browser.new_context(
            permissions=["microphone", "camera"],
            bypass_csp=True
        )
        
        # Register init scripts on context BEFORE document navigation so every frame receives them
        await context.add_init_script(CAMERA_BLOCK_JS)
        await context.add_init_script(formatted_intercept_js)
        
        page = await context.new_page()
        page.on("console", lambda msg: logger.info(f"[BrowserConsole] {msg.type}: {msg.text}"))
        
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Page.setBypassCSP", {"enabled": True})
            logger.info("[TeamsBot] CDP Page.setBypassCSP enabled successfully.")
        except Exception as cdpe:
            logger.warning(f"[TeamsBot] Could not set CDP Page.setBypassCSP: {cdpe}")

        # Navigate to Teams Meeting Link
        await page.goto(meeting_url)
        
        # Start background periodic JS interceptor injector
        injector_task = asyncio.create_task(periodic_injector(page, browser_ws_url))
        
        await asyncio.sleep(5.0) # Allow landing page to load fully
        
        # Save landing page screenshot for diagnosis
        debug_dir = os.path.join(os.getcwd(), "interviews", session_id)
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
        formatted_js = INTERCEPT_JS.replace("%WS_URL%", browser_ws_url)
        await page.add_init_script(formatted_js)
        try:
            await page.evaluate(formatted_js)
            logger.info("[TeamsBot] WebRTC interceptor evaluated immediately on page context.")
        except Exception as ee:
            logger.warning(f"[TeamsBot] Direct evaluation of interceptor script skipped/failed: {ee}")
        
        # Enter guest name in name field
        try:
            logger.info("[TeamsBot] Waiting for credentials page to load (can take up to 30-45s)...")
            
            # Check for Teams Meeting Passcode input field
            try:
                passcode_input = page.locator("input[data-tid='meeting-passcode'], input[placeholder*='passcode' i], input[placeholder*='password' i]")
                if await passcode_input.count() > 0 and await passcode_input.first.is_visible(timeout=3000):
                    import urllib.parse
                    parsed = urllib.parse.urlparse(meeting_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    passcode = params.get("p", [""])[0]
                    if passcode:
                        logger.info(f"[TeamsBot] Entering meeting passcode from URL: {passcode}")
                        await passcode_input.first.fill(passcode)
                        await passcode_input.first.press("Enter")
                        await asyncio.sleep(4.0)
            except Exception as pe:
                logger.debug(f"[TeamsBot] Passcode check skipped/not required: {pe}")

            name_input = page.locator(
                "input[data-tid='prejoin-display-name-input'], "
                "input[placeholder='Type your name'], "
                "input.fui-Input__input, "
                "input[placeholder*='Type your name' i], "
                "input[placeholder*='Enter name' i], "
                "input[aria-label*='Type your name' i], "
                "input[aria-label*='Enter name' i]"
            )
            
            # Wait for name field to be loaded/visible
            target_name_input = name_input.first
            await target_name_input.wait_for(state="visible", timeout=BOT_PREJOIN_TIMEOUT_MS)
            
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

            # Fill name input field with fallbacks
            try:
                await target_name_input.click(timeout=3000, force=True)
                await target_name_input.fill(BOT_DISPLAY_NAME, timeout=5000)
            except Exception as fe:
                logger.warning(f"[TeamsBot] Playwright fill/click failed ({fe}); applying direct JS value assignment fallback...")
                await target_name_input.evaluate(
                    "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }",
                    BOT_DISPLAY_NAME
                )

            try:
                await target_name_input.dispatch_event("input")
                await target_name_input.dispatch_event("change")
            except Exception:
                pass
            logger.info("[TeamsBot] Filled guest display name input field.")
            
            # Click "Join Now" or "Join" button
            join_button = page.locator(
                "button#prejoin-join-button, "
                "button[data-tid='prejoin-join-button'], "
                "[id='prejoin-join-button'], "
                "[data-tid='prejoin-join-button'], "
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
                logger.warning(f"[TeamsBot] Direct Join button click failed ({jbe}); attempting JS click & Enter key fallback...")
                try:
                    await target_join_button.evaluate("el => el.click()")
                    logger.info("[TeamsBot] Join request submitted via JS click.")
                except Exception:
                    await target_name_input.press("Enter")
                    logger.info("[TeamsBot] Join request submitted via Enter key press.")

            logger.info("[TeamsBot] Waiting in lobby...")
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
            in_meeting_camera_off = False
            while True:
                await asyncio.sleep(3)
                # Keep active check of the browser window health
                if page.is_closed():
                    logger.warning("[TeamsBot] Teams browser page closed. Exiting...")
                    break

                all_frames = [page] + list(page.frames)

                # In-meeting top toolbar camera check across all frames
                if not in_meeting_camera_off:
                    for frame in all_frames:
                        try:
                            in_meeting_camera = frame.locator("button[data-tid='camera-button'], button[aria-label*='camera' i], button[aria-label*='video' i]").first
                            if await in_meeting_camera.is_visible(timeout=500):
                                label = (await in_meeting_camera.get_attribute("aria-label") or "").lower()
                                pressed = (await in_meeting_camera.get_attribute("aria-pressed") or "").lower()
                                if pressed == "true" or ("turn camera off" in label) or ("camera on" in label and "turn camera on" not in label):
                                    await in_meeting_camera.click()
                                    in_meeting_camera_off = True
                                    logger.info("[TeamsBot] In-meeting top toolbar camera clicked OFF.")
                                    break
                                elif "turn camera on" in label or pressed == "false":
                                    in_meeting_camera_off = True
                                    logger.info(f"[TeamsBot] In-meeting camera already OFF (label: '{label}').")
                                    break
                        except Exception:
                            pass

                # In-meeting top toolbar microphone mute check across all frames
                if not in_meeting_muted:
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

                # Meeting Termination & Empty Room Detection (Autonomous Shutdown)
                meeting_ended = False
                for frame in all_frames:
                    try:
                        # Detect Teams post-meeting / left meeting screen or rejoin button
                        end_indicator = frame.locator(
                            "[data-tid='call-ended'], "
                            "button[data-tid='rejoin-button'], "
                            "button:has-text('Rejoin'), "
                            "[aria-label*='Rejoin' i], "
                            "div:has-text('You left the meeting'), "
                            "div:has-text('The meeting has ended'), "
                            "h1:has-text('You left the meeting'), "
                            "h2:has-text('You left the meeting')"
                        ).first
                        if await end_indicator.is_visible(timeout=200):
                            logger.info("[TeamsBot] Teams meeting has ended / left meeting screen detected. Exiting browser...")
                            meeting_ended = True
                            break

                        # Detect "You're the only one here" or empty room after joining
                        only_one = frame.locator(
                            "div:has-text('You’re the only one here'), "
                            "div:has-text('You\\'re the only one here'), "
                            "span:has-text('You’re the only one here'), "
                            "span:has-text('You\\'re the only one here')"
                        ).first
                        if await only_one.is_visible(timeout=200):
                            logger.info("[TeamsBot] All other participants left the meeting ('You\\'re the only one here'). Exiting browser...")
                            meeting_ended = True
                            break
                    except Exception:
                        pass

                if meeting_ended:
                    break
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
