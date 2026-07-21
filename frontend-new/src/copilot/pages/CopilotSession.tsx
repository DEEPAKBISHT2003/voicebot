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
  VolumeX
} from 'lucide-react';
import { useCopilotAudio } from '../hooks/useCopilotAudio';
import { stopCopilot, getCopilotStatus, finalizeCopilotReport } from '../../api/copilot';

export const CopilotSession: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  
  const { 
    status, 
    error, 
    transcript, 
    intelligence, 
    assistance, 
    startConnection, 
    stopConnection
  } = useCopilotAudio(id || null);

  const [uiMode, setUiMode] = useState<'live' | 'report'>('live');

  // Simulation mode check & audio control states
  const searchParams = new URLSearchParams(window.location.search);
  const isSimulation = searchParams.get('simulate') === 'true';

  const [isSimulationFinished, setIsSimulationFinished] = useState<boolean>(false);
  const [isGeneratingReport, setIsGeneratingReport] = useState<boolean>(false);
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [volume, setVolume] = useState<number>(1.0);

  const isMutedRef = React.useRef(isMuted);
  const volumeRef = React.useRef(volume);

  useEffect(() => {
    isMutedRef.current = isMuted;
    volumeRef.current = volume;
  }, [isMuted, volume]);

  // Establish Copilot WebSocket connection on mount or when id changes
  useEffect(() => {
    if (id) {
      startConnection();
    }
    return () => {
      stopConnection();
    };
  }, [id]);

  // Secondary connection to trigger audio simulation and receive binary audio frames if simulate=true
  useEffect(() => {
    if (!id) return;
    const simulate = searchParams.get('simulate');
    if (simulate !== 'true') return;

    console.log('[Simulation] Initiating background simulation trigger connection...');
    
    let wsUrl = `ws://localhost:8000/api/ws/interview/${id}?mode=observer&simulate=true`;
    const rawEnvUrl = import.meta.env.VITE_API_URL || 
                      import.meta.env.VITE_BACKEND_URL;
    if (rawEnvUrl) {
      try {
        const parsedUrl = new URL(rawEnvUrl);
        const wsProtocol = parsedUrl.protocol === 'https:' ? 'wss:' : 'ws:';
        wsUrl = `${wsProtocol}//${parsedUrl.host}/api/ws/interview/${id}?mode=observer&simulate=true`;
      } catch (e) {
        console.warn('[SimulationWS] Failed to parse env backend URL', e);
      }
    }

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
        audioCtx.close().catch(() => {});
      }
    };
    return () => {
      ws.close();
      if (audioCtx) {
        audioCtx.close().catch(() => {});
      }
    };
  }, [id]);

  // Accordion open/close toggles for Live Interview view
  const [isTranscriptExpanded, setIsTranscriptExpanded] = useState<boolean>(false);
  const [isJdCoverageExpanded, setIsJdCoverageExpanded] = useState<boolean>(false);
  const [isResumeCoverageExpanded, setIsResumeCoverageExpanded] = useState<boolean>(false);
  const [isAllQuestionsExpanded, setIsAllQuestionsExpanded] = useState<boolean>(false);

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

  // Helper to determine the status of the candidate's latest evaluated answer
  const getLatestAnswerStatus = () => {
    const candidateEvaluations = transcript.filter(m => m.speaker === 'Candidate' && m.evaluation);
    if (candidateEvaluations.length === 0) return { label: 'No Answer Detected', color: 'text-muted-gray bg-gray-50 border-gray-200' };
    
    const latest = candidateEvaluations[candidateEvaluations.length - 1];
    const rating = latest.evaluation?.technical_accuracy?.rating;
    
    if (rating === undefined) {
      return { label: 'Evaluating...', color: 'text-amber-500 bg-amber-50 border-amber-200 animate-pulse font-bold' };
    }
    if (rating >= 80) {
      return { label: 'Strong Answer', color: 'text-green-700 bg-green-50 border-green-200 font-bold' };
    }
    if (rating >= 50) {
      return { label: 'Partial Answer', color: 'text-amber-700 bg-amber-50 border-amber-200 font-bold' };
    }
    return { label: 'Weak Answer', color: 'text-red-700 bg-red-50 border-red-200 font-bold' };
  };

  // Poll backend status to auto-detect session closure
  useEffect(() => {
    if (!id) return;

    const checkStatus = async () => {
      try {
        const res = await getCopilotStatus(id);
        const active = (res as any).is_active;
        if (active === false) {
          setIsSimulationFinished(true);
        }
      } catch (err) {
        console.error('Failed to query session status:', err);
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => clearInterval(interval);
  }, [id]);

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

  // Helper to calculate candidate average score from latest evaluations
  const getCandidateScore = () => {
    const candidateMessages = transcript.filter(m => m.speaker === 'Candidate' && m.evaluation);
    if (candidateMessages.length === 0) return 'N/A';
    
    let totalAccuracy = 0;
    let count = 0;
    candidateMessages.forEach(m => {
      const accuracy = m.evaluation?.technical_accuracy?.rating;
      if (accuracy !== undefined && accuracy > 0) {
        totalAccuracy += accuracy;
        count++;
      }
    });

    return count > 0 ? `${Math.round(totalAccuracy / count)}%` : 'N/A';
  };

  // Helper to calculate average score for custom metric keys
  const getAverageMetric = (key: 'technical_accuracy' | 'confidence' | 'completeness' | 'practical_knowledge' | 'communication' | 'production_experience') => {
    const candidateMessages = transcript.filter(m => m.speaker === 'Candidate' && m.evaluation);
    if (candidateMessages.length === 0) return 0;
    
    let total = 0;
    let count = 0;
    candidateMessages.forEach(m => {
      // Safely access evaluation fields dynamically
      const val = (m.evaluation as any)?.[key]?.rating;
      if (val !== undefined && val > 0) {
        total += val;
        count++;
      }
    });
    return count > 0 ? Math.round(total / count) : 0;
  };

  // Helper to collect all unique knowledge gaps from candidate evaluations
  const getUniqueGaps = () => {
    const gaps = new Set<string>();
    transcript.forEach(m => {
      m.evaluation?.knowledge_gaps?.forEach(g => gaps.add(g));
    });
    return Array.from(gaps);
  };

  // Helper to collect all unique missing concepts from candidate evaluations
  const getUniqueMissingConcepts = () => {
    const concepts = new Set<string>();
    transcript.forEach(m => {
      m.evaluation?.missing_concepts?.forEach(c => concepts.add(c));
    });
    return Array.from(concepts);
  };

  // Helper to calculate recommended hiring decision and rationale
  const getHiringRecommendation = () => {
    const scoreStr = getCandidateScore();
    if (scoreStr === 'N/A') {
      return {
        decision: 'No Decision',
        rationale: 'Insufficient response evaluations gathered during this session to formulate a talent recommendation.',
        color: 'text-muted-gray bg-gray-50 border-gray-200'
      };
    }
    const score = parseInt(scoreStr);
    if (score >= 85) {
      return {
        decision: 'Strong Hire',
        rationale: 'Candidate demonstrated exceptional core mastery, strong accuracy scores, and minimal conceptual missing points.',
        color: 'text-green-700 bg-green-50 border-green-200 shadow-sm'
      };
    }
    if (score >= 70) {
      return {
        decision: 'Hire',
        rationale: 'Candidate has healthy competency levels across primary skill vectors with minor development areas.',
        color: 'text-blue-700 bg-blue-50 border-blue-200 shadow-sm'
      };
    }
    if (score >= 50) {
      return {
        decision: 'Borderline Review',
        rationale: 'Candidate showed uneven response quality with notable gaps in practical design or concept understanding.',
        color: 'text-amber-700 bg-amber-50 border-amber-200 shadow-sm'
      };
    }
    return {
      decision: 'No Hire',
      rationale: 'Significant competency deficiencies, repeated knowledge gaps, and low communication or accuracy scores observed.',
      color: 'text-red-700 bg-red-50 border-red-200 shadow-sm'
    };
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
                  AI Copilot Console
                </>
              )}
            </h2>
            <p className="text-xs text-muted-gray select-all">Session ID: {id}</p>
          </div>
        </div>

        {uiMode === 'live' && (
          <div className="flex items-center gap-3 self-end sm:self-auto">
            {/* View Final Results Button */}
            <button
              onClick={async () => {
                if (!id) return;
                setIsGeneratingReport(true);
                try {
                  await finalizeCopilotReport(id);
                  setUiMode('report');
                } catch (err) {
                  console.error('Failed to compile final report:', err);
                  setUiMode('report');
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
            <div className="flex items-center gap-2 px-3 py-1.5 bg-white rounded-lg border border-border-gray">
              <span className={`h-2.5 w-2.5 rounded-full ${
                status === 'connected' ? 'bg-green-500 animate-pulse' :
                status === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-muted-gray'
              }`} />
              <span className="text-xs font-bold capitalize text-primary">{status}</span>
            </div>

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
          </div>
        )}
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-600 rounded-lg text-xs font-semibold">
          Error: {error}
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
                  className={`p-2 rounded-lg text-xs font-bold border transition-all flex items-center gap-1.5 ${
                    isMuted
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
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Current Topic Card */}
            <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-between">
              <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">Current Discussion Topic</span>
              <div className="flex items-center gap-2 text-sm font-bold text-primary py-1">
                <Activity className="h-4 w-4 text-green-500 shrink-0" />
                {intelligence.current_topic || 'No topic detected yet'}
              </div>
            </div>

            {/* Interview Decision Card */}
            {(() => {
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
            })()}

            {/* Recommended Next Topic Card */}
            <div className="bg-secondary rounded-xl p-4 border border-border-gray shadow-sm flex flex-col justify-between">
              <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1 font-semibold">Recommended Next Topic</span>
              <div className="flex items-center gap-2 text-xs font-bold text-primary bg-primary/5 p-2 rounded-lg border border-primary/10 py-1">
                <Compass className="h-4 w-4 text-primary shrink-0 animate-spin-slow" />
                {assistance.recommended_next_topic || 'Determining next topic...'}
              </div>
            </div>
          </div>

          {/* Primary Suggestions Area */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left Box: Recommended Questions */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col gap-4 min-h-[300px]">
              <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
                <User className="h-4 w-4 text-primary" />
                Recommended Questions
              </h3>
              <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                {status !== 'connected' ? (
                  <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray py-8 gap-1.5">
                    <MessageSquare className="h-8 w-8 stroke-[1.5]" />
                    <p className="text-sm font-bold">Suggestions Stream Offline</p>
                    <p className="text-xs">Connect voice/text channel to start receiving live interviewer assistance tips.</p>
                  </div>
                ) : (
                  (() => {
                    const allQuestions = [
                      ...(assistance.suggested_follow_up_questions || []).map(q => ({
                        text: q,
                        type: 'Follow-up',
                        color: 'bg-purple-50 text-purple-700 border-purple-200'
                      })),
                      ...(assistance.verification_questions || []).map(q => ({
                        text: q,
                        type: 'Verification',
                        color: 'bg-amber-50 text-amber-700 border-amber-200'
                      })),
                      ...(assistance.suggested_practical_questions || []).map(q => ({
                        text: q,
                        type: 'Scenario',
                        color: 'bg-blue-50 text-blue-700 border-blue-200'
                      }))
                    ];

                    if (allQuestions.length === 0) {
                      return (
                        <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray py-8">
                          <p className="text-xs italic">No recommended questions at this stage.</p>
                        </div>
                      );
                    }

                    const visibleQuestions = isAllQuestionsExpanded ? allQuestions : allQuestions.slice(0, 3);

                    return (
                      <div className="space-y-3">
                        <div className="space-y-2">
                          {visibleQuestions.map((q, idx) => (
                            <div key={idx} className="bg-white rounded-lg p-2.5 border border-border-gray/80 text-xs text-primary shadow-sm leading-relaxed flex flex-col gap-1.5 items-start">
                              <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${q.color}`}>
                                {q.type}
                              </span>
                              <span>{q.text}</span>
                            </div>
                          ))}
                        </div>

                        {allQuestions.length > 3 && (
                          <button
                            onClick={() => setIsAllQuestionsExpanded(!isAllQuestionsExpanded)}
                            className="text-xs font-bold text-primary hover:underline text-left mt-2 block"
                          >
                            {isAllQuestionsExpanded ? 'Show Less' : `Show All Recommended Questions (${allQuestions.length})`}
                          </button>
                        )}
                      </div>
                    );
                  })()
                )}
              </div>
            </div>

            {/* Right Box: Short Interview Notes */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col gap-4 min-h-[300px]">
              <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
                <FileText className="h-4 w-4 text-primary" />
                Interviewer / Observer Notes
              </h3>
              <div className="flex-1 overflow-y-auto space-y-3 pr-1">
                {status !== 'connected' ? (
                  <p className="text-xs text-muted-gray text-center py-8">Notes stream offline.</p>
                ) : (
                  <>
                    {assistance.interview_notes && assistance.interview_notes.length > 0 ? (
                      <ul className="space-y-2 bg-white rounded-lg p-4 border border-border-gray text-xs text-primary list-disc list-inside">
                        {assistance.interview_notes.map((note, idx) => (
                          <li key={idx} className="leading-relaxed mb-1 font-medium">{note}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray py-8">
                        <p className="text-xs italic">No observer notes logged yet. Notes will populate as the conversation progresses.</p>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Collapsible Accordion Sections */}
          <div className="space-y-4">
            
            {/* Accordion 1: Live Transcript Log */}
            <div className="bg-secondary rounded-xl border border-border-gray shadow-sm overflow-hidden">
              <button
                onClick={() => setIsTranscriptExpanded(!isTranscriptExpanded)}
                className="w-full flex items-center justify-between p-4 bg-secondary/80 hover:bg-secondary transition-colors"
              >
                <span className="font-bold text-primary flex items-center gap-2 text-sm">
                  <MessageSquare className="h-4 w-4 text-primary" />
                  Live Transcript Log
                </span>
                {isTranscriptExpanded ? <ChevronUp className="h-4 w-4 text-primary" /> : <ChevronDown className="h-4 w-4 text-primary" />}
              </button>

              {isTranscriptExpanded && (
                <div ref={transcriptContainerRef} className="border-t border-border-gray p-4 max-h-[400px] overflow-y-auto space-y-4 bg-white">
                  {transcript.length === 0 ? (
                    <p className="text-xs text-muted-gray text-center py-4">No transcript logged yet.</p>
                  ) : (
                    transcript.map((msg, index) => {
                      const isCandidate = msg.speaker === 'Candidate';
                      const isSystem = msg.speaker === 'System';

                      if (isSystem) {
                        return (
                          <div key={index} className="flex justify-center">
                            <span className="text-[10px] font-bold bg-border-gray/50 text-muted-gray px-2.5 py-1 rounded-full uppercase tracking-wider">
                              {msg.text}
                            </span>
                          </div>
                        );
                      }

                      return (
                        <div 
                          key={index} 
                          className={`flex flex-col gap-1.5 ${isCandidate ? 'items-start' : 'items-end'}`}
                        >
                          <span className="text-[10px] font-bold text-muted-gray px-1">
                            {msg.speaker}
                          </span>
                          <div className={`group relative rounded-xl p-3 max-w-[90%] border shadow-sm transition-all ${
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
          <div className="bg-secondary p-6 rounded-xl border border-border-gray shadow-sm">
            <h1 className="text-xl font-black text-primary uppercase tracking-wider mb-1">Candidate Technical Assessment Dossier</h1>
            <p className="text-xs text-muted-gray">This report is finalized and compiled for hiring manager review. Session: {id}</p>
          </div>

          {/* Hiring Summary Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Card 1: Recommended Hiring Decision */}
            {(() => {
              const rec = getHiringRecommendation();
              return (
                <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between">
                  <div>
                    <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">Recommended Hiring Decision</span>
                    <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-sm border font-extrabold ${rec.color} mt-1`}>
                      {rec.decision}
                    </span>
                  </div>
                  <p className="text-xs text-muted-gray mt-4 leading-relaxed font-medium">{rec.rationale}</p>
                </div>
              );
            })()}

            {/* Card 2: Overall Candidate Score */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between text-center">
              <div>
                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">Overall Candidate Score</span>
                <span className="text-5xl font-black text-primary block my-3">{getCandidateScore()}</span>
              </div>
              <span className="text-xs text-muted-gray font-medium">Aggregated mean accuracy across evaluated candidate answers</span>
            </div>

            {/* Card 3: JD Alignment Progress */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm flex flex-col justify-between">
              <div>
                <span className="text-[10px] font-bold text-muted-gray uppercase tracking-wider block mb-1">JD Alignment Coverage</span>
                <span className="text-2xl font-black text-primary block mt-1">{intelligence.interview_progress.percentage || 0}% Match</span>
              </div>
              <div className="space-y-2 mt-4">
                <div className="w-full h-3 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                  <div 
                    className="h-full bg-primary rounded-full transition-all duration-500" 
                    style={{ width: `${intelligence.interview_progress.percentage || 0}%` }}
                  />
                </div>
                <span className="text-[10px] text-muted-gray block font-medium">Discussion covered {intelligence.covered_skills.length} of {intelligence.covered_skills.length + intelligence.remaining_skills.length} required skill areas</span>
              </div>
            </div>
          </div>

          {/* Core Competency Ratings Scorecard */}
          <div className="bg-secondary rounded-xl p-6 border border-border-gray shadow-sm space-y-4">
            <h2 className="text-xs font-bold text-primary uppercase tracking-wider border-b border-border-gray pb-2">Core Competency Dimensions</h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {[
                { name: 'Technical Accuracy', key: 'technical_accuracy' as const, desc: 'Conceptual validity & correctness of technical explanations' },
                { name: 'Confidence & Assurance', key: 'confidence' as const, desc: 'Speed and presence of responses without hesitation' },
                { name: 'Answer Completeness', key: 'completeness' as const, desc: 'Coverage of all facets and sub-points of questions' },
                { name: 'Practical Knowledge', key: 'practical_knowledge' as const, desc: 'Familiarity with concrete implementations vs abstract theory' },
                { name: 'Communication Clarity', key: 'communication' as const, desc: 'Clarity, phrasing, and structured delivery of technical topics' },
                { name: 'Production Experience', key: 'production_experience' as const, desc: 'Evidence of running, scaling, and maintaining in production' },
              ].map((metric) => {
                const score = getAverageMetric(metric.key);
                const scoreColor = score >= 80 ? 'text-green-600' : score >= 50 ? 'text-amber-600' : 'text-red-600';
                return (
                  <div key={metric.key} className="space-y-1.5 p-3.5 bg-white rounded-lg border border-border-gray shadow-sm">
                    <div className="flex justify-between items-center text-xs font-bold">
                      <span className="text-primary">{metric.name}</span>
                      <span className={scoreColor}>{score}%</span>
                    </div>
                    <div className="w-full h-1.5 bg-secondary rounded-full overflow-hidden border border-border-gray/50 p-0.5">
                      <div 
                        className={`h-full rounded-full ${
                          score >= 80 ? 'bg-green-500' : score >= 50 ? 'bg-amber-500' : 'bg-red-500'
                        }`} 
                        style={{ width: `${score}%` }}
                      />
                    </div>
                    <p className="text-[10px] text-muted-gray leading-tight mt-1">{metric.desc}</p>
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
              <div className="space-y-3 text-xs text-primary leading-relaxed">
                {intelligence.covered_skills.length > 0 && (
                  <div>
                    <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Demonstrated Skill Mastery</span>
                    <ul className="list-disc list-inside space-y-1">
                      {intelligence.covered_skills.map((s, idx) => (
                        <li key={idx}>Fluent explanation and response validation in <strong>{s}</strong></li>
                      ))}
                    </ul>
                  </div>
                )}
                {intelligence.resume_projects_covered.length > 0 && (
                  <div className="pt-2">
                    <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Verified Resume Accomplishments</span>
                    <ul className="list-disc list-inside space-y-1">
                      {intelligence.resume_projects_covered.map((p, idx) => (
                        <li key={idx}>Confirmed hands-on role and contribution on project: <strong>{p}</strong></li>
                      ))}
                    </ul>
                  </div>
                )}
                {intelligence.covered_skills.length === 0 && intelligence.resume_projects_covered.length === 0 && (
                  <p className="text-xs text-muted-gray italic">No significant strengths demonstrated during evaluations.</p>
                )}
              </div>
            </div>

            {/* Development Areas & Gaps Card */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-amber-700 flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Compass className="h-4 w-4 text-amber-600" />
                Development Areas & Gaps
              </h3>
              <div className="space-y-4 text-xs text-primary">
                {getUniqueGaps().length > 0 && (
                  <div>
                    <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Identified Knowledge Gaps</span>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {getUniqueGaps().map((gap, idx) => (
                        <span key={idx} className="px-2 py-0.5 bg-amber-50 text-amber-700 border border-amber-200 rounded text-[10px] font-semibold">{gap}</span>
                      ))}
                    </div>
                  </div>
                )}
                {getUniqueMissingConcepts().length > 0 && (
                  <div>
                    <span className="text-[10px] font-bold text-muted-gray uppercase block mb-1">Omitted JD Core Concepts</span>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {getUniqueMissingConcepts().map((concept, idx) => (
                        <span key={idx} className="px-2 py-0.5 bg-red-50 text-red-700 border border-red-200 rounded text-[10px] font-semibold">{concept}</span>
                      ))}
                    </div>
                  </div>
                )}
                {getUniqueGaps().length === 0 && getUniqueMissingConcepts().length === 0 && (
                  <p className="text-xs text-muted-gray italic">Candidate covered all topics successfully without major gap flags.</p>
                )}
              </div>
            </div>
          </div>

          {/* Validation Matrix Section */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* JD Skills Coverage Panel */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <BookOpen className="h-4 w-4 text-primary" />
                JD Skill Requirements Matrix
              </h3>
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-2 tracking-wider">Discussed / Validated</span>
                  <div className="space-y-1.5">
                    {intelligence.covered_skills.map((skill, idx) => (
                      <div key={idx} className="flex items-center gap-1.5 font-bold text-green-700">
                        <CheckCircle className="h-3.5 w-3.5 text-green-600 shrink-0" />
                        {skill}
                      </div>
                    ))}
                    {intelligence.covered_skills.length === 0 && <span className="text-muted-gray italic">No skills covered</span>}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-amber-600 uppercase block mb-2 tracking-wider">Remaining / Uncovered</span>
                  <div className="space-y-1.5 text-muted-gray font-medium">
                    {intelligence.remaining_skills.map((skill, idx) => (
                      <div key={idx} className="flex items-center gap-1.5">
                        <div className="h-3.5 w-3.5 rounded-full border border-border-gray shrink-0" />
                        {skill}
                      </div>
                    ))}
                    {intelligence.remaining_skills.length === 0 && <span className="text-green-600 italic">All skills discussed!</span>}
                  </div>
                </div>
              </div>
            </div>

            {/* Resume Validation Matrix */}
            <div className="bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Briefcase className="h-4 w-4 text-primary" />
                Resume Experience Validation
              </h3>
              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <span className="text-[10px] font-bold text-green-600 uppercase block mb-2 tracking-wider">Verified Projects</span>
                  <div className="space-y-1.5">
                    {intelligence.resume_projects_covered.map((proj, idx) => (
                      <div key={idx} className="flex items-center gap-1.5 font-bold text-green-700">
                        <CheckCircle className="h-3.5 w-3.5 text-green-600 shrink-0" />
                        {proj}
                      </div>
                    ))}
                    {intelligence.resume_projects_covered.length === 0 && <span className="text-muted-gray italic">No projects verified</span>}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-muted-gray uppercase block mb-2 tracking-wider">Unverified Projects</span>
                  <div className="space-y-1.5 text-muted-gray font-medium">
                    {intelligence.resume_projects_remaining.map((proj, idx) => (
                      <div key={idx} className="flex items-center gap-1.5">
                        <div className="h-3.5 w-3.5 rounded-full border border-border-gray shrink-0" />
                        {proj}
                      </div>
                    ))}
                    {intelligence.resume_projects_remaining.length === 0 && <span className="text-green-600 italic">All projects verified!</span>}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Timeline & Notes Panel */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Timeline */}
            <div className="lg:col-span-1 bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <Activity className="h-4 w-4 text-primary" />
                Conversation Timeline
              </h3>
              {intelligence.covered_skills.length === 0 ? (
                <p className="text-xs text-muted-gray italic">No topics logged in timeline.</p>
              ) : (
                <div className="relative border-l border-border-gray pl-4 ml-2 space-y-4 text-xs">
                  {intelligence.covered_skills.map((skill, idx) => (
                    <div key={idx} className="relative">
                      <div className="absolute -left-[21px] mt-1 h-2.5 w-2.5 rounded-full bg-primary border-2 border-white" />
                      <span className="text-[10px] font-bold text-muted-gray block">Phase {idx + 1}</span>
                      <span className="font-bold text-primary">{skill} discussion</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Notes */}
            <div className="lg:col-span-2 bg-secondary rounded-xl p-5 border border-border-gray shadow-sm space-y-4">
              <h3 className="font-bold text-primary flex items-center gap-2 text-xs uppercase tracking-wider border-b border-border-gray pb-2.5">
                <FileText className="h-4 w-4 text-primary" />
                Interviewer Observer Notes
              </h3>
              {assistance.interview_notes && assistance.interview_notes.length > 0 ? (
                <ul className="space-y-2 bg-white rounded-lg p-4 border border-border-gray text-xs text-primary list-disc list-inside font-medium">
                  {assistance.interview_notes.map((note, idx) => (
                    <li key={idx} className="leading-relaxed mb-1">{note}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-gray italic">No observer notes logged during this session.</p>
              )}
            </div>
          </div>

          {/* Question-by-Question Deep Analysis */}
          <div className="flex flex-col bg-secondary rounded-xl border border-border-gray p-6 space-y-4 shadow-sm">
            <h3 className="font-bold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-3">
              <MessageSquare className="h-4 w-4 text-primary" />
              Detailed Question-by-Question Analysis
            </h3>

            <div className="space-y-6 max-h-[700px] overflow-y-auto pr-1">
              {transcript.map((msg, index) => {
                const isCandidate = msg.speaker === 'Candidate';
                const isSystem = msg.speaker === 'System';

                if (isSystem) {
                  return (
                    <div key={index} className="flex justify-center">
                      <span className="text-[10px] font-bold bg-border-gray/50 text-muted-gray px-2.5 py-1 rounded-full uppercase tracking-wider">
                        {msg.text}
                      </span>
                    </div>
                  );
                }

                return (
                  <div 
                    key={index} 
                    className={`flex flex-col gap-2 ${isCandidate ? 'items-start' : 'items-end'}`}
                  >
                    <span className="text-[10px] font-bold text-muted-gray px-1">
                      {msg.speaker}
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

                    {/* Question evaluation metrics expanded inline */}
                    {isCandidate && msg.evaluation && (
                      <div className="w-full bg-white rounded-lg p-4 border border-border-gray shadow-inner text-xs space-y-3 mt-1">
                        <div className="flex flex-col gap-1.5 pb-2 border-b border-border-gray/50">
                          {msg.evaluation.question_asker && (
                            <div className="flex justify-between items-center text-[10px]">
                              <span className="text-muted-gray font-semibold">Asked By:</span>
                              <span className="font-bold text-primary px-1.5 py-0.5 bg-secondary rounded border border-border-gray/30">{msg.evaluation.question_asker}</span>
                            </div>
                          )}
                          {msg.evaluation.answerer && (
                            <div className="flex justify-between items-center text-[10px]">
                              <span className="text-muted-gray font-semibold">Answered By:</span>
                              <span className="font-bold text-primary px-1.5 py-0.5 bg-secondary rounded border border-border-gray/30">{msg.evaluation.answerer}</span>
                            </div>
                          )}
                          {msg.evaluation.is_complete !== undefined && (
                            <div className="flex justify-between items-center text-[10px]">
                              <span className="text-muted-gray font-semibold">Answer Complete:</span>
                              <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                msg.evaluation.is_complete 
                                  ? 'text-green-600 bg-green-50 border-green-200' 
                                  : 'text-amber-600 bg-amber-50 border-amber-200'
                              }`}>
                                {msg.evaluation.is_complete ? 'YES' : 'NO'}
                              </span>
                            </div>
                          )}
                          {msg.evaluation.follow_up_required !== undefined && (
                            <div className="flex justify-between items-center text-[10px]">
                              <span className="text-muted-gray font-semibold">Follow-up Required:</span>
                              <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                                msg.evaluation.follow_up_required 
                                  ? 'text-red-600 bg-red-50 border-red-200 animate-pulse' 
                                  : 'text-green-600 bg-green-50 border-green-200'
                              }`}>
                                {msg.evaluation.follow_up_required ? 'YES' : 'NO'}
                              </span>
                            </div>
                          )}
                        </div>

                        {msg.evaluation.follow_up_required && msg.evaluation.follow_up_reason && (
                          <div className="bg-red-50 border border-red-200 rounded-lg p-2 text-[10px] text-red-700 leading-normal font-medium">
                            <strong>Follow-up Note:</strong> {msg.evaluation.follow_up_reason}
                          </div>
                        )}

                        <div className="grid grid-cols-2 gap-3 pt-1">
                          {/* Technical accuracy */}
                          {msg.evaluation.technical_accuracy && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Technical Accuracy</span>
                                <span className="font-black text-primary">{msg.evaluation.technical_accuracy.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-primary rounded-full" style={{ width: `${msg.evaluation.technical_accuracy.rating}%` }} />
                              </div>
                              {msg.evaluation.technical_accuracy.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.technical_accuracy.comment}</p>
                              )}
                            </div>
                          )}

                          {/* Confidence */}
                          {msg.evaluation.confidence && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Confidence</span>
                                <span className="font-black text-primary">{msg.evaluation.confidence.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-green-500 rounded-full" style={{ width: `${msg.evaluation.confidence.rating}%` }} />
                              </div>
                              {msg.evaluation.confidence.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.confidence.comment}</p>
                              )}
                            </div>
                          )}

                          {/* Completeness */}
                          {msg.evaluation.completeness && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Completeness</span>
                                <span className="font-black text-primary">{msg.evaluation.completeness.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-blue-500 rounded-full" style={{ width: `${msg.evaluation.completeness.rating}%` }} />
                              </div>
                              {msg.evaluation.completeness.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.completeness.comment}</p>
                              )}
                            </div>
                          )}

                          {/* Practical Skill */}
                          {msg.evaluation.practical_knowledge && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Practical Knowledge</span>
                                <span className="font-black text-primary">{msg.evaluation.practical_knowledge.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-purple-500 rounded-full" style={{ width: `${msg.evaluation.practical_knowledge.rating}%` }} />
                              </div>
                              {msg.evaluation.practical_knowledge.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.practical_knowledge.comment}</p>
                              )}
                            </div>
                          )}

                          {/* Communication */}
                          {msg.evaluation.communication && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Communication</span>
                                <span className="font-black text-primary">{msg.evaluation.communication.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-amber-500 rounded-full" style={{ width: `${msg.evaluation.communication.rating}%` }} />
                              </div>
                              {msg.evaluation.communication.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.communication.comment}</p>
                              )}
                            </div>
                          )}

                          {/* Production experience */}
                          {msg.evaluation.production_experience && (
                            <div className="p-2 bg-secondary rounded-lg border border-border-gray/60 space-y-1">
                              <div className="flex justify-between items-center text-[10px]">
                                <span className="font-bold text-primary">Production Experience</span>
                                <span className="font-black text-primary">{msg.evaluation.production_experience.rating}%</span>
                              </div>
                              <div className="w-full h-1 bg-white rounded-full overflow-hidden border border-border-gray p-0.5">
                                <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${msg.evaluation.production_experience.rating}%` }} />
                              </div>
                              {msg.evaluation.production_experience.comment && (
                                <p className="text-[9px] text-muted-gray leading-tight italic">{msg.evaluation.production_experience.comment}</p>
                              )}
                            </div>
                          )}
                        </div>

                        {/* Concept lists */}
                        <div className="flex flex-col gap-2 pt-2 border-t border-border-gray/50">
                          {msg.evaluation.missing_concepts && msg.evaluation.missing_concepts.length > 0 && (
                            <div>
                              <span className="text-[9px] font-bold text-muted-gray uppercase block mb-1">Omitted Concepts</span>
                              <div className="flex flex-wrap gap-1">
                                {msg.evaluation.missing_concepts.map((concept, idx) => (
                                  <span key={idx} className="px-2 py-0.5 bg-red-50 text-red-600 rounded text-[9px] font-semibold border border-red-100">{concept}</span>
                                ))}
                              </div>
                            </div>
                          )}
                          {msg.evaluation.knowledge_gaps && msg.evaluation.knowledge_gaps.length > 0 && (
                            <div>
                              <span className="text-[9px] font-bold text-muted-gray uppercase block mb-1 font-semibold">Identified Gaps</span>
                              <div className="flex flex-wrap gap-1">
                                {msg.evaluation.knowledge_gaps.map((gap, idx) => (
                                  <span key={idx} className="px-2 py-0.5 bg-amber-50 text-amber-600 rounded text-[9px] font-semibold border border-amber-100">{gap}</span>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
