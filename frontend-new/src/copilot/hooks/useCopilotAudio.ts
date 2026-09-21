import { useState, useEffect, useRef } from 'react';
import { getCopilotWebSocketUrl } from '../../api/copilot';

export type CopilotConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

export interface CopilotEvaluationDetail {
  rating: number;
  comment: string;
}

export interface CopilotEvaluation {
  technical_accuracy?: CopilotEvaluationDetail;
  confidence?: CopilotEvaluationDetail;
  completeness?: CopilotEvaluationDetail;
  practical_knowledge?: CopilotEvaluationDetail;
  communication?: CopilotEvaluationDetail;
  production_experience?: CopilotEvaluationDetail;
  missing_concepts?: string[];
  knowledge_gaps?: string[];
  question_asker?: string;
  answerer?: string;
  is_complete?: boolean;
  follow_up_required?: boolean;
  follow_up_reason?: string;
}

export interface CopilotTranscriptEntry {
  id?: string;
  turn_id?: number;
  sequence_id?: number;
  speaker: string;
  speaker_name?: string;
  text: string;
  timestamp: string;
  evaluation?: CopilotEvaluation;
  source?: string;
}

export const getTranscriptEntryKey = (entry: CopilotTranscriptEntry): string => {
  if (entry.id) return String(entry.id);
  if (entry.turn_id !== undefined && entry.turn_id !== null) return `turn-${entry.turn_id}`;
  if (entry.sequence_id !== undefined && entry.sequence_id !== null) return `seq-${entry.sequence_id}`;
  const speakerStr = (entry.speaker || entry.speaker_name || 'spk').trim().toLowerCase();
  const timeStr = (entry.timestamp || 'ts').trim();
  const textSnippet = (entry.text || '').trim().toLowerCase();
  return `fp-${speakerStr}-${timeStr}-${textSnippet}`;
};

export const reconcileTranscript = (
  existing: CopilotTranscriptEntry[],
  serverTranscript: CopilotTranscriptEntry[]
): CopilotTranscriptEntry[] => {
  if (!serverTranscript || !Array.isArray(serverTranscript)) return existing;
  if (serverTranscript.length === 0) return existing;

  const existingMap = new Map<string, CopilotTranscriptEntry>();
  existing.forEach((entry) => {
    existingMap.set(getTranscriptEntryKey(entry), entry);
  });

  const result: CopilotTranscriptEntry[] = [];
  const seenKeys = new Set<string>();

  for (const serverEntry of serverTranscript) {
    const key = getTranscriptEntryKey(serverEntry);
    seenKeys.add(key);
    const prev = existingMap.get(key);
    if (prev) {
      result.push({ ...prev, ...serverEntry });
    } else {
      result.push(serverEntry);
    }
  }

  for (const clientEntry of existing) {
    const key = getTranscriptEntryKey(clientEntry);
    if (!seenKeys.has(key)) {
      result.push(clientEntry);
      seenKeys.add(key);
    }
  }

  // Identity check to avoid redundant state updates and auto-scroll jitter
  if (
    result.length === existing.length &&
    result.every((item, i) => {
      const ex = existing[i];
      return (
        getTranscriptEntryKey(item) === getTranscriptEntryKey(ex) &&
        item.text === ex.text &&
        item.speaker === ex.speaker
      );
    })
  ) {
    return existing;
  }

  return result;
};

export const appendSingleTurn = (
  existing: CopilotTranscriptEntry[],
  singleTurn: CopilotTranscriptEntry
): CopilotTranscriptEntry[] => {
  const turnKey = getTranscriptEntryKey(singleTurn);
  const existingIdx = existing.findIndex((e) => getTranscriptEntryKey(e) === turnKey);

  if (existingIdx !== -1) {
    const updated = [...existing];
    updated[existingIdx] = { ...updated[existingIdx], ...singleTurn };
    return updated;
  } else {
    return [...existing, singleTurn];
  }
};

export const deduplicateTranscript = reconcileTranscript;


export interface CopilotTimelinePhase {
  topic: string;
  summary: string;
  message_count: number;
}

export interface CopilotIntelligence {
  current_topic: string;
  covered_skills: string[];
  remaining_skills: string[];
  resume_projects_covered: string[];
  resume_projects_remaining: string[];
  conversation_timeline: CopilotTimelinePhase[];
  interview_progress: {
    total_skills: number;
    covered_count: number;
    percentage: number;
  };
}

export interface CopilotAssistance {
  suggested_follow_up_questions: string[];
  suggested_practical_questions: string[];
  missing_concepts: string[];
  verification_questions: string[];
  recommended_next_topic: string;
  interview_notes: string[];
  current_candidate_understanding: string;
}

export interface CopilotQuestionItem {
  id: string;
  type: 'Follow-up' | 'Verification' | 'Scenario';
  text: string;
  isPinned: boolean;
  timestamp: number;
}

export const useCopilotAudio = (sessionId: string | null) => {
  const [status, setStatus] = useState<CopilotConnectionStatus>('disconnected');
  const [error, setError] = useState<string | null>(null);
  
  // Real-time Copilot analytics & recommendations state
  const [transcript, setTranscript] = useState<CopilotTranscriptEntry[]>([]);
  const [intelligence, setIntelligence] = useState<CopilotIntelligence>({
    current_topic: '',
    covered_skills: [],
    remaining_skills: [],
    resume_projects_covered: [],
    resume_projects_remaining: [],
    conversation_timeline: [],
    interview_progress: { total_skills: 0, covered_count: 0, percentage: 0 }
  });
  const [assistance, setAssistance] = useState<CopilotAssistance>({
    suggested_follow_up_questions: [],
    suggested_practical_questions: [],
    missing_concepts: [],
    verification_questions: [],
    recommended_next_topic: '',
    interview_notes: [],
    current_candidate_understanding: ''
  });

  // Cumulative questions state with pinning support
  const [questions, setQuestions] = useState<CopilotQuestionItem[]>([]);

  const togglePinQuestion = (id: string) => {
    setQuestions((prev) =>
      prev.map((q) => (q.id === id ? { ...q, isPinned: !q.isPinned } : q))
    );
  };

  const processIncomingQuestions = (assist: CopilotAssistance) => {
    setQuestions((prev) => {
      const existingTexts = new Set(prev.map((q) => q.text.trim().toLowerCase()));
      const newItems: CopilotQuestionItem[] = [];

      const addItems = (list: string[] | undefined, type: 'Follow-up' | 'Verification' | 'Scenario') => {
        if (!Array.isArray(list)) return;
        list.forEach((text) => {
          const trimmed = text.trim();
          if (trimmed && !existingTexts.has(trimmed.toLowerCase())) {
            existingTexts.add(trimmed.toLowerCase());
            newItems.push({
              id: `${type}-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
              type,
              text: trimmed,
              isPinned: false,
              timestamp: Date.now()
            });
          }
        });
      };

      addItems(assist.suggested_follow_up_questions, 'Follow-up');
      addItems(assist.verification_questions, 'Verification');
      addItems(assist.suggested_practical_questions, 'Scenario');

      if (newItems.length === 0) return prev;
      return [...prev, ...newItems];
    });
  };

  const socketRef = useRef<WebSocket | null>(null);
  const shouldReconnectRef = useRef<boolean>(false);
  const reconnectTimeoutRef = useRef<any>(null);

  const updateState = (data: any) => {
    if (!data) return;
    if (data.transcript && Array.isArray(data.transcript)) {
      setTranscript((prev) => reconcileTranscript(prev, data.transcript));
    }
    if (data.intelligence) {
      try {
        const parsedIntel = typeof data.intelligence === 'string'
          ? JSON.parse(data.intelligence.replace(/```json/g, '').replace(/```/g, '').trim())
          : data.intelligence;
        setIntelligence(parsedIntel);
      } catch (intelErr) {
        console.warn('[CopilotWS] Error parsing intelligence JSON:', intelErr);
        setIntelligence(data.intelligence);
      }
    }
    if (data.assistance) {
      try {
        const parsedAssist: CopilotAssistance = typeof data.assistance === 'string'
          ? JSON.parse(data.assistance.replace(/```json/g, '').replace(/```/g, '').trim())
          : data.assistance;
        setAssistance(parsedAssist);
        processIncomingQuestions(parsedAssist);
      } catch (assistErr) {
        console.warn('[CopilotWS] Error parsing assistance JSON:', assistErr);
        setAssistance(data.assistance);
      }
    }
  };

  const startConnection = async () => {
    if (!sessionId) return;
    shouldReconnectRef.current = true;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    setStatus('connecting');
    setError(null);

    try {
      const wsUrl = getCopilotWebSocketUrl(sessionId);
      const ws = new WebSocket(wsUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        console.log('[CopilotWS] Connected to copilot session stream');
        setStatus('connected');
        setError(null);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'copilot_update') {
            console.log('[CopilotWS] Received structured state update:', data);
            updateState(data);
          } else if (data.type === 'transcript') {
            console.log('[CopilotWS] Received single transcript turn:', data);
            const singleTurn: CopilotTranscriptEntry = {
              id: data.id,
              turn_id: data.turn_id,
              sequence_id: data.sequence_id,
              speaker: data.speaker_name || data.speaker || 'Unknown',
              speaker_name: data.speaker_name || data.speaker,
              text: data.text || '',
              timestamp: data.timestamp || new Date().toISOString(),
              source: data.source || 'teams_native'
            };
            setTranscript((prev) => appendSingleTurn(prev, singleTurn));
          }
        } catch (err) {
          console.error('[CopilotWS] Failed to parse message frame:', err);
        }
      };

      ws.onerror = (e) => {
        console.error('[CopilotWS] Connection error:', e);
        setError('Failed to connect to Copilot WebSocket.');
        setStatus('error');
      };

      ws.onclose = () => {
        console.log('[CopilotWS] Connection closed');
        if (shouldReconnectRef.current) {
          setStatus('connecting');
          reconnectTimeoutRef.current = setTimeout(() => {
            if (shouldReconnectRef.current) {
              console.log('[CopilotWS] Attempting WebSocket reconnect...');
              startConnection();
            }
          }, 2000);
        } else {
          setStatus((prev) => (prev === 'error' ? 'error' : 'disconnected'));
        }
      };
    } catch (err: any) {
      console.error('[CopilotWS] Setup error:', err);
      setError(err.message || 'Setup error');
      setStatus('error');
    }
  };

  const stopConnection = () => {
    shouldReconnectRef.current = false;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (socketRef.current) {
      const state = socketRef.current.readyState;
      if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) {
        socketRef.current.close();
      }
      socketRef.current = null;
    }
    setStatus('disconnected');
  };

  // Helper to send manually input/transcribed statements to backend session engine
  const sendMessage = (speaker: string, text: string) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      const payload = JSON.stringify({ speaker, text });
      socketRef.current.send(payload);
      console.log('[CopilotWS] Sent message to session engine:', payload);
    } else {
      console.warn('[CopilotWS] Cannot send message, WebSocket is not open.');
    }
  };

  useEffect(() => {
    return () => {
      stopConnection();
    };
  }, []);

  return {
    status,
    error,
    transcript,
    intelligence,
    assistance,
    questions,
    togglePinQuestion,
    startConnection,
    stopConnection,
    sendMessage,
    updateState,
  };
};
