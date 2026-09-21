/**
 * Microsoft Teams Native Live Captions Observer & Transport Bridge
 * File: services/browser/src/browser_js/native_captions.js
 * 
 * Captures Teams native live caption mutations from:
 *   [data-tid="closed-caption-renderer-wrapper"]
 *     -> [data-tid="author"]
 *     -> [data-tid="closed-caption-text"]
 * 
 * Transports events directly to Copilot backend via dedicated WebSocket
 * (/api/ws/copilot/{sessionId}?mode=native_captions) with HTTP POST fallback.
 */

(function initTeamsNativeCaptions() {
    // Singleton guard: if already running, tear down previous before re-initializing
    if (window.__miaCaptionsRunning && window.__stopMiaCaptions__) {
        try { window.__stopMiaCaptions__(); } catch (e) {}
    }

    const SESSION_ID = "%SESSION_ID%";
    const WS_URL = "%WS_URL%";
    const HTTP_URL = "%HTTP_URL%";

    console.log(`[MIA CAPTIONS] Initializing native caption transport for session=${SESSION_ID}`);
    console.log(`[MIA CAPTIONS] WS_URL: ${WS_URL}`);
    console.log(`[MIA CAPTIONS] HTTP_URL: ${HTTP_URL}`);

    window.__miaCaptionsRunning = true;

    let ws = null;
    let observer = null;
    let pollInterval = null;
    let isReconnecting = false;

    // Sequence & Deduplication State
    let currentSequence = 0;
    let lastSpeaker = null;
    let lastSentTextBySeq = {};
    const seenMessageElements = new WeakMap();
    const activeTrackedElements = new Map();

    // -------------------------------------------------------------
    // WebSocket Transport with Reconnection & HTTP Fallback
    // -------------------------------------------------------------
    function connectWS() {
        if (!window.__miaCaptionsRunning) return;
        try {
            console.log(`[MIA CAPTIONS] Connecting to WebSocket: ${WS_URL}`);
            ws = new WebSocket(WS_URL);

            ws.onopen = () => {
                console.log("[MIA CAPTIONS] WebSocket connected successfully.");
                isReconnecting = false;
            };

            ws.onerror = (err) => {
                console.warn("[MIA CAPTIONS] WebSocket encountered error:", err);
            };

            ws.onclose = (evt) => {
                console.log(`[MIA CAPTIONS] WebSocket closed (code=${evt.code}, reason=${evt.reason})`);
                ws = null;
                if (window.__miaCaptionsRunning && evt.code !== 1000 && !isReconnecting) {
                    isReconnecting = true;
                    setTimeout(() => {
                        if (window.__miaCaptionsRunning) {
                            connectWS();
                        }
                    }, 3000);
                }
            };
        } catch (e) {
            console.error("[MIA CAPTIONS] Failed to establish WebSocket:", e);
            ws = null;
        }
    }

    function sendCaptionEvent(payload) {
        if (!window.__miaCaptionsRunning) return;

        const payloadStr = JSON.stringify(payload);

        // 1. Try WebSocket primary transport
        if (ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(payloadStr);
                return;
            } catch (wse) {
                console.warn("[MIA CAPTIONS] WebSocket send failed, falling back to HTTP:", wse);
            }
        }

        // 2. HTTP POST fallback
        if (HTTP_URL && HTTP_URL.indexOf("http") === 0) {
            try {
                fetch(HTTP_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: payloadStr
                }).catch(err => {
                    console.debug("[MIA CAPTIONS] HTTP fallback error:", err);
                });
            } catch (fe) {
                console.warn("[MIA CAPTIONS] HTTP fallback exception:", fe);
            }
        }
    }

    // Connect transport
    connectWS();

    // -------------------------------------------------------------
    // Caption Element Scanning & Emission Engine
    // -------------------------------------------------------------
    function scanAndEmitCaptions() {
        if (!window.__miaCaptionsRunning) return;

        const textElements = [];

        function collectElements(doc) {
            if (!doc) return;
            // Prefer the exact caption text node; only fallback to container if no text node exists
            let found = Array.from(doc.querySelectorAll('[data-tid="closed-caption-text"]'));
            if (found.length === 0) {
                found = Array.from(doc.querySelectorAll('.fui-ChatMessageCompact__body, span[class*="CaptionsMessage"]'));
            }
            // De-duplicate any nested/parent elements
            const nonNested = found.filter((el, idx) => {
                return !found.some((other, oIdx) => idx !== oIdx && other.contains(el));
            });
            nonNested.forEach(el => textElements.push(el));
        }

        // 1. Search top-level document
        try {
            collectElements(document);
        } catch (e) {}

        // 2. Search all iframes
        try {
            document.querySelectorAll("iframe").forEach(ifr => {
                try {
                    const doc = ifr.contentDocument || ifr.contentWindow.document;
                    collectElements(doc);
                } catch (e) {}
            });
        } catch (e) {}

        for (const textEl of textElements) {
            let rawText = (textEl.innerText || "").trim();
            if (!rawText) continue;

            // Locate enclosing card or container with author
            let authorEl = null;
            let card = textEl.parentElement;
            let depth = 0;
            while (card && depth < 8) {
                authorEl = card.querySelector('[data-tid="author"], .fui-ChatMessageCompact__author');
                if (authorEl) break;
                card = card.parentElement;
                depth++;
            }

            let rawSpeaker = authorEl ? (authorEl.innerText || "").trim() : "";
            if (rawSpeaker) {
                lastSpeaker = rawSpeaker;
            } else {
                rawSpeaker = lastSpeaker || "Unknown";
            }

            // Strip speaker prefix if repeated in text body
            if (rawSpeaker && rawSpeaker !== "Unknown" && rawText.startsWith(rawSpeaker)) {
                rawText = rawText.substring(rawSpeaker.length).replace(/^[:\s\-]+/, "").trim();
            }

            if (!rawText) continue;

            // Determine sequence ID & DOM action
            let isNewSequence = false;
            let seqId = seenMessageElements.get(textEl);
            if (seqId === undefined) {
                if (card && seenMessageElements.has(card)) {
                    seqId = seenMessageElements.get(card);
                } else {
                    currentSequence++;
                    seqId = currentSequence;
                    isNewSequence = true;
                    seenMessageElements.set(textEl, seqId);
                    if (card) seenMessageElements.set(card, seqId);
                }
            }

            // Detect speaker change on same message node
            if (card && card.__lastSpeaker && card.__lastSpeaker !== rawSpeaker) {
                currentSequence++;
                seqId = currentSequence;
                isNewSequence = true;
                seenMessageElements.set(textEl, seqId);
                seenMessageElements.set(card, seqId);
            }
            if (card) card.__lastSpeaker = rawSpeaker;

            // Deduplication against last sent text for this sequence
            const prevText = lastSentTextBySeq[seqId] || "";
            if (rawText === prevText) {
                continue;
            }

            lastSentTextBySeq[seqId] = rawText;
            const domAction = isNewSequence ? "inserted" : "updated";

            activeTrackedElements.set(seqId, {
                textEl: textEl,
                card: card,
                rawSpeaker: rawSpeaker,
                lastText: rawText
            });

            const eventPayload = {
                event_type: "native_caption",
                dom_action: domAction,
                session_id: SESSION_ID,
                event_id: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ("evt-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8)),
                speaker_name: rawSpeaker,
                text: rawText,
                detected_at: new Date().toISOString(),
                source: "teams_native",
                is_final: null,
                finality: "unknown",
                caption_sequence: seqId
            };

            console.log(`[MIA CAPTIONS] Captured (seq=${seqId}, action=${domAction}) [${rawSpeaker}]: "${rawText.substring(0, 40)}..."`);
            sendCaptionEvent(eventPayload);
        }

        // Check for removed DOM elements across previously tracked sequences (Strategy B)
        for (const [trackedSeqId, trackedEntry] of Array.from(activeTrackedElements.entries())) {
            if (trackedEntry.textEl && !trackedEntry.textEl.isConnected) {
                const removalPayload = {
                    event_type: "native_caption",
                    dom_action: "removed",
                    session_id: SESSION_ID,
                    event_id: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : ("evt-rem-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8)),
                    speaker_name: trackedEntry.rawSpeaker,
                    text: trackedEntry.lastText,
                    detected_at: new Date().toISOString(),
                    source: "teams_native",
                    is_final: true,
                    finality: "dom_removed",
                    caption_sequence: trackedSeqId
                };
                console.log(`[MIA CAPTIONS] Node removed from DOM (seq=${trackedSeqId}) [${trackedEntry.rawSpeaker}]: "${trackedEntry.lastText.substring(0, 40)}..."`);
                sendCaptionEvent(removalPayload);
                activeTrackedElements.delete(trackedSeqId);
            }
        }
    }

    // -------------------------------------------------------------
    // MutationObserver & Dual-Trigger Watcher
    // -------------------------------------------------------------
    function setupMutationObserver() {
        if (observer) {
            try { observer.disconnect(); } catch (e) {}
            observer = null;
        }

        // Attach to caption container if found, otherwise document.body
        const container = document.querySelector('div[data-tid="closed-caption-renderer-wrapper"], div[aria-label="Live Captions"]') || document.body;

        observer = new MutationObserver(() => {
            if (!window.__miaCaptionsRunning) return;
            scanAndEmitCaptions();
        });

        observer.observe(container, {
            childList: true,
            subtree: true,
            characterData: true
        });

        console.log("[MIA CAPTIONS] MutationObserver attached to:", container.tagName);
    }

    setupMutationObserver();

    // High-frequency polling heartbeat (every 250ms) to guarantee zero missed updates
    pollInterval = setInterval(scanAndEmitCaptions, 250);

    // Initial immediate scan
    scanAndEmitCaptions();

    // -------------------------------------------------------------
    // Clean Shutdown Handler
    // -------------------------------------------------------------
    window.__stopMiaCaptions__ = function() {
        console.log("[MIA CAPTIONS] __stopMiaCaptions__ called. Tearing down transport and observer...");
        window.__miaCaptionsRunning = false;

        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }

        if (observer) {
            try { observer.disconnect(); } catch (e) {}
            observer = null;
        }

        if (ws) {
            try {
                ws.close(1000, "Normal Closure");
            } catch (e) {}
            ws = null;
        }

        lastSentTextBySeq = {};
        activeTrackedElements.clear();
        console.log("[MIA CAPTIONS] Teardown complete.");
    };

    console.log("[MIA CAPTIONS] Native captions observer initialized successfully.");
})();
