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
  speaker: 'Interviewer' | 'Candidate' | 'System';
  text: string;
  timestamp: string;
  evaluation?: CopilotEvaluation;
}

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

  const startConnection = async () => {
    if (!sessionId) return;
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
            if (data.transcript) {
              setTranscript(data.transcript);
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
        setStatus((prev) => (prev === 'error' ? 'error' : 'disconnected'));
      };
    } catch (err: any) {
      console.error('[CopilotWS] Setup error:', err);
      setError(err.message || 'Setup error');
      setStatus('error');
    }
  };

  const stopConnection = () => {
    if (socketRef.current) {
      socketRef.current.close();
    }
    setStatus('disconnected');
  };

  // Helper to send manually input/transcribed statements to backend session engine
  const sendMessage = (speaker: 'Interviewer' | 'Candidate' | 'System', text: string) => {
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
  };
};
