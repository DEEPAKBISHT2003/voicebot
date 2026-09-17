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
      const prevFollowUps = prev.filter((q) => q.type === 'Follow-up');
      const prevVerifications = prev.filter((q) => q.type === 'Verification');
      const prevScenarios = prev.filter((q) => q.type === 'Scenario');

      // 1. Follow-up Questions: Keep dynamically suggesting new questions throughout the interview
      const existingFollowUpTexts = new Set(prevFollowUps.map((q) => q.text.trim().toLowerCase()));
      const newFollowUps: CopilotQuestionItem[] = [];
      if (Array.isArray(assist.suggested_follow_up_questions)) {
        assist.suggested_follow_up_questions.forEach((text) => {
          const trimmed = text.trim();
          if (trimmed && !existingFollowUpTexts.has(trimmed.toLowerCase())) {
            existingFollowUpTexts.add(trimmed.toLowerCase());
            newFollowUps.push({
              id: `Follow-up-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
              type: 'Follow-up',
              text: trimmed,
              isPinned: false,
              timestamp: Date.now()
            });
          }
        });
      }

      // 2. Verification Questions: Comes only once per meeting/interview
      let finalVerifications = prevVerifications;
      if (
        prevVerifications.length === 0 &&
        Array.isArray(assist.verification_questions) &&
        assist.verification_questions.length > 0
      ) {
        const pinnedMap = new Map(prevVerifications.map((q) => [q.text.trim().toLowerCase(), q.isPinned]));
        finalVerifications = assist.verification_questions.map((text, idx) => {
          const trimmed = text.trim();
          return {
            id: `Verification-${idx}`,
            type: 'Verification',
            text: trimmed,
            isPinned: pinnedMap.get(trimmed.toLowerCase()) || false,
            timestamp: Date.now()
          };
        });
      }

      // 3. Scenario Questions: Comes only once per meeting/interview
      let finalScenarios = prevScenarios;
      if (
        prevScenarios.length === 0 &&
        Array.isArray(assist.suggested_practical_questions) &&
        assist.suggested_practical_questions.length > 0
      ) {
        const pinnedMap = new Map(prevScenarios.map((q) => [q.text.trim().toLowerCase(), q.isPinned]));
        finalScenarios = assist.suggested_practical_questions.map((text, idx) => {
          const trimmed = text.trim();
          return {
            id: `Scenario-${idx}`,
            type: 'Scenario',
            text: trimmed,
            isPinned: pinnedMap.get(trimmed.toLowerCase()) || false,
            timestamp: Date.now()
          };
        });
      }

      if (newFollowUps.length === 0 && finalVerifications === prevVerifications && finalScenarios === prevScenarios) {
        return prev;
      }

      return [
        ...prevFollowUps,
        ...newFollowUps,
        ...finalVerifications,
        ...finalScenarios
      ];
    });
  };

  const socketRef = useRef<WebSocket | null>(null);

  const updateState = (data: any) => {
    if (!data) return;
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
  };

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
            updateState(data);
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
      const state = socketRef.current.readyState;
      if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) {
        socketRef.current.close();
      }
      socketRef.current = null;
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
    updateState,
  };
};
