import { useState, useEffect, useRef } from 'react';

export type AudioConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export const useInterviewAudio = (sessionId: string | null) => {
  const [status, setStatus] = useState<AudioConnectionStatus>('disconnected');
  const [error, setError] = useState<string | null>(null);
  const [micVolume, setMicVolume] = useState<number>(0);

  const socketRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const inputNodeRef = useRef<ScriptProcessorNode | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  // Keep track of scheduled playback times to prevent overlapping or gaps
  const nextPlayTimeRef = useRef<number>(0);

  // Helper to construct WebSocket URL
  const getWebSocketUrl = (sid: string): string => {
    const host = window.location.host;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    
    // Check if VITE_API_URL is configured
    const apiEnvUrl = import.meta.env.VITE_API_URL;
    if (apiEnvUrl) {
      const parsedUrl = new URL(apiEnvUrl);
      const wsProtocol = parsedUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsProtocol}//${parsedUrl.host}/api/ws/interview/${sid}`;
    }
    
    // In local development, the vite server proxies /api, but WebSockets need direct mapping
    // or standard proxy support. We can target port 8008 directly or use standard protocol conversion.
    if (host.includes('localhost') || host.includes('127.0.0.1')) {
      return `ws://localhost:8000/api/ws/interview/${sid}`;
    }
    
    return `${protocol}//${host}/api/ws/interview/${sid}`;
  };

  const startConnection = async () => {
    if (!sessionId) return;
    setStatus('connecting');
    setError(null);

    try {
      // 1. Establish WebSocket Connection
      const wsUrl = getWebSocketUrl(sessionId);
      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      socketRef.current = ws;

      ws.onopen = async () => {
        console.log('[AudioWS] Connection established');
        try {
          // 2. Request microphone stream
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              channelCount: 1,
              sampleRate: 16000,
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
          });
          mediaStreamRef.current = stream;

          // 3. Set up Web Audio API Context
          const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
          const audioCtx = new AudioContextClass();
          audioContextRef.current = audioCtx;
          nextPlayTimeRef.current = audioCtx.currentTime;

          // Source node from stream
          const source = audioCtx.createMediaStreamSource(stream);
          micSourceRef.current = source;

          // Capture input and downsample
          const bufferSize = 2048;
          const scriptProcessor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
          inputNodeRef.current = scriptProcessor;

          scriptProcessor.onaudioprocess = (e) => {
            const inputData = e.inputBuffer.getChannelData(0);
            
            // Measure mic volume level (RMS value)
            let sum = 0;
            for (let i = 0; i < inputData.length; i++) {
              sum += inputData[i] * inputData[i];
            }
            const rms = Math.sqrt(sum / inputData.length);
            setMicVolume(rms);

            // Downsample buffer to 16,000Hz PCM and convert to 16-bit Int16
            const pcmBuffer = downsampleBuffer(inputData, audioCtx.sampleRate, 16000);
            
            // Stream raw binary PCM bytes over WebSocket
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(pcmBuffer.buffer as ArrayBuffer);
            }
          };

          source.connect(scriptProcessor);
          scriptProcessor.connect(audioCtx.destination);

          setStatus('connected');
        } catch (err: any) {
          console.error('[AudioWS] Mic initialization failed:', err);
          setError(err.message || 'Failed to access microphone. Please check permissions.');
          setStatus('error');
          stopConnection();
        }
      };

      ws.onmessage = (event) => {
        if (!(event.data instanceof ArrayBuffer)) return;
        
        const audioCtx = audioContextRef.current;
        if (!audioCtx) return;

        // Convert received binary PCM (16kHz, 16-bit, Mono) to Float32 array
        const pcmData = new Int16Array(event.data);
        const floatData = new Float32Array(pcmData.length);
        for (let i = 0; i < pcmData.length; i++) {
          floatData[i] = pcmData[i] / 32768.0;
        }

        // Create buffer and load the Float32 array
        const audioBuffer = audioCtx.createBuffer(1, floatData.length, 16000);
        audioBuffer.copyToChannel(floatData, 0);

        // Schedule playback smoothly
        const sourceNode = audioCtx.createBufferSource();
        sourceNode.buffer = audioBuffer;
        sourceNode.connect(audioCtx.destination);

        const startTime = Math.max(nextPlayTimeRef.current, audioCtx.currentTime);
        sourceNode.start(startTime);
        
        const chunkDuration = floatData.length / 16000;
        nextPlayTimeRef.current = startTime + chunkDuration;
      };

      ws.onerror = (e) => {
        console.error('[AudioWS] WebSocket error:', e);
        setError('WebSocket connection error.');
        setStatus('error');
      };

      ws.onclose = () => {
        console.log('[AudioWS] Connection closed');
        setStatus((prev) => (prev === 'error' ? 'error' : 'disconnected'));
        cleanupAudio();
      };

    } catch (err: any) {
      console.error('[AudioWS] Setup error:', err);
      setError(err.message || 'Setup error');
      setStatus('error');
    }
  };

  const stopConnection = () => {
    if (socketRef.current) {
      socketRef.current.close();
    }
    cleanupAudio();
    setStatus('disconnected');
    setMicVolume(0);
  };

  const cleanupAudio = () => {
    if (inputNodeRef.current) {
      inputNodeRef.current.disconnect();
      inputNodeRef.current = null;
    }
    if (micSourceRef.current) {
      micSourceRef.current.disconnect();
      micSourceRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch((err) => console.warn('[AudioWS] Failed to close audio context:', err));
      audioContextRef.current = null;
    }
  };

  // Helper function to downsample audio from browser capture rate (e.g. 48kHz) to 16,000Hz PCM
  const downsampleBuffer = (
    buffer: Float32Array,
    inputRate: number,
    outputRate: number
  ): Int16Array => {
    if (outputRate === inputRate) {
      const output = new Int16Array(buffer.length);
      for (let i = 0; i < buffer.length; i++) {
        output[i] = Math.min(1, Math.max(-1, buffer[i])) * 0x7fff;
      }
      return output;
    }
    const sampleRateRatio = inputRate / outputRate;
    const newLength = Math.round(buffer.length / sampleRateRatio);
    const result = new Int16Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = Math.min(1, Math.max(-1, accum / (count || 1))) * 0x7fff;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  };

  useEffect(() => {
    return () => {
      cleanupAudio();
    };
  }, []);

  return {
    status,
    error,
    micVolume,
    startConnection,
    stopConnection,
  };
};
