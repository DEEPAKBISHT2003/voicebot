import React, { useState, useEffect, useRef } from 'react';
import { CheckCircle2, Clock, Loader2, Sparkles, AlertCircle, Radio, X } from 'lucide-react';
import type { InterviewReadinessState } from '../hooks/useCopilotAudio';

interface InterviewReadinessPanelProps {
  readiness: InterviewReadinessState;
  sessionId?: string | null;
  isServiceOff?: boolean;
  isCompletedSession?: boolean;
}

export const InterviewReadinessPanel: React.FC<InterviewReadinessPanelProps> = ({
  readiness,
  sessionId = null,
  isServiceOff = false,
  isCompletedSession = false,
}) => {
  const [secondsAgo, setSecondsAgo] = useState<number | null>(null);
  const [showNotification, setShowNotification] = useState<boolean>(false);
  const hasMountedRef = useRef<boolean>(false);

  // Play subtle chime on readiness confirmation
  const playSuccessChime = () => {
    try {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioContextClass) return;
      const ctx = new AudioContextClass();
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      const now = ctx.currentTime;
      // Tone 1: E5 (659.25 Hz)
      const osc1 = ctx.createOscillator();
      const gain1 = ctx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(659.25, now);
      gain1.gain.setValueAtTime(0.06, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
      osc1.connect(gain1);
      gain1.connect(ctx.destination);
      osc1.start(now);
      osc1.stop(now + 0.3);

      // Tone 2: A5 (880.00 Hz)
      const osc2 = ctx.createOscillator();
      const gain2 = ctx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(880.0, now + 0.12);
      gain2.gain.setValueAtTime(0.08, now + 0.12);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
      osc2.connect(gain2);
      gain2.connect(ctx.destination);
      osc2.start(now + 0.12);
      osc2.stop(now + 0.5);
    } catch (e) {
      console.debug('[ReadinessAudio] Notice: Audio chime bypassed:', e);
    }
  };

  // Readiness transition detector & explicit toast notification handler
  useEffect(() => {
    if (!sessionId) return;
    const sessionKey = `readiness_notified_${sessionId}`;
    const wasAlreadyNotified = sessionStorage.getItem(sessionKey) === 'true';

    // On initial mount: if session was already ready, mark as notified so page refresh never duplicates
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      if (readiness.interviewReady && readiness.readinessConfirmed) {
        sessionStorage.setItem(sessionKey, 'true');
        return;
      }
    }

    // Trigger notification the moment readiness_confirmed becomes true during live session
    if (readiness.interviewReady && readiness.readinessConfirmed && !wasAlreadyNotified) {
      sessionStorage.setItem(sessionKey, 'true');
      setShowNotification(true);
      playSuccessChime();

      // Auto-dismiss after 6.5 seconds (within 5-8 second specification)
      const timer = setTimeout(() => {
        setShowNotification(false);
      }, 6500);

      return () => clearTimeout(timer);
    }
  }, [readiness.interviewReady, readiness.readinessConfirmed, sessionId]);

  // Live timer tracking seconds since last caption was received
  useEffect(() => {
    if (!readiness.lastCaptionTime) {
      setSecondsAgo(null);
      return;
    }

    const updateTimer = () => {
      const elapsed = Math.max(0, Math.floor((Date.now() - (readiness.lastCaptionTime || Date.now())) / 1000));
      setSecondsAgo(elapsed);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [readiness.lastCaptionTime]);

  // If session is already completed or marked service off, hide the startup readiness banner
  if (isServiceOff || isCompletedSession) {
    return null;
  }

  const isReady = Boolean(readiness.interviewReady && readiness.readinessConfirmed);
  const isSilenceWarning = isReady && secondsAgo !== null && secondsAgo > 15;
  const isCaptionsFlowing = isReady || readiness.hasProvenTranscript || (readiness.captionCount >= 2 && readiness.uniqueSpeakersDetected >= 2);
  const hasFirstCaption = Boolean(readiness.firstCaptionReceived || readiness.captionCount >= 1 || readiness.hasProvenTranscript);

  return (
    <>
      {/* Explicit Floating Success Toast Notification (Auto-dismisses in 6.5s) */}
      {showNotification && (
        <div
          role="alert"
          aria-live="assertive"
          className="fixed top-5 right-5 z-50 max-w-md w-full bg-white border-2 border-emerald-500 rounded-2xl shadow-2xl p-4 flex items-start gap-3.5 animate-bounce-short transition-all"
        >
          <div className="h-10 w-10 rounded-xl bg-emerald-100 border border-emerald-300 flex items-center justify-center shrink-0">
            <CheckCircle2 className="h-6 w-6 text-emerald-600" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-black text-emerald-950 flex items-center gap-1.5">
                <span>✅ Interview Ready</span>
              </h4>
              <button
                onClick={() => setShowNotification(false)}
                className="text-gray-400 hover:text-gray-600 text-xs cursor-pointer p-0.5"
                title="Dismiss"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <p className="text-xs text-gray-700 font-medium mt-1">
              Transcript is active and receiving captions.
            </p>
            <p className="text-xs font-bold text-emerald-700 mt-1">
              You may begin the interview.
            </p>
          </div>
        </div>
      )}

      {/* Main Readiness Panel */}
      <div
        className={`rounded-xl border p-4 sm:p-5 transition-all shadow-sm ${
          isReady
            ? 'bg-gradient-to-r from-emerald-50/70 via-green-50/50 to-white border-green-200'
            : 'bg-white border-border-gray'
        }`}
      >
        {/* Top Header Row */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-border-gray/50 pb-3.5 mb-4">
          <div className="flex items-center gap-3">
            {isReady ? (
              <div className="h-9 w-9 rounded-lg bg-green-100 border border-green-300 flex items-center justify-center shrink-0 shadow-xs">
                <CheckCircle2 className="h-5 w-5 text-green-600" />
              </div>
            ) : (
              <div className="h-9 w-9 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <Loader2 className="h-5 w-5 text-primary animate-spin" />
              </div>
            )}
            <div>
              <div className="flex items-center gap-2">
                <h3
                  className={`text-sm font-bold tracking-tight ${
                    isReady ? 'text-green-900' : 'text-primary'
                  }`}
                >
                  {isReady ? 'Status: Interview Ready' : 'Preparing Interview'}
                </h3>
              </div>
              <p className="text-xs text-muted-gray">
                {isReady
                  ? 'All ingestion pipelines operational and transcript engine is fully activated.'
                  : 'Verifying live Teams meeting connection and caption stream activation...'}
              </p>
            </div>
          </div>

          {/* Live Caption Activity Badge */}
          {isReady && (
            <div className="flex items-center gap-2 self-start sm:self-auto">
              {isSilenceWarning ? (
                <div
                  className="flex items-center gap-2 px-3 py-1 bg-amber-50 border border-amber-300 rounded-full text-amber-800 text-xs font-semibold shadow-xs"
                  title="No new caption frames detected from meeting audio recently"
                >
                  <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                  <span>No captions detected for {secondsAgo}s</span>
                </div>
              ) : (
                <div
                  className="flex items-center gap-2 px-3 py-1 bg-green-100/90 border border-green-300 rounded-full text-green-800 text-xs font-bold shadow-xs"
                  title="Captions are actively streaming from Teams meeting in real time"
                >
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-green-600" />
                  </span>
                  <Radio className="h-3.5 w-3.5 text-green-600" />
                  <span>Transcript Active</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Readiness Checkpoints Grid */}
        <div className={`grid grid-cols-1 sm:grid-cols-2 ${isReady ? 'lg:grid-cols-5' : 'lg:grid-cols-4'} gap-2.5 sm:gap-3`}>
          {/* 1. Joined Meeting */}
          <div
            className={`flex items-center gap-2.5 p-3 rounded-lg border text-xs font-semibold transition-all ${
              readiness.botJoined
                ? 'bg-green-50/80 border-green-200 text-green-800'
                : 'bg-secondary/60 border-border-gray text-muted-gray'
            }`}
          >
            {readiness.botJoined ? (
              <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            ) : (
              <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />
            )}
            <span>{readiness.botJoined ? '✓ Joined Meeting' : 'Joining Meeting...'}</span>
          </div>

          {/* 2. Caption Stream Connected */}
          <div
            className={`flex items-center gap-2.5 p-3 rounded-lg border text-xs font-semibold transition-all ${
              readiness.captionSocketConnected
                ? 'bg-green-50/80 border-green-200 text-green-800'
                : 'bg-secondary/60 border-border-gray text-muted-gray'
            }`}
          >
            {readiness.captionSocketConnected ? (
              <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            ) : (
              <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />
            )}
            <span>
              {readiness.captionSocketConnected
                ? '✓ Transcript Stream Connected'
                : 'Connecting Transcript Stream...'}
            </span>
          </div>

          {/* 3. Transcript Processor Ready */}
          <div
            className={`flex items-center gap-2.5 p-3 rounded-lg border text-xs font-semibold transition-all ${
              readiness.transcriptProcessorInitialized
                ? 'bg-green-50/80 border-green-200 text-green-800'
                : 'bg-secondary/60 border-border-gray text-muted-gray'
            }`}
          >
            {readiness.transcriptProcessorInitialized ? (
              <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            ) : (
              <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />
            )}
            <span>
              {readiness.transcriptProcessorInitialized
                ? '✓ Transcript Processor Ready'
                : 'Initializing Processor...'}
            </span>
          </div>

          {/* 4. Caption Activity / Captions Flowing */}
          <div
            className={`flex items-center gap-2.5 p-3 rounded-lg border text-xs font-semibold transition-all ${
              isCaptionsFlowing
                ? 'bg-green-50/80 border-green-200 text-green-800'
                : hasFirstCaption
                ? 'bg-blue-50/80 border-blue-200 text-blue-900 font-bold'
                : 'bg-amber-50/80 border-amber-200 text-amber-900 font-bold'
            }`}
          >
            {isCaptionsFlowing ? (
              <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
            ) : hasFirstCaption ? (
              <Loader2 className="h-4 w-4 text-blue-600 animate-spin shrink-0" />
            ) : (
              <Clock className="h-4 w-4 text-amber-600 shrink-0 animate-pulse" />
            )}
            <span>
              {isCaptionsFlowing
                ? '✓ Captions Flowing'
                : hasFirstCaption
                ? `⏳ Captions Detected (${readiness.captionCount}/2)`
                : '⏳ Waiting For Transcript Activity'}
            </span>
          </div>

          {/* 5. Interview Ready (Visible when readiness is confirmed) */}
          {isReady && (
            <div className="flex items-center gap-2.5 p-3 rounded-lg border text-xs font-bold bg-green-100/90 border-green-300 text-green-900 shadow-xs animate-fade-in">
              <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
              <span>✓ Interview Ready</span>
            </div>
          )}
        </div>

        {/* Action / Guidance Message Footer */}
        {isReady ? (
          <div className="mt-4 p-3 bg-white/90 border border-green-200 rounded-lg flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 shadow-2xs">
            <div className="flex items-center gap-2 text-green-900 font-bold text-xs">
              <Sparkles className="h-4 w-4 text-green-600 shrink-0" />
              <span>"You may begin the interview."</span>
            </div>
            <span className="text-[11px] text-muted-gray">
              Captions and candidate evaluations are actively recording.
            </span>
          </div>
        ) : (
          <div className="mt-3.5 p-2.5 bg-secondary/50 rounded-lg text-xs text-muted-gray flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-primary/70 shrink-0" />
            <span>
              The interview system activates automatically once speech is detected. You do not need to speak random test phrases.
            </span>
          </div>
        )}
      </div>
    </>
  );
};
