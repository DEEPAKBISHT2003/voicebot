import asyncio
import os
import sys
from typing import Optional
from playwright.async_api import Page
from loguru import logger

# Ensure src directory and project root are in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(_src_dir)))

for _p in [_src_dir, _project_root]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from config import BACKEND_HOST, COPILOT_PORT, USE_SHARED_NAMESPACE
except ImportError:
    from services.browser.src.config import BACKEND_HOST, COPILOT_PORT, USE_SHARED_NAMESPACE


# Load browser audio bridge JavaScript
_AUDIO_BRIDGE_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "browser_js",
    "audio_bridge.js",
)
with open(_AUDIO_BRIDGE_JS_PATH, "r", encoding="utf-8") as _f:
    UNIFIED_BROWSER_AUDIO_JS = _f.read()


async def periodic_injector(page: Page, ws_url: str) -> None:
    """Periodically re-injects the WebRTC / audio processor script into all frames."""
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


async def start_localhost_proxy(
    host: str = "127.0.0.1",
    port: int = 8000,
    target_host: str = BACKEND_HOST,
    target_port: int = COPILOT_PORT,
):
    """Starts local TCP proxy relay for legacy non-shared network namespace."""
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
                return_exceptions=True,
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


class AudioBridge:
    """Manages audio bridge script formatting, periodic frame injection, and WebSocket proxying."""

    def __init__(self, browser_ws_url: str):
        self.browser_ws_url = browser_ws_url
        self.injector_task: Optional[asyncio.Task] = None
        self.proxy_server = None

    def get_formatted_js(self) -> str:
        """Return the browser audio bridge JavaScript with the WebSocket URL interpolated."""
        return UNIFIED_BROWSER_AUDIO_JS.replace("%WS_URL%", self.browser_ws_url)

    async def setup_proxy_if_needed(self, use_shared_namespace: bool = USE_SHARED_NAMESPACE) -> None:
        """Start local TCP AudioProxy if not using container shared network namespace."""
        if use_shared_namespace:
            logger.info("[TeamsBot] Shared namespace mode: AudioProxy BYPASSED — Chromium connects directly to FastAPI via localhost")
        else:
            logger.info("[TeamsBot] Legacy mode: Starting AudioProxy for CSP-compliant WebSocket relay")
            self.proxy_server = await start_localhost_proxy()

    def start_injector(self, page: Page) -> asyncio.Task:
        """Start the background periodic script injector task for all frames."""
        self.injector_task = asyncio.create_task(periodic_injector(page, self.browser_ws_url))
        return self.injector_task

    def stop_injector(self) -> None:
        """Stop the background periodic script injector task."""
        if self.injector_task and not self.injector_task.done():
            self.injector_task.cancel()
            self.injector_task = None
