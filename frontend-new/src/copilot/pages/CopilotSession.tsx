import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Mic, MicOff, MessageSquare, Power, ArrowLeft } from 'lucide-react';
import { useCopilotAudio } from '../hooks/useCopilotAudio';
import { stopCopilot } from '../../api/copilot';

export const CopilotSession: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  
  const { status, error, startConnection, stopConnection } = useCopilotAudio(id || null);

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
      {/* Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={handleEndSession}
          className="flex items-center gap-2 text-sm font-medium text-muted-gray hover:text-primary transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Exit Room
        </button>
        <div className="flex items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${
            status === 'connected' ? 'bg-green-500 animate-pulse' :
            status === 'connecting' ? 'bg-amber-500 animate-pulse' : 'bg-muted-gray'
          }`} />
          <span className="text-xs font-semibold capitalize text-muted-gray">{status}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Connection Control Panel */}
        <div className="md:col-span-1 bg-secondary rounded-xl p-6 border border-border-gray flex flex-col items-center justify-center text-center gap-6 min-h-[300px]">
          <div className={`h-20 w-20 rounded-full flex items-center justify-center border transition-all ${
            status === 'connected' 
              ? 'bg-primary/10 border-primary text-primary scale-105' 
              : 'bg-white border-border-gray text-muted-gray'
          }`}>
            {status === 'connected' ? <Mic className="h-10 w-10" /> : <MicOff className="h-10 w-10" />}
          </div>

          <div className="space-y-1.5">
            <h3 className="font-semibold text-primary">AI Copilot Companion</h3>
            <p className="text-xs text-muted-gray">
              {status === 'connected' ? 'Listening and helping...' : 'Connect to start conversation'}
            </p>
          </div>

          {error && (
            <p className="text-xs text-red-500 max-w-[200px]">{error}</p>
          )}

          <div className="flex gap-3 w-full">
            {status === 'connected' ? (
              <button
                onClick={stopConnection}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg transition-colors"
              >
                <Power className="h-4 w-4" />
                Disconnect
              </button>
            ) : (
              <button
                onClick={startConnection}
                disabled={status === 'connecting'}
                className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-primary hover:bg-primary/95 text-white text-sm font-medium rounded-lg disabled:bg-primary/50 transition-colors"
              >
                <Mic className="h-4 w-4" />
                {status === 'connecting' ? 'Connecting...' : 'Connect Copilot'}
              </button>
            )}
          </div>
        </div>

        {/* Live Copilot Assist Log */}
        <div className="md:col-span-2 bg-secondary rounded-xl p-6 border border-border-gray flex flex-col gap-4">
          <h3 className="font-semibold text-primary flex items-center gap-2 text-sm border-b border-border-gray pb-2.5">
            <MessageSquare className="h-4 w-4" />
            AI Suggestions
          </h3>

          <div className="flex-1 min-h-[220px] max-h-[400px] overflow-y-auto space-y-4">
            {status === 'connected' ? (
              <div className="flex gap-3">
                <div className="h-8 w-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                  <span className="text-xs font-semibold text-primary">AI</span>
                </div>
                <div className="bg-white rounded-lg p-3 border border-border-gray max-w-[85%]">
                  <p className="text-sm text-primary">
                    AI Copilot connected! Speak through your microphone or listen for realtime preparation assistance.
                  </p>
                </div>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-center text-muted-gray py-12 gap-1.5">
                <MessageSquare className="h-8 w-8 stroke-[1.5]" />
                <p className="text-sm font-medium">No active suggestion log</p>
                <p className="text-xs">Connect voice stream to begin receiving copilot suggestions.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
