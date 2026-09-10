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
        console.log(`[MIA WS] CREATED connection_id=${connId} to ${targetUrl}`);

        try {
            const newSocket = new WebSocket(targetUrl);
            newSocket.binaryType = "arraybuffer";
            newSocket.__connId = connId;
            window.__miaSocket = newSocket;

            newSocket.onopen = () => {
                console.log(`[MIA WS] OPEN connection_id=${connId}`);
                ensureAudioContextRunning();
                // Flush any pending frames
                while (window.__miaPendingFrames.length > 0 && newSocket.readyState === WebSocket.OPEN) {
                    const f = window.__miaPendingFrames.shift();
                    newSocket.send(f.buffer);
                }
            };

            // Jitter Buffer & Playback Scheduler Configuration
            const INITIAL_BUFFER_MS = 350;
            const RECOVERY_BUFFER_MS = 250;

            // Stream and Queue tracking state
            window.__miaCurrentStreamId = window.__miaCurrentStreamId || 0;
            window.__miaStreamFrameCount = 0;
            window.__miaLastRxTime = 0;
            window.__miaRxGapCount = 0;
            window.__miaRxUnderrunCount = 0;
            window.__miaRxRecoveryCount = 0;

            window.__miaPlaybackQueue = [];
            window.__miaQueuedDurationMs = 0;
            window.__miaPlaybackState = "BUFFERING"; // "BUFFERING", "PLAYING", "STARVED"
            window.__miaNextPlaybackTime = 0;

            function drainPlaybackQueue() {
                const currentTime = audioCtx.currentTime;
                const destination = window.__audioAnalyser || audioDestinationNode;

                // 1. Initial Buffering Gate: wait until queue accumulates INITIAL_BUFFER_MS
                if (window.__miaPlaybackState === "BUFFERING") {
                    if (window.__miaQueuedDurationMs < INITIAL_BUFFER_MS) {
                        return; // Continue accumulating initial chunks
                    }
                    window.__miaPlaybackState = "PLAYING";
                    window.__miaNextPlaybackTime = currentTime + 0.05;
                    console.log(`[MIA PLAYBACK START] stream=${window.__miaCurrentStreamId} initial_buffer_ms=${window.__miaQueuedDurationMs.toFixed(1)} scheduled_start_t=${window.__miaNextPlaybackTime.toFixed(3)}s ctx_time=${currentTime.toFixed(3)}s connection_id=${connId}`);
                }

                // 2. Underrun Recovery Gate: wait until queue accumulates RECOVERY_BUFFER_MS
                if (window.__miaPlaybackState === "STARVED") {
                    if (window.__miaQueuedDurationMs < RECOVERY_BUFFER_MS) {
                        return; // Continue accumulating recovery chunks
                    }
                    window.__miaPlaybackState = "PLAYING";
                    window.__miaRxRecoveryCount++;
                    window.__miaNextPlaybackTime = currentTime + 0.05;
                    console.log(`[MIA PLAYBACK RECOVERED] stream=${window.__miaCurrentStreamId} recovery_buffer_ms=${window.__miaQueuedDurationMs.toFixed(1)} recovery_count=${window.__miaRxRecoveryCount} scheduled_t=${window.__miaNextPlaybackTime.toFixed(3)}s ctx_time=${currentTime.toFixed(3)}s connection_id=${connId}`);
                }

                // 3. Continuous WebAudio Playback Scheduling
                while (window.__miaPlaybackQueue.length > 0 && window.__miaPlaybackState === "PLAYING") {
                    const item = window.__miaPlaybackQueue[0];

                    // Detect starvation / underrun if scheduled timeline has fallen behind current AudioContext time by >20ms
                    if (window.__miaNextPlaybackTime < currentTime - 0.02) {
                        window.__miaPlaybackState = "STARVED";
                        window.__miaRxUnderrunCount++;
                        const lagMs = ((currentTime - window.__miaNextPlaybackTime) * 1000).toFixed(1);
                        console.warn(`[MIA PLAYBACK UNDERRUN] stream=${item.streamId} frame=${item.frameId} queue_ms=${window.__miaQueuedDurationMs.toFixed(1)} lag=${lagMs}ms context_time=${currentTime.toFixed(3)}s next_playback_time=${window.__miaNextPlaybackTime.toFixed(3)}s underruns=${window.__miaRxUnderrunCount}`);
                        break; // Stop scheduling until recovery buffer refills
                    }

                    // Shift chunk from queue and schedule on continuous timeline
                    window.__miaPlaybackQueue.shift();
                    window.__miaQueuedDurationMs = Math.max(0, window.__miaQueuedDurationMs - item.durationSec * 1000);

                    const sourceNode = audioCtx.createBufferSource();
                    sourceNode.buffer = item.audioBuffer;
                    sourceNode.connect(destination);

                    const scheduleTime = window.__miaNextPlaybackTime;
                    sourceNode.start(scheduleTime);
                    window.__miaNextPlaybackTime += item.durationSec;

                    const headroomMs = ((scheduleTime - currentTime) * 1000).toFixed(1);

                    // Periodic telemetry
                    if (item.frameId <= 5 || item.frameId % 50 === 0) {
                        console.log(`[MIA PLAYBACK] stream=${item.streamId} frame=${item.frameId} scheduled_t=${scheduleTime.toFixed(3)}s ctx_t=${currentTime.toFixed(3)}s headroom=${headroomMs}ms queued_ms=${window.__miaQueuedDurationMs.toFixed(1)} underruns=${window.__miaRxUnderrunCount}`);
                        console.log(`[MIA PLAYBACK BUFFER] stream=${item.streamId} queued_ms=${window.__miaQueuedDurationMs.toFixed(1)} scheduled_headroom_ms=${headroomMs} state=${window.__miaPlaybackState}`);
                    }
                }
            }

            // Periodic interval to ensure queue drains even if packets pause
            if (window.__miaPlaybackDrainTimer) clearInterval(window.__miaPlaybackDrainTimer);
            window.__miaPlaybackDrainTimer = setInterval(() => {
                if (window.__miaPlaybackQueue && window.__miaPlaybackQueue.length > 0) {
                    drainPlaybackQueue();
                }
            }, 40);

            newSocket.onmessage = (event) => {
                if (!(event.data instanceof ArrayBuffer)) {
                    return;
                }
                ensureAudioContextRunning();

                const now = Date.now();
                const dt = window.__miaLastRxTime > 0 ? (now - window.__miaLastRxTime) : 0;
                window.__miaLastRxTime = now;

                // Detect new stream boundary if gap is > 1.2s or initial stream
                if (window.__miaStreamFrameCount === 0 || dt > 1200) {
                    window.__miaCurrentStreamId++;
                    window.__miaStreamFrameCount = 0;
                    window.__miaPlaybackState = "BUFFERING";
                    window.__miaPlaybackQueue = [];
                    window.__miaQueuedDurationMs = 0;
                    window.__miaNextPlaybackTime = 0;
                    console.log(`[MIA AUDIO STREAM START] stream=${window.__miaCurrentStreamId} reason=${dt > 1200 ? 'gap_boundary' : 'initial_stream'} connection_id=${connId}`);
                }

                window.__miaStreamFrameCount++;
                window.__miaReceivedAudioFrames++;
                const rawBuffer = event.data;
                const pcm16Data = new Int16Array(rawBuffer);
                const numSamples = pcm16Data.length;
                if (numSamples === 0) return;

                window.__miaReceivedAudioBytes += rawBuffer.byteLength;

                // Detect arrival timing gaps (>200ms)
                if (dt > 200 && window.__miaStreamFrameCount > 1) {
                    window.__miaRxGapCount++;
                    console.warn(`[MIA WS RX GAP] stream=${window.__miaCurrentStreamId} frame=${window.__miaStreamFrameCount} dt=${dt}ms connection_id=${connId}`);
                }

                if (window.__miaStreamFrameCount <= 5 || window.__miaStreamFrameCount % 50 === 0) {
                    console.log(`[MIA WS RX] connection=${connId} stream=${window.__miaCurrentStreamId} frame=${window.__miaStreamFrameCount} bytes=${rawBuffer.byteLength} dt=${dt}ms total_rx_frames=${window.__miaReceivedAudioFrames}`);
                }

                // Pipecat RawPCMAudioSerializer sends 16-bit PCM mono @ 16000 Hz
                const pcmSampleRate = 16000;
                const durationSec = numSamples / pcmSampleRate;

                // 1. Create AudioBuffer with 1 channel and 16000Hz native sample rate
                const audioBuffer = audioCtx.createBuffer(1, numSamples, pcmSampleRate);
                const channelData = audioBuffer.getChannelData(0);

                // 2. Convert Int16 (-32768 to 32767) to Float32 (-1.0 to 1.0)
                for (let i = 0; i < numSamples; i++) {
                    channelData[i] = pcm16Data[i] / 32768.0;
                }

                // 3. Push to playback queue
                window.__miaPlaybackQueue.push({
                    audioBuffer: audioBuffer,
                    durationSec: durationSec,
                    streamId: window.__miaCurrentStreamId,
                    frameId: window.__miaStreamFrameCount
                });
                window.__miaQueuedDurationMs += durationSec * 1000;

                // 4. Drain queue & schedule WebAudio playback
                drainPlaybackQueue();
            };

            newSocket.onerror = (err) => {
                console.error(`[MIA WS] error (connection_id=${connId}):`, err);
            };

            newSocket.onclose = (evt) => {
                console.log(`[MIA WS] CLOSED connection_id=${connId} code=${evt.code} reason=${evt.reason}`);
                if (window.__miaPlaybackDrainTimer) {
                    clearInterval(window.__miaPlaybackDrainTimer);
                }
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

            const now = payload.timestamp;
            const dt = window.__miaTxLastTime > 0 ? (now - window.__miaTxLastTime) : 0;
            window.__miaTxLastTime = now;

            const sock = window.__miaSocket;
            if (sock && sock.readyState === WebSocket.OPEN) {
                window.__miaFrameCounter++;
                sock.send(payload.buffer);
                if (window.__miaFrameCounter <= 5 || window.__miaFrameCounter % 100 === 0) {
                    console.log(`[MIA AUDIO TX] seq=${window.__miaFrameCounter} frames=${window.__miaFrameCounter} bytes=${payload.byteLength} dt=${dt}ms in_samples=${inputData.length} out_samples=${outputData.length} conn=${sock.__connId}`);
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
