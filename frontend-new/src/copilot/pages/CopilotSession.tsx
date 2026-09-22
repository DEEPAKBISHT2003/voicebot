import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Mic,
  MessageSquare,
  Power,
  ArrowLeft,
  User,
  Activity,
  BookOpen,
  Briefcase,
  CheckCircle,
  Compass,
  FileText,
  ChevronDown,
  ChevronUp,
  Volume2,
  VolumeX,
  Pin
} from 'lucide-react';
import { useCopilotAudio, getTranscriptEntryKey, type CopilotTranscriptEntry } from '../hooks/useCopilotAudio';
import { stopCopilot, serviceOffCopilot, getCopilotStatus, finalizeCopilotReport } from '../../api/copilot';
import type { CopilotFinalReport } from '../../types/copilot-report';

export const getSpeakerDisplayName = (
  rawSpeaker?: string,
  allEntries?: CopilotTranscriptEntry[]
): string => {
  const genericRoles = new Set(['candidate', 'interviewer', 'user', 'assistant', 'system', 'unknown']);
  const clean = (rawSpeaker || '').trim();

  if (clean && !genericRoles.has(clean.toLowerCase())) {
    return clean;
  }

  // Look for any real human speaker name present in the session transcript
  if (allEntries && allEntries.length > 0) {
    const realEntry = allEntries.find((e) => {
      const spk = (e.speaker_name || e.speaker || '').trim();
      return spk && !genericRoles.has(spk.toLowerCase());
    });
    if (realEntry) {
      return (realEntry.speaker_name || realEntry.speaker).trim();
    }
  }

  return 'Participant';
};

export const CopilotSession: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const {
    status,
    error,
    transcript,
    intelligence,
    questions,
    previousAnswer,
    togglePinQuestion,
    startConnection,
    stopConnection,
    updateState
  } = useCopilotAudio(id || null);

  const [uiMode, setUiMode] = useState<'live' | 'report'>('live');
  const [finalReport, setFinalReport] = useState<CopilotFinalReport | null>(null);
  const [finalizationError, setFinalizationError] = useState<string | null>(null);

  // Simulation mode check & audio control states
  const searchParams = new URLSearchParams(window.location.search);
  const isSimulation = searchParams.get('simulate') === 'true';

  const [isSimulationFinished, setIsSimulationFinished] = useState<boolean>(false);
  const [isGeneratingReport, setIsGeneratingReport] = useState<boolean>(false);
  const [isServiceOff, setIsServiceOff] = useState<boolean>(false);
  const [isServiceOffLoading, setIsServiceOffLoading] = useState<boolean>(false);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [volume, setVolume] = useState<number>(1.0);

  const isMutedRef = React.useRef(isMuted);
  const volumeRef = React.useRef(volume);

  useEffect(() => {
    isMutedRef.current = isMuted;
    volumeRef.current = volume;
  }, [isMuted, volume]);

  const [isCompletedSession, setIsCompletedSession] = useState<boolean>(false);

  // Initial session inspection: detect completed status from PostgreSQL before connecting WebSocket
  useEffect(() => {
    if (!id) return;
    let isMounted = true;

    const initSession = async () => {
      try {
        const res = await getCopilotStatus(id);
        if (!isMounted || !res) return;

        updateState(res);
        if (res.final_report && res.final_report.is_finalized === true) {
          setFinalReport(res.final_report);
        }
        const active = (res as any).is_active;
        const serviceOff = (res as any).service_off || res.status === 'Service Off';
        const hasReport = Boolean(res.final_report && res.final_report.is_finalized === true);

        if (hasReport || serviceOff || active === false) {
          setIsCompletedSession(true);
          setIsSimulationFinished(true);
          if (serviceOff) setIsServiceOff(true);
          stopConnection();
          if (hasReport) {
            setUiMode('report');
          }
        } else {
          setIsCompletedSession(false);
          startConnection();
        }
      } catch (err) {
        console.error('Failed to initialize session status:', err);
        if (isMounted) startConnection();
      }
    };

    initSession();

    return () => {
      isMounted = false;
      stopConnection();
    };
  }, [id]);

  // Secondary connection to trigger audio simulation only if simulate=true and not completed
  useEffect(() => {
    if (!id || isCompletedSession) return;
    const simulate = searchParams.get('simulate');
    if (simulate !== 'true') return;

    console.log('[Simulation] Initiating background simulation trigger connection...');

    // Use copilot service simulation WebSocket via Nginx proxy
    const host = window.location.host;
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${host}/api/ws/copilot/${id}/simulate`;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    let audioCtx: AudioContext | null = null;
    let gainNode: GainNode | null = null;
    let nextPlayTime = 0;

    ws.onopen = () => {
      console.log('[SimulationWS] Simulation trigger WebSocket opened.');
      try {
        const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
        audioCtx = new AudioContextClass();
        gainNode = audioCtx.createGain();
        gainNode.gain.value = isMutedRef.current ? 0 : volumeRef.current;
        gainNode.connect(audioCtx.destination);
        nextPlayTime = audioCtx.currentTime;
      } catch (err) {
        console.warn('[SimulationWS] Could not initialize Web Audio API Context:', err);
      }
    };

    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'simulation_complete') {
            console.log('[SimulationWS] Audio simulation complete frame received.');
            setIsSimulationFinished(true);
          }
        } catch (e) {
          // ignore string parse errors
        }
        return;
      }

      if (event.data instanceof ArrayBuffer && audioCtx && gainNode) {
        try {
          if (audioCtx.state === 'suspended') {
            audioCtx.resume();
          }
          const pcmData = new Int16Array(event.data);
          const floatData = new Float32Array(pcmData.length);
          for (let i = 0; i < pcmData.length; i++) {
            floatData[i] = pcmData[i] / 32768.0;
          }

          const audioBuffer = audioCtx.createBuffer(1, floatData.length, 16000);
          audioBuffer.copyToChannel(floatData, 0);

          const sourceNode = audioCtx.createBufferSource();
          sourceNode.buffer = audioBuffer;

          gainNode.gain.value = isMutedRef.current ? 0 : volumeRef.current;
          sourceNode.connect(gainNode);

          const startTime = Math.max(nextPlayTime, audioCtx.currentTime);
          sourceNode.start(startTime);

          const chunkDuration = floatData.length / 16000;
          nextPlayTime = startTime + chunkDuration;
        } catch (err) {
          console.error('[SimulationWS] Audio playback error:', err);
        }
      }
    };

    ws.onclose = () => {
      console.log('[SimulationWS] Simulation trigger WebSocket closed.');
      if (audioCtx) {
        audioCtx.close().catch(() => { });
      }
    };
    return () => {
      ws.close();
      if (audioCtx) {
        audioCtx.close().catch(() => { });
      }
    };
  }, [id]);

  // Accordion open/close toggles for Live Interview view (Live Transcript expanded by default)
  const [isTranscriptExpanded, setIsTranscriptExpanded] = useState<boolean>(true);
  const [isJdCoverageExpanded, setIsJdCoverageExpanded] = useState<boolean>(false);
  const [isResumeCoverageExpanded, setIsResumeCoverageExpanded] = useState<boolean>(false);

  // Auto-scroll ref for Live Transcript Log container
  const transcriptContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (transcriptContainerRef.current && isTranscriptExpanded) {
      transcriptContainerRef.current.scrollTo({
        top: transcriptContainerRef.current.scrollHeight,
        behavior: 'smooth'
      });
    }
  }, [transcript, isTranscriptExpanded]);


  // Poll backend status to auto-detect session closure for active sessions
  useEffect(() => {
    if (!id || isServiceOff || isCompletedSession) return;

    const checkStatus = async () => {
      try {
        const res = await getCopilotStatus(id);
        if (res) {
          updateState(res);
          if (res.final_report && res.final_report.is_finalized === true) {
            setFinalReport(res.final_report);
          }
          const active = (res as any).is_active;
          const serviceOff = (res as any).service_off || res.status === 'Service Off';
          const hasReport = Boolean(res.final_report && res.final_report.is_finalized === true);

          if (serviceOff) {
            setIsServiceOff(true);
            setIsCompletedSession(true);
            stopConnection();
          } else if (hasReport || active === false) {
            setIsCompletedSession(true);
            setIsSimulationFinished(true);
            stopConnection();
            if (hasReport) {
              setUiMode('report');
            }
          }
        }
      } catch (err) {
        console.error('Failed to query session status:', err);
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => clearInterval(interval);
  }, [id, isServiceOff, isCompletedSession]);

  const handleServiceOff = async () => {
    if (!id || isServiceOffLoading || isServiceOff) return;
    setIsServiceOffLoading(true);
    try {
      await serviceOffCopilot(id);
      stopConnection();
      setIsServiceOff(true);
    } catch (e) {
      console.error('Failed to execute Service Off on backend:', e);
    } finally {
      setIsServiceOffLoading(false);
    }
  };

  const handleEndSession = async () => {
    stopConnection();
    if (id) {
      try {
        await stopCopilot(id);
      } catch (e) {
        console.error('Failed to stop copilot session on backend:', e);
      }
    }
    navigate('/');
  };

  return (
    <div className="space-y-6">
      {/* Top Header Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 bg-secondary p-4 rounded-xl border border-border-gray shadow-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={handleEndSession}
            className="flex items-center justify-center p-2 rounded-lg hover:bg-border-gray/30 text-muted-gray hover:text-primary transition-all"
            title="Exit Room"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div>
            <h2 className="text-base font-bold text-primary flex items-center gap-2">
              {uiMode === 'report' ? (
                <>
                  <FileText className="h-5 w-5 text-primary" />
                  Final Evaluation Report
                </>
              ) : (
                <>
                  <Mic className="h-5 w-5 text-primary" />
                  Appz Moderator Console
                </>
              )}
            </h2>
            <p className="text-xs text-muted-gray select-all">Session ID: {id}</p>
          </div>
        </div>

        <div className="flex items-center gap-3 self-end sm:self-auto">
          {uiMode === 'report' ? (
            <button
              onClick={() => setUiMode('live')}
              className="flex items-center gap-2 px-4 py-2 bg-white hover:bg-secondary text-primary text-xs font-bold rounded-lg border border-border-gray shadow-sm transition-all cursor-pointer"
              title="Switch to Live Console & Transcript view"
            >
              <Mic className="h-4 w-4" />
              Console & Logs View
            </button>
          ) : (
            <>
              {/* View Final Results Button */}
              <button
                onClick={async () => {
                  if (!id) return;
                  if (finalReport && finalReport.is_finalized === true) {
                    setUiMode('report');
                    return;
                  }
                  setIsGeneratingReport(true);
                  setFinalizationError(null);
                  try {
                    const finalRes = await finalizeCopilotReport(id);
                    if (finalRes && finalRes.is_finalized === true) {
                      setFinalReport(finalRes);
                      updateState(finalRes);
                      setUiMode('report');
                    } else {
                      // Failed or unconfirmed finalization: do not enter report mode, do not overwrite existing valid report
                      const errMsg = finalRes?.error || 'Final evaluation synthesis failed. Session remains eligible for retry.';
                      console.warn('[Finalize] Report finalization failed or unconfirmed:', errMsg);
                      setFinalizationError(errMsg);
                    }
                  } catch (err: any) {
                    console.error('Failed to compile final report:', err);
                    setFinalizationError(err?.message || 'Failed to compile final report. Please retry.');
                  } finally {
                    setIsGeneratingReport(false);
                  }
                }}
                disabled={isGeneratingReport}
                className={`flex items-center gap-2 px-4 py-2 text-white text-xs font-bold rounded-lg shadow-md transition-all cursor-pointer border ${
                  isSimulationFinished
                    ? 'bg-green-600 hover:bg-green-700 border-green-700 animate-bounce'
                    : 'bg-primary hover:bg-primary/90 border-primary'
                }`}
              >
                {isGeneratingReport ? (
                  <>
                    <div className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-white" />
                    Compiling Report...
                  </>
                ) : (
                  <>
                    <FileText className="h-4 w-4" />
                    View Final Results
                  </>
                )}
              </button>

              {/* Status Indicator */}
              {isServiceOff ? (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-red-50 rounded-lg border border-red-200 shadow-sm">
                  <span className="h-2.5 w-2.5 rounded-full bg-red-600" />
                  <span className="text-xs font-black uppercase text-red-700 tracking-wider">Service Disconnected</span>
                </div>
              ) : isCompletedSession ? (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-green-50 rounded-lg border border-green-200 shadow-sm">
                  <span className="h-2.5 w-2.5 rounded-full bg-green-600" />
                  <span className="text-xs font-bold uppercase text-green-700 tracking-wider">COMPLETED</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-white rounded-lg border border-border-gray">
                  <span className={`h-2.5 w-2.5 rounded-full ${status === 'connected' ? 'bg-green-500 animate-pulse' :
                      status === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-muted-gray'
                    }`} />
                  <span className="text-xs font-bold capitalize text-primary">{status === 'disconnected' ? 'On Hold' : status}</span>
                </div>
              )}

              {!isCompletedSession && !isServiceOff && (
                <>
                  {status === 'connected' ? (
                    <button
                      onClick={stopConnection}
                      className="flex items-center gap-2 px-4 py-2 bg-red-50 hover:bg-red-100 text-red-600 text-xs font-bold rounded-lg border border-red-200 transition-colors"
                    >
                      <Power className="h-3.5 w-3.5" />
                      Disconnect
                    </button>
                  ) : (
                    <button
                      onClick={startConnection}
                      disabled={status === 'connecting'}
                      className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary/95 text-white text-xs font-bold rounded-lg disabled:opacity-50 transition-colors shadow-sm"
                    >
                      <Mic className="h-3.5 w-3.5" />
                      {status === 'connecting' ? 'Connecting...' : 'Connect Copilot'}
                    </button>
                  )}
                </>
              )}

              {/* Dedicated SERVICE OFF / Disconnect Button */}
              <button
                onClick={handleServiceOff}
                disabled={isServiceOff || isServiceOffLoading || isCompletedSession}
                className={`flex items-center gap-2 px-4 py-2 text-xs font-bold rounded-lg shadow-sm transition-all border ${
                  isServiceOff || isCompletedSession
                    ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed opacity-90'
                    : 'bg-red-600 hover:bg-red-700 text-white border-red-700 active:scale-95 disabled:opacity-50'
                }`}
                title={isServiceOff ? "Session is permanently Service Off" : isCompletedSession ? "Session is completed" : "Instruct Teams bot to leave and permanently shut down this session"}
              >
                <Power className="h-3.5 w-3.5" />
                {isServiceOff ? 'Service Disconnected' : isServiceOffLoading ? 'Disconnecting...' : 'Disconnect'}
              </button>
            </>
          )}
        </div>
      </div>

      {(error || finalizationError) && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-600 rounded-lg text-xs font-semibold flex items-center justify-between">
          <span>Error: {finalizationError || error}</span>
          {finalizationError && (
            <button
              onClick={() => setFinalizationError(null)}
              className="text-red-600 hover:text-red-800 text-xs font-bold underline ml-2 cursor-pointer"
            >
              Dismiss
            </button>
          )}
        </div>
      )}

      {/* Main Grid Workspace */}
      {uiMode === 'live' ? (
        <div className="space-y-6 animate-fade-in">
          {/* Simulation Audio Control Bar */}
          {isSimulation && (
            <div className="bg-primary/5 border border-primary/20 rounded-xl p-3 flex flex-col sm:flex-row items-center justify-between gap-3 shadow-sm">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-primary text-white rounded-lg">
                  <Volume2 className="h-4 w-4 animate-pulse" />
                </div>
                <div>
                  <span className="text-xs font-bold text-primary block">Simulation Audio Live Stream</span>
                  <span className="text-[10px] text-muted-gray">
                    {isSimulationFinished ? 'Recording Finished. Click "View Final Results" to compile dossier.' : 'Playing test WAV audio through browser speakers in sync with suggestions.'}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-4 self-end sm:self-auto">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-gray font-bold uppercase">Volume:</span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={volume}
                    onChange={(e) => setVolume(parseFloat(e.target.value))}
                    className="w-24 accent-primary cursor-pointer"
                  />
                </div>

                <button
                  onClick={() => setIsMuted(!isMuted)}
                  className={`p-2 rounded-lg text-xs font-bold border transition-all flex items-center gap-1.5 ${isMuted
                      ? 'bg-red-50 text-red-600 border-red-200'
                      : 'bg-white text-primary border-border-gray hover:bg-secondary'
                    }`}
                >
                  {isMuted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                  {isMuted ? 'Muted' : 'Mute'}
                </button>
              </div>
            </div>
          )}

          {/* HUD Cards Grid */}
          {/* <div className="grid grid-cols-1 md:grid-cols-3 gap-4"> */}
          {/* Current Topic Card */}
          {/* <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-between">
              <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">Current Discussion Topic</span>
              <div className="flex items-center gap-2 text-sm font-bold text-primary py-1">
                <Activity className="h-4 w-4 text-green-500 shrink-0" />
                {intelligence.current_topic || 'No topic detected yet'}
              </div>
            </div> */}

          {/* Interview Decision Card */}
          {/* {(() => {
              const statusObj = getLatestAnswerStatus();
              return (
                <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-between">
                  <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">Interview Decision</span>
                  <div className="py-1">
                    <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs border font-bold ${statusObj.color}`}>
                      {statusObj.label}
                    </span>
                  </div>
                </div>
              );
            })()} */}

          {/* Recommended Next Topic Card */}
          {/* <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-between">
              <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1 font-semibold">Recommended Next Topic</span>
              <div className="flex items-center gap-2 text-xs font-bold text-primary bg-primary/5 p-2 rounded-lg border border-primary/10 py-1">
                <Compass className="h-4 w-4 text-primary shrink-0 animate-spin-slow" />
                {assistance.recommended_next_topic || 'Determining next topic...'}
              </div>
            </div>
          </div> */}

          {/* Phase 2W: Previous Answer Accuracy Live Indicator */}
          <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm space-y-3">
            <div className="flex items-center justify-between border-b border-border-gray/50 pb-2">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-primary shrink-0" />
                <span className="text-xs font-bold text-primary uppercase tracking-wider">
                  Previous Answer Accuracy
                </span>
              </div>
              {!previousAnswer && (
                <span className="text-xs text-muted-gray italic">
                  Waiting for completed answer...
                </span>
              )}
            </div>

            {previousAnswer && (
              <div className="space-y-2 text-sm">
                {previousAnswer.question && (
                  <div className="flex items-start gap-2">
                    <span className="font-bold text-primary uppercase text-xs tracking-wider shrink-0 mt-0.5">q:</span>
                    <p className="text-primary italic">"{previousAnswer.question}"</p>
                  </div>
                )}

                {previousAnswer.answer && (
                  <div className="flex items-start gap-2">
                    <span className="font-bold text-muted-gray uppercase text-xs tracking-wider shrink-0 mt-0.5">a:</span>
                    <p className="text-muted-gray">"{previousAnswer.answer}"</p>
                  </div>
                )}

                <div className="flex justify-end pt-1">
                  <div className="flex items-center gap-1.5 font-bold">
                    <span className="text-xs uppercase text-muted-gray tracking-wider">score:</span>
                    <span className="text-base font-black text-primary px-2.5 py-0.5 rounded bg-white border border-border-gray shadow-xs">
                      {previousAnswer.score}%
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Pinned Questions Section (if any question is pinned) */}
          {(() => {
            const pinnedList = questions.filter((q) => q.isPinned);
            if (pinnedList.length === 0) return null;
            return (
              <div className="bg-amber-50/70 rounded-xl p-4 border border-amber-200 shadow-sm space-y-3">
                <div className="flex items-center justify-between border-b border-amber-200/80 pb-2">
                  <h4 className="font-bold text-amber-900 flex items-center gap-2 text-xs uppercase tracking-wider">
                    <Pin className="h-4 w-4 text-amber-700 fill-amber-600" />
                    Pinned Questions ({pinnedList.length})
                  </h4>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {pinnedList.map((q) => {
                    const badgeColor =
                      q.type === 'Follow-up'
                        ? 'bg-purple-50 text-purple-700 border-purple-200'
                        : q.type === 'Verification'
                          ? 'bg-amber-50 text-amber-700 border-amber-200'
                          : 'bg-blue-50 text-blue-700 border-blue-200';
                    return (
                      <div
                        key={q.id}
                        className="bg-white rounded-lg p-3 border border-amber-300 shadow-sm text-xs text-primary flex flex-col justify-between gap-2.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className={`px-2 py-0.5 rounded text-[9px] font-bold border ${badgeColor}`}>
                            {q.type}
                          </span>
                          <button
                            onClick={() => togglePinQuestion(q.id)}
                            title="Unpin Question"
                            className="p-1 text-amber-600 hover:text-amber-800 rounded hover:bg-amber-100/60 transition-colors"
                          >
                            <Pin className="h-3.5 w-3.5 fill-amber-500" />
                          </button>
                        </div>
                        <p className="leading-relaxed font-medium">{q.text}</p>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* Primary Suggestions Area: 3 Horizontal Containers (Follow-up | Verification | Scenario) */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Column 1: Follow-up Questions */}
            {(() => {
              const list = questions.filter((q) => q.type === 'Follow-up');
              return (
                <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col gap-3 min-h-[300px]">
                  <h3 className="font-bold text-primary flex items-center justify-between text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                    <span className="flex items-center gap-1.5 text-purple-700 font-semibold">
                      <User className="h-4 w-4" />
                      Follow-up Questions
                    </span>
                    <span className="bg-purple-100 text-purple-800 px-2 py-0.5 rounded-full text-[10px] font-bold">
                      {list.length}
                    </span>
                  </h3>
                  <div className="flex-1 overflow-y-auto space-y-2.5 max-h-[350px] pr-1">
                    {status !== 'connected' ? (
                      <p className="text-xs text-muted-gray text-center py-8">Offline</p>
                    ) : list.length === 0 ? (
                      <div className="h-full flex items-center justify-center text-center text-muted-gray py-8">
                        <p className="text-xs italic">No follow-up questions yet.</p>
                      </div>
                    ) : (
                      list.map((q) => (
                        <div
                          key={q.id}
                          className={`bg-white rounded-lg p-3 border text-xs text-primary shadow-sm leading-relaxed flex flex-col gap-2 transition-all ${q.isPinned ? 'border-amber-400 ring-1 ring-amber-300' : 'border-border-gray/80 hover:border-purple-300'
                            }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold border bg-purple-50 text-purple-700 border-purple-200">
                              Follow-up
                            </span>
                            <button
                              onClick={() => togglePinQuestion(q.id)}
                              title={q.isPinned ? 'Unpin question' : 'Pin question'}
                              className={`p-1 rounded transition-colors ${q.isPinned
                                  ? 'text-amber-600 hover:text-amber-800 bg-amber-50'
                                  : 'text-gray-400 hover:text-amber-600 hover:bg-gray-100'
                                }`}
                            >
                              <Pin className={`h-3.5 w-3.5 ${q.isPinned ? 'fill-amber-500' : ''}`} />
                            </button>
                          </div>
                          <span>{q.text}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })()}

            {/* Column 2: Verification Questions */}
            {(() => {
              const list = questions.filter((q) => q.type === 'Verification');
              return (
                <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col gap-3 min-h-[300px]">
                  <h3 className="font-bold text-primary flex items-center justify-between text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                    <span className="flex items-center gap-1.5 text-amber-700 font-semibold">
                      <CheckCircle className="h-4 w-4" />
                      Verification Questions
                    </span>
                    <span className="bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full text-[10px] font-bold">
                      {list.length}
                    </span>
                  </h3>
                  <div className="flex-1 overflow-y-auto space-y-2.5 max-h-[350px] pr-1">
                    {status !== 'connected' ? (
                      <p className="text-xs text-muted-gray text-center py-8">Offline</p>
                    ) : list.length === 0 ? (
                      <div className="h-full flex items-center justify-center text-center text-muted-gray py-8">
                        <p className="text-xs italic">No verification questions yet.</p>
                      </div>
                    ) : (
                      list.map((q) => (
                        <div
                          key={q.id}
                          className={`bg-white rounded-lg p-3 border text-xs text-primary shadow-sm leading-relaxed flex flex-col gap-2 transition-all ${q.isPinned ? 'border-amber-400 ring-1 ring-amber-300' : 'border-border-gray/80 hover:border-amber-300'
                            }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold border bg-amber-50 text-amber-700 border-amber-200">
                              Verification
                            </span>
                            <button
                              onClick={() => togglePinQuestion(q.id)}
                              title={q.isPinned ? 'Unpin question' : 'Pin question'}
                              className={`p-1 rounded transition-colors ${q.isPinned
                                  ? 'text-amber-600 hover:text-amber-800 bg-amber-50'
                                  : 'text-gray-400 hover:text-amber-600 hover:bg-gray-100'
                                }`}
                            >
                              <Pin className={`h-3.5 w-3.5 ${q.isPinned ? 'fill-amber-500' : ''}`} />
                            </button>
                          </div>
                          <span>{q.text}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })()}

            {/* Column 3: Scenario Questions */}
            {(() => {
              const list = questions.filter((q) => q.type === 'Scenario');
              return (
                <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col gap-3 min-h-[300px]">
                  <h3 className="font-bold text-primary flex items-center justify-between text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                    <span className="flex items-center gap-1.5 text-blue-700 font-semibold">
                      <BookOpen className="h-4 w-4" />
                      Scenario Questions
                    </span>
                    <span className="bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full text-[10px] font-bold">
                      {list.length}
                    </span>
                  </h3>
                  <div className="flex-1 overflow-y-auto space-y-2.5 max-h-[350px] pr-1">
                    {status !== 'connected' ? (
                      <p className="text-xs text-muted-gray text-center py-8">Offline</p>
                    ) : list.length === 0 ? (
                      <div className="h-full flex items-center justify-center text-center text-muted-gray py-8">
                        <p className="text-xs italic">No scenario questions yet.</p>
                      </div>
                    ) : (
                      list.map((q) => (
                        <div
                          key={q.id}
                          className={`bg-white rounded-lg p-3 border text-xs text-primary shadow-sm leading-relaxed flex flex-col gap-2 transition-all ${q.isPinned ? 'border-amber-400 ring-1 ring-amber-300' : 'border-border-gray/80 hover:border-blue-300'
                            }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold border bg-blue-50 text-blue-700 border-blue-200">
                              Scenario
                            </span>
                            <button
                              onClick={() => togglePinQuestion(q.id)}
                              title={q.isPinned ? 'Unpin question' : 'Pin question'}
                              className={`p-1 rounded transition-colors ${q.isPinned
                                  ? 'text-amber-600 hover:text-amber-800 bg-amber-50'
                                  : 'text-gray-400 hover:text-amber-600 hover:bg-gray-100'
                                }`}
                            >
                              <Pin className={`h-3.5 w-3.5 ${q.isPinned ? 'fill-amber-500' : ''}`} />
                            </button>
                          </div>
                          <span>{q.text}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Collapsible Accordion Sections */}
          <div className="space-y-4">

            {/* Accordion 1: LIVE TRANSCRIPT */}
            <div className="bg-secondary rounded-xl border border-border-gray shadow-sm overflow-hidden">
              <button
                onClick={() => setIsTranscriptExpanded(!isTranscriptExpanded)}
                className="w-full flex items-center justify-between p-4 bg-secondary/80 hover:bg-secondary transition-colors"
              >
                <div className="flex items-center gap-2.5">
                  <MessageSquare className="h-4 w-4 text-primary" />
                  <span className="font-bold text-primary text-sm tracking-wide">
                    LIVE TRANSCRIPT
                  </span>
                  <span className="text-[11px] font-semibold bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                    {transcript.length} {transcript.length === 1 ? 'turn' : 'turns'}
                  </span>
                </div>
                {isTranscriptExpanded ? <ChevronUp className="h-4 w-4 text-primary" /> : <ChevronDown className="h-4 w-4 text-primary" />}
              </button>

              {isTranscriptExpanded && (
                <div ref={transcriptContainerRef} className="border-t border-border-gray p-4 max-h-[420px] overflow-y-auto space-y-3 bg-white">
                  {transcript.length === 0 ? (
                    <div className="text-center py-8 space-y-1.5">
                      <p className="text-xs text-muted-gray font-medium">Listening for speech from Microsoft Teams Native Captions...</p>
                      <p className="text-[10px] text-muted-gray/70">Finalized conversational turns will appear here in real time.</p>
                    </div>
                  ) : (
                    transcript.map((msg) => {
                      const isSystem = msg.speaker === 'System';

                      if (isSystem) {
                        return (
                          <div key={getTranscriptEntryKey(msg)} className="flex justify-center my-1">
                            <span className="text-[10px] font-medium bg-border-gray/50 text-muted-gray px-3 py-1 rounded-full uppercase tracking-wider">
                              {msg.text}
                            </span>
                          </div>
                        );
                      }

                      const entryKey = getTranscriptEntryKey(msg);
                      const displayName = getSpeakerDisplayName(msg.speaker || msg.speaker_name, transcript);

                      return (
                        <div
                          key={entryKey}
                          className="p-3.5 rounded-xl border border-border-gray/70 bg-secondary/30 hover:bg-secondary/60 transition-colors space-y-1"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-bold text-xs text-primary tracking-tight">
                              {displayName}
                            </span>
                            {msg.timestamp && (
                              <span className="text-[10px] text-muted-gray font-mono">
                                {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-primary leading-relaxed whitespace-pre-wrap">
                            {msg.text}
                          </p>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            {/* Accordion 2: JD Skills Coverage */}
            <div className="bg-secondary rounded-xl border border-border-gray shadow-sm overflow-hidden">
              <button
                onClick={() => setIsJdCoverageExpanded(!isJdCoverageExpanded)}
                className="w-full flex items-center justify-between p-4 bg-secondary/80 hover:bg-secondary transition-colors"
              >
                <span className="font-bold text-primary flex items-center gap-2 text-sm">
                  <BookOpen className="h-4 w-4 text-primary" />
                  JD Skill Requirements & Coverage
                </span>
                {isJdCoverageExpanded ? <ChevronUp className="h-4 w-4 text-primary" /> : <ChevronDown className="h-4 w-4 text-primary" />}
              </button>

              {isJdCoverageExpanded && (
                <div className="border-t border-border-gray p-5 space-y-4 bg-white">
                  {/* JD Coverage Progress bar */}
                  <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-center max-w-md">
                    <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">JD Coverage Progress</span>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-3 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                        <div
                          className="h-full bg-primary rounded-full transition-all duration-500"
                          style={{ width: `${intelligence.interview_progress.percentage || 0}%` }}
                        />
                      </div>
                      <span className="text-xs font-black text-primary">{intelligence.interview_progress.percentage || 0}%</span>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <span className="text-[10px] font-bold text-green-600 uppercase block mb-1.5 tracking-wider">Discussed / Covered</span>
                      <div className="flex flex-wrap gap-1.5">
                        {intelligence.covered_skills.length === 0 ? (
                          <span className="text-[10px] text-muted-gray italic">No skills covered yet</span>
                        ) : (
                          intelligence.covered_skills.map((skill, idx) => (
                            <span key={idx} className="flex items-center gap-1 px-2.5 py-1 bg-green-50 text-green-700 border border-green-200 rounded-lg text-[10px] font-bold shadow-sm">
                              <CheckCircle className="h-3 w-3 text-green-600 shrink-0" />
                              {skill}
                            </span>
                          ))
                        )}
                      </div>
                    </div>

                    <div>
                      <span className="text-[10px] font-bold text-amber-600 uppercase block mb-1.5 tracking-wider">Remaining / Uncovered</span>
                      <div className="flex flex-wrap gap-1.5">
                        {intelligence.remaining_skills.length === 0 ? (
                          <span className="text-[10px] text-muted-gray italic">All skills discussed!</span>
                        ) : (
                          intelligence.remaining_skills.map((skill, idx) => (
                            <span key={idx} className="px-2.5 py-1 bg-white text-muted-gray border border-border-gray rounded-lg text-[10px] font-bold shadow-sm">
                              {skill}
                            </span>
                          ))
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Accordion 3: Resume Experience Coverage */}
            <div className="bg-secondary rounded-xl border border-border-gray shadow-sm overflow-hidden">
              <button
                onClick={() => setIsResumeCoverageExpanded(!isResumeCoverageExpanded)}
                className="w-full flex items-center justify-between p-4 bg-secondary/80 hover:bg-secondary transition-colors"
              >
                <span className="font-bold text-primary flex items-center gap-2 text-sm">
                  <Briefcase className="h-4 w-4 text-primary" />
                  Resume Experience Coverage
                </span>
                {isResumeCoverageExpanded ? <ChevronUp className="h-4 w-4 text-primary" /> : <ChevronDown className="h-4 w-4 text-primary" />}
              </button>

              {isResumeCoverageExpanded && (
                <div className="border-t border-border-gray p-5 space-y-4 bg-white">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <span className="text-[10px] font-bold text-green-600 uppercase block mb-1.5 tracking-wider">Verified Projects</span>
                      <div className="flex flex-wrap gap-1.5">
                        {intelligence.resume_projects_covered.length === 0 ? (
                          <span className="text-[10px] text-muted-gray italic">No resume projects covered yet</span>
                        ) : (
                          intelligence.resume_projects_covered.map((proj, idx) => (
                            <span key={idx} className="flex items-center gap-1 px-2.5 py-1 bg-green-50 text-green-700 border border-green-200 rounded-lg text-[10px] font-bold shadow-sm">
                              <CheckCircle className="h-3 w-3 text-green-600 shrink-0" />
                              {proj}
                            </span>
                          ))
                        )}
                      </div>
                    </div>

                    <div>
                      <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1.5 tracking-wider">Unverified Projects</span>
                      <div className="flex flex-wrap gap-1.5">
                        {intelligence.resume_projects_remaining.length === 0 ? (
                          <span className="text-[10px] text-muted-gray italic">All projects verified!</span>
                        ) : (
                          intelligence.resume_projects_remaining.map((proj, idx) => (
                            <span key={idx} className="px-2.5 py-1 bg-white text-muted-gray border border-border-gray rounded-lg text-[10px] font-bold shadow-sm">
                              {proj}
                            </span>
                          ))
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>

          </div>
        </div>
      ) : (
        <div className="space-y-8 animate-fade-in pb-12">
          {/* Dossier Header */}
          <div className="bg-secondary p-6 rounded-xl border border-border-gray shadow-sm flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-xl font-black text-primary uppercase tracking-wider mb-1">
                Candidate Technical Assessment Dossier
              </h1>
              <p className="text-xs text-muted-gray">
                Authoritative evaluation compiled for hiring team review • Session: <span className="font-mono text-primary font-bold">{id}</span>
                {finalReport?.evaluated_at && (
                  <span className="ml-2 font-medium">• Finalized: {new Date(finalReport.evaluated_at).toLocaleString()}</span>
                )}
              </p>
            </div>
            {finalReport?.is_finalized && (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 bg-green-50 text-green-700 border border-green-200 rounded-lg text-xs font-black uppercase tracking-wider self-start sm:self-auto">
                <CheckCircle className="h-3.5 w-3.5" />
                Verified & Finalized
              </span>
            )}
          </div>

          {/* Top Score Cards Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {/* Card 1: Overall Score */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between text-center">
              <div>
                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">
                  Overall Candidate Score
                </span>
                <span className="text-5xl font-black text-primary block my-3" data-testid="overall-score">
                  {typeof finalReport?.overall_score === 'number'
                    ? `${finalReport.overall_score}%`
                    : 'Insufficient Evidence'}
                </span>
              </div>
              <span className="text-xs text-muted-gray font-medium">
                {finalReport?.scoring_formula || '70% Q&A Accuracy + 30% Holistic Competency'}
              </span>
            </div>

            {/* Card 2: Q&A Accuracy */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between text-center">
              <div>
                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">
                  Q&A Accuracy Average
                </span>
                <span className="text-5xl font-black text-primary block my-3" data-testid="qa-accuracy">
                  {typeof finalReport?.qa_accuracy_average === 'number'
                    ? `${finalReport.qa_accuracy_average}%`
                    : 'N/A'}
                </span>
              </div>
              <span className="text-xs text-muted-gray font-medium">
                Arithmetic mean of {finalReport?.qa_evaluated_count ?? (finalReport?.question_analysis?.filter(q => typeof q.accuracy_score === 'number').length || 0)} evaluated Q&A pairs
              </span>
            </div>

            {/* Card 3: Holistic Competency */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between text-center">
              <div>
                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">
                  Holistic Competency
                </span>
                <span className="text-5xl font-black text-primary block my-3" data-testid="holistic-score">
                  {typeof finalReport?.holistic_competency?.score === 'number'
                    ? `${finalReport.holistic_competency.score}%`
                    : 'N/A'}
                </span>
              </div>
              <span className="text-xs text-muted-gray font-medium">
                Synthesized across 4 core competency vectors
              </span>
            </div>
          </div>

          {/* Neutral Hiring Decision Card */}
          <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">
                Hiring Recommendation
              </span>
              <span className="inline-flex items-center gap-2 px-3 py-1 rounded-lg text-sm border font-extrabold text-muted-gray bg-gray-50 border-gray-200" data-testid="hiring-decision">
                Decision not yet configured
              </span>
            </div>
            <p className="text-xs text-muted-gray leading-relaxed font-medium max-w-xl">
              Awaiting organizational evaluation criteria configuration. No hiring decision is inferred from the candidate's scores.
            </p>
          </div>

          {/* Core Competency Dimensions Scorecard */}
          <div className="bg-secondary rounded-xl p-6 border border-border-gray shadow-sm space-y-4">
            <h2 className="text-xs font-bold text-primary uppercase tracking-wider border-b border-border-gray pb-2">
              Holistic Competency Dimensions
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { name: 'Technical Depth', key: 'technical_depth' as const, desc: 'Architectural understanding, depth of core technologies, and systems design' },
                { name: 'Practical Experience', key: 'practical_experience' as const, desc: 'Production deployment, real-world troubleshooting, and concrete implementation' },
                { name: 'Problem Solving', key: 'problem_solving' as const, desc: 'Algorithmic reasoning, analytical trade-off analysis, and debugging' },
                { name: 'Communication Clarity', key: 'communication_clarity' as const, desc: 'Structured articulation, clarity of explanations, and precision' },
              ].map((metric) => {
                const dim = finalReport?.holistic_competency?.dimensions?.[metric.key];
                const score = dim?.score;
                const hasScore = typeof score === 'number';
                const scoreColor = hasScore
                  ? (score >= 80 ? 'text-green-600' : score >= 50 ? 'text-amber-600' : 'text-red-600')
                  : 'text-muted-gray';
                return (
                  <div key={metric.key} className="space-y-1.5 p-3.5 bg-white rounded-lg border border-border-gray shadow-sm flex flex-col justify-between" data-testid={`dimension-${metric.key}`}>
                    <div>
                      <div className="flex justify-between items-center text-xs font-bold">
                        <span className="text-primary">{metric.name}</span>
                        <span className={scoreColor}>{hasScore ? `${score}%` : 'N/A'}</span>
                      </div>
                      <div className="w-full h-1.5 bg-secondary rounded-full overflow-hidden border border-border-gray/50 p-0.5 mt-2">
                        <div
                          className={`h-full rounded-full ${hasScore ? (score >= 80 ? 'bg-green-500' : score >= 50 ? 'bg-amber-500' : 'bg-red-500') : 'bg-gray-300'}`}
                          style={{ width: `${hasScore ? score : 0}%` }}
                        />
                      </div>
                    </div>
                    <p className="text-[10px] text-muted-gray leading-tight mt-2 italic">
                      {dim?.summary || metric.desc}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Strengths & Development Areas (Side-by-side) */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Strengths Card */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-green-700 flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <CheckCircle className="h-4 w-4 text-green-600" />
                Candidate Strengths
              </h3>
              <div className="space-y-3 text-xs text-primary leading-relaxed" data-testid="strengths-list">
                {finalReport?.strengths && finalReport.strengths.length > 0 ? (
                  <ul className="list-disc list-inside space-y-2">
                    {finalReport.strengths.map((s, idx) => (
                      <li key={idx} className="leading-relaxed font-medium">{s}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-gray italic">No specific strengths documented in final evaluation.</p>
                )}
              </div>
            </div>

            {/* Development Areas & Gaps Card */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-amber-700 flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Compass className="h-4 w-4 text-amber-600" />
                Development Areas & Gaps
              </h3>
              <div className="space-y-3 text-xs text-primary leading-relaxed" data-testid="development-areas-list">
                {finalReport?.development_areas && finalReport.development_areas.length > 0 ? (
                  <ul className="list-disc list-inside space-y-2">
                    {finalReport.development_areas.map((d, idx) => (
                      <li key={idx} className="leading-relaxed font-medium">{d}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-gray italic">No development areas highlighted in final evaluation.</p>
                )}
              </div>
            </div>
          </div>

          {/* Validation Matrix Section */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* JD Skills Coverage Panel */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4" data-testid="jd-analysis">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <BookOpen className="h-4 w-4 text-primary" />
                JD Skill Requirements Matrix
              </h3>
              {finalReport?.jd_analysis?.summary && (
                <p className="text-xs text-muted-gray italic leading-relaxed">{finalReport.jd_analysis.summary}</p>
              )}
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-2 tracking-wider">Discussed / Covered</span>
                  <div className="space-y-1.5">
                    {(finalReport?.jd_analysis?.covered_skills || []).map((skill, idx) => (
                      <div key={idx} className="flex items-center gap-1.5 font-bold text-green-700">
                        <CheckCircle className="h-3.5 w-3.5 text-green-600 shrink-0" />
                        {skill}
                      </div>
                    ))}
                    {(!finalReport?.jd_analysis?.covered_skills || finalReport.jd_analysis.covered_skills.length === 0) && (
                      <span className="text-muted-gray italic">No skills covered</span>
                    )}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-amber-600 uppercase block mb-2 tracking-wider">Remaining / Unassessed</span>
                  <div className="space-y-1.5 text-muted-gray font-medium">
                    {(finalReport?.jd_analysis?.remaining_skills || []).map((skill, idx) => (
                      <div key={idx} className="flex items-center gap-1.5">
                        <div className="h-3.5 w-3.5 rounded-full border border-border-gray shrink-0" />
                        {skill}
                      </div>
                    ))}
                    {(!finalReport?.jd_analysis?.remaining_skills || finalReport.jd_analysis.remaining_skills.length === 0) && (
                      <span className="text-green-600 italic">All skills assessed</span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Resume Experience Validation */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4" data-testid="resume-validation">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Briefcase className="h-4 w-4 text-primary" />
                Resume Experience Validation
              </h3>
              {finalReport?.resume_validation?.summary && (
                <p className="text-xs text-muted-gray italic leading-relaxed">{finalReport.resume_validation.summary}</p>
              )}
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-2 tracking-wider">Verified Projects</span>
                  <div className="space-y-1.5">
                    {(finalReport?.resume_validation?.verified_projects || []).map((proj, idx) => (
                      <div key={idx} className="flex items-center gap-1.5 font-bold text-green-700">
                        <CheckCircle className="h-3.5 w-3.5 text-green-600 shrink-0" />
                        {proj}
                      </div>
                    ))}
                    {(!finalReport?.resume_validation?.verified_projects || finalReport.resume_validation.verified_projects.length === 0) && (
                      <span className="text-muted-gray italic">No projects verified</span>
                    )}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-muted-gray uppercase block mb-2 tracking-wider">Unverified Projects</span>
                  <div className="space-y-1.5 text-muted-gray font-medium">
                    {(finalReport?.resume_validation?.unverified_projects || []).map((proj, idx) => (
                      <div key={idx} className="flex items-center gap-1.5">
                        <div className="h-3.5 w-3.5 rounded-full border border-border-gray shrink-0" />
                        {proj}
                      </div>
                    ))}
                    {(!finalReport?.resume_validation?.unverified_projects || finalReport.resume_validation.unverified_projects.length === 0) && (
                      <span className="text-green-600 italic">All projects verified</span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Conversation Summary & Observer Notes */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Conversation Summary */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4" data-testid="conversation-summary">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Activity className="h-4 w-4 text-primary" />
                Conversation Summary
              </h3>
              {finalReport?.conversation_summary ? (
                <p className="text-xs text-primary leading-relaxed bg-white p-4 rounded-lg border border-border-gray font-medium">
                  {finalReport.conversation_summary}
                </p>
              ) : (
                <p className="text-xs text-muted-gray italic">No conversation summary recorded.</p>
              )}
            </div>

            {/* Observer Notes */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4" data-testid="observer-notes">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <FileText className="h-4 w-4 text-primary" />
                Interviewer Observer Notes
              </h3>
              {finalReport?.observer_notes && finalReport.observer_notes.length > 0 ? (
                <ul className="space-y-2 bg-white rounded-lg p-4 border border-border-gray text-xs text-primary list-disc list-inside font-medium">
                  {finalReport.observer_notes.map((note, idx) => (
                    <li key={idx} className="leading-relaxed mb-1">{note}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-gray italic">No observer notes logged during this session.</p>
              )}
            </div>
          </div>

          {/* Question-by-Question Deep Analysis */}
          {(() => {
            const qaList = (finalReport?.question_analysis && finalReport.question_analysis.length > 0)
              ? finalReport.question_analysis
              : (finalReport?.confirmed_qa_pairs || []);

            return (
              <div className="flex flex-col bg-secondary rounded-xl border border-border-gray p-6 space-y-4 shadow-sm" data-testid="question-analysis">
                <h3 className="font-bold text-primary flex items-center justify-between text-sm border-b border-border-gray pb-3">
                  <span className="flex items-center gap-2">
                    <MessageSquare className="h-4 w-4 text-primary" />
                    Detailed Question-by-Question Analysis
                  </span>
                  {qaList.length > 0 && (
                    <span className="text-xs font-bold text-muted-gray">
                      {qaList.length} Confirmed Q&A {qaList.length === 1 ? 'Pair' : 'Pairs'}
                    </span>
                  )}
                </h3>

                {qaList.length > 0 ? (
                  <div className="space-y-4">
                    {qaList.map((qa, idx) => {
                      const hasScore = typeof qa.accuracy_score === 'number';
                      const scoreBadgeColor = hasScore
                        ? (qa.accuracy_score! >= 80
                            ? 'bg-green-50 text-green-700 border-green-200'
                            : qa.accuracy_score! >= 60
                            ? 'bg-amber-50 text-amber-700 border-amber-200'
                            : 'bg-red-50 text-red-700 border-red-200')
                        : 'bg-gray-50 text-muted-gray border-gray-200';

                      return (
                        <div key={qa.qa_id || qa.pair_id || `qa-${idx}`} className="bg-white rounded-xl p-5 border border-border-gray shadow-sm space-y-3">
                          <div className="flex justify-between items-start gap-4">
                            <div className="space-y-1 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider">
                                  Question {idx + 1}
                                </span>
                                <span className="text-[10px] font-mono text-muted-gray bg-secondary px-1.5 py-0.5 rounded border border-border-gray/40">
                                  {qa.qa_id || qa.pair_id}
                                </span>
                              </div>
                              <p className="text-xs font-bold text-primary leading-relaxed">{qa.question}</p>
                            </div>
                            <div className={`px-2.5 py-1 rounded-lg text-xs font-black border uppercase tracking-wider shrink-0 ${scoreBadgeColor}`}>
                              {hasScore ? `${qa.accuracy_score}% Accuracy` : 'Accuracy: N/A'}
                            </div>
                          </div>

                          <div className="bg-secondary/40 p-3.5 rounded-lg border border-border-gray/50 space-y-1">
                            <span className="text-[10px] font-bold text-muted-gray uppercase block tracking-wider">Candidate Response</span>
                            <p className="text-xs text-primary leading-relaxed font-medium">{qa.answer}</p>
                          </div>

                          {qa.observations && (
                            <div className="bg-blue-50/50 p-3 rounded-lg border border-blue-100 text-xs text-blue-900 leading-relaxed">
                              <strong className="font-bold text-blue-950">Evaluation Note: </strong>
                              {qa.observations}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="p-8 text-center text-muted-gray italic text-xs bg-white rounded-lg border border-border-gray">
                    No confirmed Q&A pairs recorded for this session.
                  </div>
                )}
              </div>
            );
          })()}

          {/* Complete Interview Transcript Section */}
          <div className="flex flex-col bg-secondary rounded-xl border border-border-gray p-6 space-y-4 shadow-sm" data-testid="full-transcript">
            <h3 className="font-bold text-primary flex items-center justify-between text-sm border-b border-border-gray pb-3">
              <span className="flex items-center gap-2">
                <FileText className="h-4 w-4 text-primary" />
                Complete Interview Transcript
              </span>
              <span className="text-xs font-bold text-muted-gray">
                {transcript.length} {transcript.length === 1 ? 'Turn' : 'Turns'}
              </span>
            </h3>

            <div className="space-y-4 max-h-[700px] overflow-y-auto pr-1">
              {transcript.map((msg) => {
                const isSystem = msg.speaker === 'System';

                if (isSystem) {
                  return (
                    <div key={getTranscriptEntryKey(msg)} className="flex justify-center">
                      <span className="text-[10px] font-bold bg-border-gray/50 text-muted-gray px-2.5 py-1 rounded-full uppercase tracking-wider">
                        {msg.text}
                      </span>
                    </div>
                  );
                }

                const displayName = getSpeakerDisplayName(msg.speaker || msg.speaker_name, transcript);
                const isCandidate = msg.speaker === 'Candidate' || msg.speaker_role === 'candidate';

                return (
                  <div
                    key={getTranscriptEntryKey(msg)}
                    className={`flex flex-col gap-1 ${isCandidate ? 'items-start' : 'items-end'}`}
                  >
                    <span className="text-[10px] font-bold text-muted-gray px-1">
                      {displayName}
                    </span>

                    <div className={`group relative rounded-xl p-3.5 max-w-[90%] border shadow-sm transition-all ${
                      isCandidate
                        ? 'bg-white border-border-gray text-primary'
                        : 'bg-primary text-white border-primary/30'
                    }`}>
                      <p className="text-xs leading-relaxed">{msg.text}</p>
                      <span className={`block text-[9px] mt-1.5 text-right ${
                        isCandidate ? 'text-muted-gray' : 'text-primary-foreground/75'
                      }`}>
                        {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </span>
                    </div>
                  </div>
                );
              })}
              {transcript.length === 0 && (
                <div className="p-8 text-center text-muted-gray italic text-xs bg-white rounded-lg border border-border-gray">
                  No transcript entries recorded for this session.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
