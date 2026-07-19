import asyncio
import os
import sys
from playwright.async_api import async_playwright
from loguru import logger

# Configuration defaults
BACKEND_WS_BASE = os.getenv("BACKEND_WS_BASE", "ws://localhost:8000")

INTERCEPT_JS = """
(async () => {
    console.log("[TeamsBot] Injecting WebRTC audio interceptor...");
    const wsUrl = "%WS_URL%";
    
    // Connect websocket
    const socket = new WebSocket(wsUrl);
    socket.onopen = () => console.log("[TeamsBot] Interceptor WebSocket connected to backend.");
    socket.onclose = () => console.log("[TeamsBot] Interceptor WebSocket closed.");
    socket.onerror = (e) => console.error("[TeamsBot] Interceptor WebSocket error:", e);
    
    // Initialize AudioContext at 16kHz
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const capturedStreams = new Set();
    
    function captureAudioStream(stream) {
        if (capturedStreams.has(stream.id)) return;
        capturedStreams.add(stream.id);
        
        console.log("[TeamsBot] Capturing WebRTC audio track from stream:", stream.id);
        try {
            const source = audioCtx.createMediaStreamSource(stream);
            const processor = audioCtx.createScriptProcessor(4096, 1, 1);
            source.connect(processor);
            processor.connect(audioCtx.destination);
            
            processor.onaudioprocess = (e) => {
                const inputData = e.inputBuffer.getChannelData(0);
                
                // Convert Float32Array to Int16 PCM Mono bytes
                const outputData = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    outputData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(outputData.buffer);
                }
            };
        } catch (err) {
            console.error("[TeamsBot] Failed to bind AudioContext source:", err);
        }
    }

    // Intercept incoming WebRTC Peer Connections
    const origSetRemoteDescription = RTCPeerConnection.prototype.setRemoteDescription;
    RTCPeerConnection.prototype.setRemoteDescription = function(desc) {
        this.addEventListener('track', (e) => {
            if (e.track && e.track.kind === 'audio') {
                const stream = e.streams[0] || new MediaStream([e.track]);
                captureAudioStream(stream);
            }
        });
        return origSetRemoteDescription.apply(this, [desc]);
    };
    
    // Periodically search for existing DOM audio elements as a fallback
    setInterval(() => {
        document.querySelectorAll('audio, video').forEach(el => {
            if (el.srcObject && !capturedStreams.has(el.srcObject.id)) {
                captureAudioStream(el.srcObject);
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
                "--use-fake-device-for-media-stream",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--autoplay-policy=no-user-gesture-required"
            ]
        )
        
        # Open context granting audio/microphone permissions
        context = await browser.new_context(
            permissions=["microphone", "camera"]
        )
        
        page = await context.new_page()
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
                camera_button = page.locator("[aria-label*='camera' i], [aria-label*='video' i], [data-tid*='video']").first
                if await camera_button.is_visible():
                    label = await camera_button.get_attribute("aria-label") or ""
                    pressed = await camera_button.get_attribute("aria-pressed") or ""
                    checked = await camera_button.get_attribute("aria-checked") or ""
                    if checked == "true" or pressed == "true" or "turn camera off" in label.lower() or "video off" not in label.lower():
                        await camera_button.click()
                        logger.info("[TeamsBot] Video camera toggled OFF.")
            except Exception as ce:
                logger.warning(f"[TeamsBot] Could not verify/toggle video camera button: {ce}")

            # Ensure Microphone is toggled OFF (Muted) for privacy
            try:
                mic_button = page.locator("[aria-label*='microphone' i], [aria-label*='mute' i], [data-tid*='mute']").first
                if await mic_button.is_visible():
                    label = await mic_button.get_attribute("aria-label") or ""
                    pressed = await mic_button.get_attribute("aria-pressed") or ""
                    checked = await mic_button.get_attribute("aria-checked") or ""
                    if checked == "true" or pressed == "true" or "mute" in label.lower() or "unmute" not in label.lower():
                        await mic_button.click()
                        logger.info("[TeamsBot] Microphone toggled OFF (Muted).")
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
            while True:
                await asyncio.sleep(5)
                # Keep active check of the browser window health
                if page.is_closed():
                    logger.warning("[TeamsBot] Teams browser page closed. Exiting...")
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
