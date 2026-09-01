import asyncio
import os
import sys
import time
from dotenv import load_dotenv

# Load .env file with override so that .env values strictly take precedence over inherited shell variables
load_dotenv(override=True)

from playwright.async_api import async_playwright
from loguru import logger

# Configuration defaults — points to Copilot Service WebSocket & Browser settings
BACKEND_WS_BASE = os.getenv("COPILOT_WS_BASE", os.getenv("BACKEND_WS_BASE", "ws://127.0.0.1:8000"))
LOCAL_AUDIO_WS_BASE = os.getenv("LOCAL_AUDIO_WS_BASE", "ws://127.0.0.1:8000")
# Shared namespace: when True, skip AudioProxy and connect directly to FastAPI via localhost:8000
USE_SHARED_NAMESPACE = os.getenv("USE_SHARED_NAMESPACE", "true").lower() == "true"
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "Mia - AI Interviewer")
BOT_ROLE = os.getenv("BOT_ROLE", "interviewer")
BOT_HEADLESS = os.getenv("BOT_HEADLESS", "true").lower() == "true"
BOT_PREJOIN_TIMEOUT_MS = int(os.getenv("BOT_PREJOIN_TIMEOUT_MS", "75000"))
MIA_JOIN_ONLY = os.getenv("MIA_JOIN_ONLY", "false").lower() == "true"

UNIFIED_BROWSER_AUDIO_JS = """
(() => {
    // 1. Single Global Audio Context, Destination & Virtual Track initialized IMMEDIATELY at load time
    if (!window.__audioCtx) {
        const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
        window.__audioCtx = new AudioCtxClass({ sampleRate: 48000 });
        window.__audioDestinationNode = window.__audioCtx.createMediaStreamDestination();
        window.__virtualMicTrack = window.__audioDestinationNode.stream.getAudioTracks()[0];
        
        console.log(`[MIA-AUDIO] AudioContext initialized at ${window.__audioCtx.sampleRate}Hz. State: ${window.__audioCtx.state}`);
        console.log(`[MIA-AUDIO] Virtual Track created: ID=${window.__virtualMicTrack.id}, readyState=${window.__virtualMicTrack.readyState}`);
    }

    const audioCtx = window.__audioCtx;
    const audioDestinationNode = window.__audioDestinationNode;
    const virtualMicTrack = window.__virtualMicTrack;

    function ensureAudioContextRunning() {
        if (audioCtx && audioCtx.state === 'suspended') {
            audioCtx.resume().then(() => {
                console.log("[MIA-AUDIO] AudioContext resumed successfully.");
            }).catch(e => {});
        }
    }
    ensureAudioContextRunning();

    // Black Video Track Helper (matching proto)
    function createBlackVideoTrack() {
        try {
            const canvas = document.createElement("canvas");
            canvas.width = 640;
            canvas.height = 480;
            const ctx = canvas.getContext("2d");
            ctx.fillStyle = "black";
            ctx.fillRect(0, 0, 640, 480);
            const blackStream = canvas.captureStream(1);
            return blackStream.getVideoTracks()[0];
        } catch (e) {
            return null;
        }
    }

    // REQUIREMENT 4 — getUserMedia Interception (proto architecture)
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && !navigator.mediaDevices.__miaPatched) {
        const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async function(constraints) {
            console.log("[MIA-GUM] getUserMedia requested with constraints:", JSON.stringify(constraints));
            ensureAudioContextRunning();
            const wantsAudio = constraints && constraints.audio;
            const wantsVideo = constraints && constraints.video;

            if (wantsAudio) {
                let tracks = [virtualMicTrack];
                if (wantsVideo) {
                    const blackTrack = createBlackVideoTrack();
                    if (blackTrack) tracks.push(blackTrack);
                }
                console.log(`[MIA-GUM] Returning Virtual Mic Track: ${virtualMicTrack.id}`);
                return new MediaStream(tracks);
            }

            if (wantsVideo && !wantsAudio) {
                const blackTrack = createBlackVideoTrack();
                if (blackTrack) return new MediaStream([blackTrack]);
            }

            return origGetUserMedia(constraints);
        };
        navigator.mediaDevices.__miaPatched = true;
    }

    // REQUIREMENT 6 — replaceTrack Interception
    if (window.RTCRtpSender && window.RTCRtpSender.prototype.replaceTrack && !window.RTCRtpSender.prototype.__miaPatched) {
        const origReplaceTrack = window.RTCRtpSender.prototype.replaceTrack;
        window.RTCRtpSender.prototype.replaceTrack = async function(newTrack) {
            ensureAudioContextRunning();
            if (newTrack && newTrack.kind === 'audio') {
                console.log(`[MIA-WEBRTC] replaceTrack intercepted for audio! Supplying virtual track: ${virtualMicTrack.id}`);
                return origReplaceTrack.call(this, virtualMicTrack);
            }
            if (newTrack && newTrack.kind === 'video') {
                const blackTrack = createBlackVideoTrack();
                return origReplaceTrack.call(this, blackTrack);
            }
            return origReplaceTrack.call(this, newTrack);
        };
        window.RTCRtpSender.prototype.__miaPatched = true;
    }

    // REQUIREMENT 5 — addTrack Interception (proto architecture)
    if (window.RTCPeerConnection && !window.RTCPeerConnection.__miaPatched) {
        const origPeerConnection = window.RTCPeerConnection;
        window.__activePeerConnections = window.__activePeerConnections || [];

        const PatchedPeerConnection = function(...args) {
            const pc = new origPeerConnection(...args);
            const pcId = window.__activePeerConnections.length + 1;
            pc.__pcId = pcId;
            window.__activePeerConnections.push(pc);
            console.log(`[MIA-WEBRTC] RTCPeerConnection #${pcId} created.`);

            const origAddTrack = pc.addTrack;
            pc.addTrack = function(track, ...streamArgs) {
                ensureAudioContextRunning();
                if (track && track.kind === 'audio') {
                    console.log(`[MIA-WEBRTC] PC #${pcId} addTrack intercepted for audio! Adding virtual track: ${virtualMicTrack.id}`);
                    const sender = origAddTrack.call(this, virtualMicTrack, ...streamArgs);
                    window.__miaAudioSender__ = sender;
                    return sender;
                }
                if (track && track.kind === 'video') {
                    const blackTrack = createBlackVideoTrack();
                    if (blackTrack) {
                        return origAddTrack.call(this, blackTrack, ...streamArgs);
                    }
                    return null;
                }
                return origAddTrack.call(this, track, ...streamArgs);
            };

            return pc;
        };
        PatchedPeerConnection.prototype = origPeerConnection.prototype;
        PatchedPeerConnection.__miaPatched = true;
        window.RTCPeerConnection = PatchedPeerConnection;
        if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = PatchedPeerConnection;
    }
    // ---- Phase 1: Singleton audio pipeline state on window globals ----
    // All mutable audio state lives on window.* so repeated IIFE re-injection
    // by periodic_injector reuses the same state instead of creating new closures.
    if (window.__miaAudioPipelineInitialized) {
        console.log("[MIA WS] processor already initialized — skip (re-injection)");
        return; // Entire IIFE is a no-op on re-injection
    }

    // First-time initialization: claim the global flag
    window.__miaAudioPipelineInitialized = true;
    window.__miaSocket = null;
    window.__miaFrameCounter = 0;
    window.__miaPendingFrames = [];
    window.__miaSharedProcessor = null;
    window.__miaCapturedTrackIds = new Set();
    window.__miaCapturedStreams = new Set();
    window.__nextPlaybackTime = window.__nextPlaybackTime || 0;
    window.__miaReceivedAudioFrames = 0;
    window.__miaReceivedAudioBytes = 0;
    window.__miaConnectionId = 0;

    console.log("[MIA WS] initialize processor — first-time setup");

    function connectAudioWS(wsUrl) {
        const targetUrl = wsUrl || "%WS_URL%";
        const sock = window.__miaSocket;

        // Singleton guard: never create a second connection
        if (sock && (sock.readyState === WebSocket.OPEN)) {
            console.log(`[MIA WS] connection already OPEN (connection_id=${window.__miaConnectionId}) — skip`);
            return;
        }
        if (sock && (sock.readyState === WebSocket.CONNECTING)) {
            console.log(`[MIA WS] connection already CONNECTING (connection_id=${window.__miaConnectionId}) — skip`);
            return;
        }

        // Create new singleton connection
        window.__miaConnectionId++;
        const connId = window.__miaConnectionId;
        console.log(`[MIA WS] creating new connection (connection_id=${connId}) to ${targetUrl}`);

        try {
            const newSocket = new WebSocket(targetUrl);
            newSocket.binaryType = "arraybuffer";
            newSocket.__connId = connId;
            window.__miaSocket = newSocket;

            newSocket.onopen = () => {
                console.log(`[MIA WS] connected (connection_id=${connId})`);
                ensureAudioContextRunning();
                // Flush any pending frames
                while (window.__miaPendingFrames.length > 0 && newSocket.readyState === WebSocket.OPEN) {
                    const f = window.__miaPendingFrames.shift();
                    newSocket.send(f.buffer);
                }
            };

            newSocket.onmessage = (event) => {
                if (!(event.data instanceof ArrayBuffer)) {
                    return;
                }
                ensureAudioContextRunning();

                const rawBuffer = event.data;
                const pcm16Data = new Int16Array(rawBuffer);
                const numSamples = pcm16Data.length;
                if (numSamples === 0) return;

                window.__miaReceivedAudioFrames++;
                window.__miaReceivedAudioBytes += rawBuffer.byteLength;

                // Pipecat RawPCMAudioSerializer sends 16-bit PCM mono @ 16000 Hz
                const pcmSampleRate = 16000;
                const durationSec = numSamples / pcmSampleRate;

                if (window.__miaReceivedAudioFrames <= 5 || window.__miaReceivedAudioFrames % 50 === 0) {
                    console.log(`[MIA AudioWS IN] Binary audio frame #${window.__miaReceivedAudioFrames}: ${numSamples} samples (${durationSec.toFixed(3)}s @ ${pcmSampleRate}Hz), bytes=${rawBuffer.byteLength}, total_bytes=${window.__miaReceivedAudioBytes}, connection_id=${connId}`);
                }

                // 1. Create AudioBuffer with 1 channel and 16000Hz native sample rate
                const audioBuffer = audioCtx.createBuffer(1, numSamples, pcmSampleRate);
                const channelData = audioBuffer.getChannelData(0);

                // 2. Convert Int16 (-32768 to 32767) to Float32 (-1.0 to 1.0)
                for (let i = 0; i < numSamples; i++) {
                    channelData[i] = pcm16Data[i] / 32768.0;
                }

                // 3. Create AudioBufferSourceNode
                const sourceNode = audioCtx.createBufferSource();
                sourceNode.buffer = audioBuffer;

                // 4. Connect sourceNode to window.__audioDestinationNode
                const destination = window.__audioAnalyser || audioDestinationNode;
                sourceNode.connect(destination);

                // 5. Sequential Browser-side AudioContext Scheduling (Zero gaps, zero overlap)
                const currentTime = audioCtx.currentTime;
                if (window.__nextPlaybackTime < currentTime) {
                    window.__nextPlaybackTime = currentTime + 0.05; // 50ms buffer for initial scheduling
                }

                const scheduleTime = window.__nextPlaybackTime;
                sourceNode.start(scheduleTime);
                window.__nextPlaybackTime += audioBuffer.duration;

                if (window.__miaReceivedAudioFrames <= 5 || window.__miaReceivedAudioFrames % 50 === 0) {
                    console.log(`[MIA SPEAKING DIAG] Scheduled AudioBufferSourceNode #${window.__miaReceivedAudioFrames} at t=${scheduleTime.toFixed(3)}s (ctx.currentTime=${currentTime.toFixed(3)}s, duration=${audioBuffer.duration.toFixed(3)}s, next=${window.__nextPlaybackTime.toFixed(3)}s)`);
                }
            };

            newSocket.onerror = (err) => {
                console.error(`[MIA WS] error (connection_id=${connId}):`, err);
            };

            newSocket.onclose = (evt) => {
                console.log(`[MIA WS] closed (connection_id=${connId}, code=${evt.code}, reason=${evt.reason})`);
                // Only clear the reference if this is still the active socket
                if (window.__miaSocket && window.__miaSocket.__connId === connId) {
                    window.__miaSocket = null;
                }
            };
        } catch (e) {
            console.error(`[MIA WS] failed to create WebSocket (connection_id=${window.__miaConnectionId}):`, e);
            window.__miaSocket = null;
        }
    }

    // Explicit gate function — the ONLY entry point for creating the WebSocket
    window.__connectMiaWebSocket__ = function(url) {
        console.log(`[MIA WS] __connectMiaWebSocket__ called (current connection_id=${window.__miaConnectionId})`);
        connectAudioWS(url);
    };

    function initSharedProcessor() {
        if (window.__miaSharedProcessor) {
            // Already initialized — idempotent, no-op
            return;
        }

        if (audioCtx.state === 'suspended') {
            audioCtx.resume().then(() => {
                console.log("[TeamsBot] AudioContext resumed successfully.");
            }).catch(err => {
                console.error("[TeamsBot] Failed to resume AudioContext:", err);
            });
        }

        // NOTE: Do NOT call connectAudioWS() here.
        // The WebSocket connection is deferred until the Python-side gate
        // calls window.__connectMiaWebSocket__() after confirming IN_MEETING.

        // Create a single shared processor node for mixing
        window.__miaSharedProcessor = audioCtx.createScriptProcessor(4096, 1, 1);

        window.__miaSharedProcessor.onaudioprocess = (e) => {
            const inputData = e.inputBuffer.getChannelData(0);

            // Real 48kHz -> 16kHz Linear Interpolation Downsampler
            const inSampleRate = audioCtx.sampleRate || 48000;
            const targetSampleRate = 16000;
            const ratio = inSampleRate / targetSampleRate;
            const resampledLength = Math.floor(inputData.length / ratio);
            const outputData = new Int16Array(resampledLength);

            for (let i = 0; i < resampledLength; i++) {
                const srcIdx = i * ratio;
                const idx0 = Math.floor(srcIdx);
                const idx1 = Math.min(idx0 + 1, inputData.length - 1);
                const frac = srcIdx - idx0;
                const sample = inputData[idx0] * (1 - frac) + inputData[idx1] * frac;
                const s = Math.max(-1, Math.min(1, sample));
                outputData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            const payload = {
                buffer: outputData.buffer,
                byteLength: outputData.buffer.byteLength,
                timestamp: Date.now()
            };

            const sock = window.__miaSocket;
            if (sock && sock.readyState === WebSocket.OPEN) {
                window.__miaFrameCounter++;
                sock.send(payload.buffer);
                if (window.__miaFrameCounter <= 5 || window.__miaFrameCounter % 100 === 0) {
                    console.log(`[AudioWS] [MIA INPUT AUDIO] sending frame #${window.__miaFrameCounter}, in_sampleRate=${inSampleRate}, out_sampleRate=16000, channels=1, in_samples=${inputData.length}, out_samples=${outputData.length}, bytes=${payload.byteLength}, timestamp=${payload.timestamp}, connection_id=${sock.__connId}`);
                }
            } else if (sock && sock.readyState === WebSocket.CONNECTING) {
                if (window.__miaPendingFrames.length < 50) {
                    window.__miaPendingFrames.push(payload);
                }
            }
            // NOTE: Do NOT auto-reconnect here. The gate path handles connection creation.
        };

        // Route through a silent GainNode to prevent host speaker echo
        const silentGain = audioCtx.createGain();
        silentGain.gain.value = 0.0;
        window.__miaSharedProcessor.connect(silentGain);
        silentGain.connect(audioCtx.destination);

        console.log("[MIA WS] Shared audio mixer initialized (silent output, WebSocket deferred).");
    }

    function captureAudioStream(stream) {
        if (!stream || stream.getAudioTracks().length === 0) return;
        if (window.__miaCapturedStreams.has(stream.id)) return;
        window.__miaCapturedStreams.add(stream.id);

        console.log("[TeamsBot] Capturing WebRTC audio track from stream:", stream.id);

        // Ensure the shared mixer is ready (idempotent)
        initSharedProcessor();

        try {
            const source = audioCtx.createMediaStreamSource(stream);
            source.connect(window.__miaSharedProcessor);
            console.log("[TeamsBot] Audio source connected to shared mixer:", stream.id);
        } catch (err) {
            console.error("[TeamsBot] Failed to bind AudioContext source:", err);
        }
    }

    // Defer WebSocket connection until host admits bot into meeting
    console.log("[AudioWS] Script loaded; WebSocket connection deferred until host admission.");

    // Intercept incoming WebRTC Peer Connections for transcript capture
    if (!RTCPeerConnection.prototype.__miaSetRemoteDescPatched) {
        const origSetRemoteDescription = RTCPeerConnection.prototype.setRemoteDescription;
        RTCPeerConnection.prototype.setRemoteDescription = function(desc) {
            this.addEventListener('track', (e) => {
                if (e.track && e.track.kind === 'audio') {
                    if (window.__miaCapturedTrackIds.has(e.track.id)) return;
                    window.__miaCapturedTrackIds.add(e.track.id);

                    const stream = e.streams[0] || new MediaStream([e.track]);
                    captureAudioStream(stream);
                }
            });
            return origSetRemoteDescription.apply(this, [desc]);
        };
        RTCPeerConnection.prototype.__miaSetRemoteDescPatched = true;
    }

    // Periodically search for existing DOM audio elements as a fallback
    setInterval(() => {
        if (audioCtx && audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
        document.querySelectorAll('audio, video').forEach(el => {
            if (el.srcObject) {
                el.srcObject.getAudioTracks().forEach(track => {
                    if (!window.__miaCapturedTrackIds.has(track.id)) {
                        window.__miaCapturedTrackIds.add(track.id);
                        captureAudioStream(el.srcObject);
                    }
                });
            }
        });
    }, 2000);
})();
"""

async def periodic_injector(page, ws_url):
    formatted_js = UNIFIED_BROWSER_AUDIO_JS.replace("%WS_URL%", ws_url)
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
    port = 8000
    target_host = os.getenv("BACKEND_HOST", "backend-services")
    target_port = int(os.getenv("COPILOT_PORT", "8000"))
    
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

async def is_really_in_meeting(page) -> tuple:
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
        "ended_screen": False
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
                    mic_el = frame.locator("button#aria-key-toolbar-microphone, button[data-tid='microphone-button'], button[aria-label*='microphone' i], button[title*='mic' i]").first
                    if await mic_el.is_visible(timeout=50):
                        evidence["toolbar_mic"] = True

                if not evidence["roster_or_canvas"]:
                    roster_canvas = frame.locator("button[data-tid='roster-button'], [data-tid='call-canvas'], .calling-screen, button[aria-label*='people' i], button[title*='people' i]").first
                    if await roster_canvas.is_visible(timeout=50):
                        evidence["roster_or_canvas"] = True

                # Early short-circuit: if we already have strong in-meeting evidence, skip remaining checks
                if evidence["hangup_btn"] or (evidence["toolbar_mic"] and evidence["roster_or_canvas"]):
                    break

                # 2. Prejoin indicators
                if not evidence["prejoin_input"]:
                    name_input = frame.locator("input[data-tid='prejoin-display-name-input'], input[placeholder*='Type your name' i]").first
                    if await name_input.is_visible(timeout=50) and await name_input.is_enabled():
                        evidence["prejoin_input"] = True

                if not evidence["prejoin_join_btn"]:
                    prejoin_join_btn = frame.locator("button#prejoin-join-button, button[data-tid='prejoin-join-button']").first
                    if await prejoin_join_btn.is_visible(timeout=50) and await prejoin_join_btn.is_enabled():
                        evidence["prejoin_join_btn"] = True

                # 3. Lobby / Admitting indicators
                if not evidence["lobby_text"]:
                    lobby_el = frame.locator("text='When the meeting starts', text='let people know you\\'re waiting', text='let you in soon', text='Waiting for someone', [data-tid*='lobby']").first
                    if await lobby_el.is_visible(timeout=50):
                        evidence["lobby_text"] = True

                if not evidence["admitting_text"]:
                    admitting_el = frame.locator("text='Admitting...', text='Getting things ready', text='Joining...', [data-tid*='admitting']").first
                    if await admitting_el.is_visible(timeout=50):
                        evidence["admitting_text"] = True

                # 4. Disconnected / ended screen
                if not evidence["ended_screen"]:
                    ended_el = frame.locator("[data-tid='call-ended'], button[data-tid='rejoin-button'], div:has-text('You left the meeting')").first
                    if await ended_el.is_visible(timeout=50):
                        evidence["ended_screen"] = True
                        break
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[MIA STATE DETECT ERROR] {e}")

    # State Classification Logic
    in_meeting_signals = sum([
        evidence["hangup_btn"],
        evidence["toolbar_mic"],
        evidence["roster_or_canvas"]
    ])

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

async def run_in_meeting_diagnostics(page):
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

async def run_bot(meeting_url: str, session_id: str):
    # Shared namespace experiment: skip AudioProxy when containers share network namespace
    if USE_SHARED_NAMESPACE:
        logger.info("[TeamsBot] Shared namespace mode: AudioProxy BYPASSED — Chromium connects directly to FastAPI via localhost")
    else:
        logger.info("[TeamsBot] Legacy mode: Starting AudioProxy for CSP-compliant WebSocket relay")
        await start_localhost_proxy()
    
    # Target ws://localhost:8000 (or LOCAL_AUDIO_WS_BASE env) for in-browser JavaScript
    # Teams CSP allows ws://localhost:* — with shared namespace, this reaches FastAPI directly
    bot_role = os.getenv("BOT_ROLE", "interviewer")
    if bot_role == "interviewer":
        browser_ws_url = f"{LOCAL_AUDIO_WS_BASE}/api/ws/interview/{session_id}"
    else:
        browser_ws_url = f"{LOCAL_AUDIO_WS_BASE}/api/ws/copilot/{session_id}?mode=audio_stream"

    logger.info(f"[AudioWS] target URL: {browser_ws_url} (bot_role={bot_role})")
    logger.info(f"[TeamsBot] Connecting Playwright bot to meeting: {meeting_url}")
    logger.info(f"[TeamsBot] Streaming audio back via CSP-compliant WebSocket: {browser_ws_url}")
    
    formatted_unified_js = UNIFIED_BROWSER_AUDIO_JS.replace("%WS_URL%", browser_ws_url)

    async with async_playwright() as p:
        # Launch Chromium with media stream bypass arguments
        browser = await p.chromium.launch(
            headless=BOT_HEADLESS,
            args=[
                "--use-fake-ui-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-popup-blocking",
                "--disable-external-intent-requests",
                "--allow-insecure-localhost",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--ignore-certificate-errors",
                f"--unsafely-treat-insecure-origin-as-secure={browser_ws_url}",
                f"--unsafely-treat-insecure-origin-as-secure={BACKEND_WS_BASE}",
                "--disable-features=BlockInsecurePrivateNetworkRequests,BlockInsecurePrivateNetworkRequestsFromPrivateNetwork,ExternalProtocolHandler,PrivateNetworkAccessPermissionPrompt,LocalNetworkAccessChecks"
            ]
        )
        
        # Open context granting microphone and camera permissions with realistic User-Agent
        context = await browser.new_context(
            permissions=["microphone", "camera"],
            bypass_csp=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # Suppress msteams:// and intent:// modal prompts
        await context.route("**/*", lambda route: route.abort() if route.request.url.startswith(("msteams:", "intent:")) else route.continue_())
        
        # Override navigator.webdriver to false to prevent Teams "Suspected threat / Unverified" blocking
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        
        if MIA_JOIN_ONLY:
            logger.info("[TeamsBot] [MIA_JOIN_ONLY=true] Audio/WebRTC interceptor injection SKIPPED to isolate join/admission lifecycle.")
        else:
            await context.add_init_script(formatted_unified_js)
        
        page = await context.new_page()
        page.on("console", lambda msg: logger.info(f"[BrowserConsole] {msg.type}: {msg.text}"))
        
        try:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Page.setBypassCSP", {"enabled": True})
            logger.info("[TeamsBot] CDP Page.setBypassCSP enabled successfully.")
        except Exception as cdpe:
            logger.warning(f"[TeamsBot] Could not set CDP Page.setBypassCSP: {cdpe}")

        # Navigate to Teams Meeting Link
        logger.info("[TeamsBot] Opening meeting URL...")
        await page.goto(meeting_url)
        logger.info("[TeamsBot] Teams page loaded")
        
        # Start background periodic JS interceptor injector only if not MIA_JOIN_ONLY
        if not MIA_JOIN_ONLY:
            injector_task = asyncio.create_task(periodic_injector(page, browser_ws_url))
        
        await asyncio.sleep(5.0) # Allow landing page to load fully
        
        # Stage 01: Teams page loaded screenshot
        debug_dir = os.path.join(os.getcwd(), "interviews", session_id)
        os.makedirs(debug_dir, exist_ok=True)
        try:
            await page.screenshot(path=os.path.join(debug_dir, "01_teams_loaded.png"))
            await page.screenshot(path=os.path.join(debug_dir, "teams_bot_landing.png"))
            logger.info("[TeamsBot] Saved 01_teams_loaded.png screenshot.")
        except Exception as se:
            logger.warning(f"[TeamsBot] Failed to save 01_teams_loaded screenshot: {se}")
        
        # Automate Teams UI Guest Selection Flow
        try:
            logger.info("[TeamsBot] Selecting Web Join option...")
            # Click "Continue on this browser" / "Join on the web instead" button
            clicked = False
            for target in [page] + page.frames:
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
                web_join_button = page.locator(
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
            await asyncio.sleep(5.0) # Wait for prep room to load
        except Exception as e:
            logger.warning(f"[TeamsBot] Bypassing Web Join select step (already on lobby page or redirected): {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_join_redirect.png"))
            except Exception:
                pass

        # Injects WebRTC interception JS code into page initialization only if not MIA_JOIN_ONLY
        if not MIA_JOIN_ONLY:
            formatted_js = UNIFIED_BROWSER_AUDIO_JS.replace("%WS_URL%", browser_ws_url)
            await page.add_init_script(formatted_js)
            try:
                await page.evaluate(formatted_js)
                logger.info("[TeamsBot] WebRTC interceptor evaluated immediately on page context.")
            except Exception as ee:
                logger.warning(f"[TeamsBot] Direct evaluation of interceptor script skipped/failed: {ee}")
        
        # Enter guest name in name field
        try:
            logger.info("[TeamsBot] Waiting for credentials page to load (can take up to 45-60s)...")
            
            target_name_input = None
            start_wait = asyncio.get_event_loop().time()
            max_wait_sec = BOT_PREJOIN_TIMEOUT_MS / 1000.0
            last_reclick_time = 0
            
            while (asyncio.get_event_loop().time() - start_wait) < max_wait_sec:
                # 1. Check for Teams Meeting Passcode input field across all frames
                for target in [page] + page.frames:
                    try:
                        passcode_input = target.locator("input[data-tid='meeting-passcode'], input[placeholder*='passcode' i], input[placeholder*='password' i]")
                        if await passcode_input.count() > 0 and await passcode_input.first.is_visible(timeout=300):
                            import urllib.parse
                            parsed = urllib.parse.urlparse(meeting_url)
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
                for target in [page] + page.frames:
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
                    for target in [page] + page.frames:
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
            
            # Stage 02: Pre-join screen screenshot
            try:
                await page.screenshot(path=os.path.join(debug_dir, "02_prejoin.png"))
            except Exception:
                pass
            
            # Ensure Video Camera is toggled OFF for privacy
            for target in [page] + page.frames:
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
            for target in [page] + page.frames:
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
            logger.info(f"[TeamsBot] Display name configured: {BOT_DISPLAY_NAME}")
            
            # Pre-join microphone switch check
            for frame in page.frames:
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
                await page.screenshot(path=os.path.join(debug_dir, "03_before_join.png"))
            except Exception:
                pass
            
            # Click "Join Now" or "Join" button across all frames
            target_join_button = None
            for target in [page] + page.frames:
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
                target_join_button = page.locator("button#prejoin-join-button, button[data-tid='prejoin-join-button']").first

            logger.info("[TeamsBot] Join button detected")
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

            # Stage 04: Immediately after Join Now screenshot
            try:
                await page.screenshot(path=os.path.join(debug_dir, "04_after_join.png"))
            except Exception:
                pass

            # Stage 05: 5 seconds after Join screenshot
            logger.info("[TeamsBot] Waiting for meeting connection...")
            await asyncio.sleep(5.0)
            try:
                await page.screenshot(path=os.path.join(debug_dir, "05_after_join_5s.png"))
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_lobby.png"))
            except Exception:
                pass

            # Stage 06: Initial state assessment
            is_in, current_state, state_evidence = await is_really_in_meeting(page)
            logger.info(f"[MIA STATE] {current_state} | evidence={state_evidence}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "06_final_state.png"))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[TeamsBot] Failed to automate input names/joining: {e}")
            try:
                await page.screenshot(path=os.path.join(debug_dir, "teams_bot_join_failed.png"))
                logger.info("[TeamsBot] Saved join failure screenshot to session directory.")
            except Exception:
                pass
            
        # Main Teams Meeting Lifecycle & Polling Loop
        try:
            consecutive_in_meeting = 0
            ws_triggered = False
            diagnostics_launched = False
            last_screenshot_state = None
            
            while True:
                await asyncio.sleep(1.5)
                is_in, current_state, evidence = await is_really_in_meeting(page)

                if current_state == "IN_MEETING":
                    consecutive_in_meeting += 1
                else:
                    consecutive_in_meeting = 0

                logger.info(f"[MIA STATE] {current_state} consecutive={consecutive_in_meeting} | evidence={evidence}")

                # Synchronize browser window.__miaState
                try:
                    await page.evaluate(f"window.__miaState = '{current_state}'")
                except Exception:
                    pass

                # Launch non-blocking background diagnostics on entering meeting
                if current_state == "IN_MEETING" and not diagnostics_launched:
                    diagnostics_launched = True
                    asyncio.create_task(run_in_meeting_diagnostics(page))

                # Check if IN_MEETING has remained stable for >= 2 consecutive checks
                if consecutive_in_meeting >= 2:
                    if MIA_JOIN_ONLY:
                        logger.info(f"[MIA JOIN ONLY PASS] Confirmed REAL IN_MEETING state! Audio pipeline isolated.")
                    elif not ws_triggered:
                        logger.info("[MIA STATE] WebSocket gate condition satisfied")
                        logger.info("[MIA WEBSOCKET GATE] Meeting admission confirmed & stable! Triggering WebSocket connection...")
                        try:
                            await page.evaluate("window.__connectMiaWebSocket__ && window.__connectMiaWebSocket__()")
                            for frame in page.frames:
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
                            await page.screenshot(path=os.path.join(debug_dir, "07_lobby.png"))
                        except Exception:
                            pass
                    elif current_state == "ADMITTING":
                        try:
                            await page.screenshot(path=os.path.join(debug_dir, "08_admitting.png"))
                        except Exception:
                            pass
                    elif current_state == "IN_MEETING":
                        try:
                            await page.screenshot(path=os.path.join(debug_dir, "09_final_state.png"))
                        except Exception:
                            pass

                # Autonomous Shutdown Detection
                if current_state == "DISCONNECTED":
                    logger.info("[TeamsBot] Teams meeting ended or disconnected. Exiting browser...")
                    break
        except asyncio.CancelledError:
            logger.info("[TeamsBot] Stopping Teams observer bot.")
        finally:
            if injector_task:
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
