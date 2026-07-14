import React, { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Mic, MicOff, AlertCircle, Square, ArrowLeft, Download, FileText } from 'lucide-react';
import { getInterviewStatus, stopInterview, getRecordingUrl, getResumeUrl } from '../api/interview';
import { useInterviewAudio } from '../hooks/useInterviewAudio';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Badge } from '../components/Badge';

export const InterviewSession: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  const [isGracefullyStopped, setIsGracefullyStopped] = useState(false);

  // Poll status from REST endpoint to retrieve live transcripts
  const {
    data: statusData,
    error: statusError,
    refetch,
  } = useQuery({
    queryKey: ['interviewStatus', id],
    queryFn: () => getInterviewStatus(id || ''),
    enabled: !!id && !isGracefullyStopped,
    refetchInterval: 1500, // Poll every 1.5s for transcript updates
  });

  // Load custom websocket audio hook
  const {
    status: audioStatus,
    error: audioError,
    micVolume,
    startConnection,
    stopConnection,
  } = useInterviewAudio(id || null);

  // Autoscroll transcript
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [statusData?.transcript]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopConnection();
    };
  }, []);

  const handleStartStream = () => {
    startConnection();
  };

  const handleStopStream = async () => {
    stopConnection();
    if (id) {
      try {
        await stopInterview(id);
        setIsGracefullyStopped(true);
        refetch(); // fetch final transcript
      } catch (err) {
        console.error('Failed to stop session gracefully:', err);
      }
    }
  };

  if (!id) {
    return (
      <div className="p-6 text-center text-sm text-red-700 bg-red-50 rounded-lg">
        Invalid session ID.
      </div>
    );
  }

  const isConnected = audioStatus === 'connected';
  const isConnecting = audioStatus === 'connecting';

  return (
    <div className="space-y-6">
      {/* Header control */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => navigate('/')}
          className="inline-flex items-center gap-1.5 text-sm text-muted-gray hover:text-primary font-medium transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Directory
        </button>

        <div className="flex items-center gap-2">
          {isConnected ? (
            <Badge variant="success">Microphone Live</Badge>
          ) : isConnecting ? (
            <Badge variant="warning">Connecting...</Badge>
          ) : (
            <Badge variant="default">Stream Disconnected</Badge>
          )}
        </div>
      </div>

      {/* Main grids */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Left: Stream Control Panel */}
        <div className="md:col-span-1 space-y-6">
          <Card className="flex flex-col items-center justify-center text-center py-10 space-y-6">
            <h3 className="font-bold text-primary text-base">Audio Console</h3>
            
            {/* Visual audio indicator */}
            <div className="relative flex items-center justify-center">
              <div
                className="absolute rounded-full bg-primary/5 transition-transform duration-75"
                style={{
                  width: '120px',
                  height: '120px',
                  transform: `scale(${1 + micVolume * 4})`,
                }}
              />
              <div
                className={`h-24 w-24 rounded-full flex items-center justify-center border transition-all ${
                  isConnected
                    ? 'bg-primary text-white border-primary shadow-lg shadow-primary/10'
                    : 'bg-secondary text-muted-gray border-border-gray'
                }`}
              >
                {isConnected ? (
                  <Mic className="h-8 w-8 animate-pulse" />
                ) : (
                  <MicOff className="h-8 w-8" />
                )}
              </div>
            </div>

            {/* Dynamic visual indicator bars */}
            {isConnected && (
              <div className="flex gap-1.5 justify-center h-6 items-end">
                {[...Array(6)].map((_, i) => {
                  const scale = Math.max(0.1, micVolume * (1 + (i % 3) * 2));
                  return (
                    <div
                      key={i}
                      className="w-1 bg-primary rounded-full transition-all duration-75"
                      style={{ height: `${Math.min(100, scale * 100)}%` }}
                    />
                  );
                })}
              </div>
            )}

            <div className="space-y-2">
              <p className="text-sm font-semibold text-primary">
                {isConnected
                  ? 'Conversation In Progress'
                  : isConnecting
                  ? 'Requesting Permissions...'
                  : 'Start Interview Session'}
              </p>
              <p className="text-xs text-muted-gray max-w-[200px] mx-auto leading-relaxed">
                {statusData?.status || 'Prepare to talk. Miaa will start the screening greeting.'}
              </p>
            </div>

            {/* Control Buttons */}
            <div className="flex flex-col gap-2.5 w-full max-w-[200px]">
              {!isConnected ? (
                <Button
                  variant="primary"
                  onClick={handleStartStream}
                  isLoading={isConnecting}
                  disabled={isGracefullyStopped}
                >
                  <Mic className="h-4 w-4 mr-2" />
                  Connect Voice
                </Button>
              ) : (
                <Button
                  variant="danger"
                  onClick={handleStopStream}
                >
                  <Square className="h-4 w-4 mr-2" />
                  Stop & Save
                </Button>
              )}
            </div>
          </Card>

          {/* Action downloads when complete */}
          {isGracefullyStopped && (
            <Card className="space-y-4">
              <h4 className="font-bold text-sm text-primary">Session Artifacts</h4>
              <div className="flex flex-col gap-2">
                <a
                  href={getRecordingUrl(id)}
                  download
                  className="w-full inline-flex items-center justify-center font-medium rounded-lg transition-colors border border-border-gray bg-white text-primary hover:bg-secondary px-4 py-2.5 text-sm"
                >
                  <Download className="h-4 w-4 mr-2" />
                  Download WAV Audio
                </a>
                <a
                  href={getResumeUrl(id)}
                  download
                  className="w-full inline-flex items-center justify-center font-medium rounded-lg transition-colors border border-border-gray bg-white text-primary hover:bg-secondary px-4 py-2.5 text-sm"
                >
                  <FileText className="h-4 w-4 mr-2" />
                  Download Resume File
                </a>
              </div>
            </Card>
          )}

          {/* Error notifications */}
          {(audioError || statusError) && (
            <div className="p-4 rounded-lg bg-red-50 border border-red-100 text-xs text-red-800 flex items-start gap-2">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>{audioError || 'Failed to sync live transcript from backend.'}</div>
            </div>
          )}
        </div>

        {/* Right: Scrolling Transcripts */}
        <div className="md:col-span-2 flex flex-col h-[70vh]">
          <Card className="flex-1 flex flex-col h-full overflow-hidden">
            <div className="border-b border-border-gray pb-4 mb-4 flex items-center justify-between">
              <h3 className="font-bold text-primary text-base">Session Transcript</h3>
              {isConnected && <div className="h-2 w-2 rounded-full bg-green-500 animate-ping" />}
            </div>

            {/* Scroll body */}
            <div className="flex-1 overflow-y-auto space-y-4 pr-2">
              {!statusData || statusData.transcript.length === 0 ? (
                <div className="h-full flex items-center justify-center text-sm text-muted-gray italic">
                  No conversation text received yet. Start audio to talk to Miaa.
                </div>
              ) : (
                statusData.transcript.map((entry, index) => {
                  const isAI = entry.role === 'assistant';
                  return (
                    <div
                      key={index}
                      className={`flex flex-col max-w-[85%] ${
                        isAI ? 'self-start mr-auto' : 'self-end ml-auto'
                      }`}
                    >
                      <span className={`text-[10px] font-bold text-muted-gray mb-1 px-1 ${
                        isAI ? 'self-start' : 'self-end'
                      }`}>
                        {isAI ? '🤖 MIAAA (AI INTERVIEWER)' : '🗣️ YOU (CANDIDATE)'}
                      </span>
                      <div
                        className={`p-3.5 rounded-lg text-sm leading-relaxed border ${
                          isAI
                            ? 'bg-secondary border-border-gray text-primary'
                            : 'bg-primary text-white border-primary'
                        }`}
                      >
                        {entry.text}
                      </div>
                    </div>
                  );
                })
              )}
              <div ref={transcriptEndRef} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
