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
  speaker_role?: 'candidate' | 'interviewer' | 'unknown' | string;
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
  initial_suggestions?: string[];
  dynamic_suggestions?: string[];
  scenario_questions?: string[];
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

export interface PreviousAnswerEvaluation {
  score: number;
  question: string;
  answer: string;
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
    initial_suggestions: [],
    dynamic_suggestions: [],
    scenario_questions: [],
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

  // Phase 2W: Single live previous-answer accuracy score (0-100) and evaluation details
  const [previousAnswerAccuracy, setPreviousAnswerAccuracy] = useState<number | null>(null);
  const [previousAnswer, setPreviousAnswer] = useState<PreviousAnswerEvaluation | null>(null);

  const togglePinQuestion = (id: string) => {
    setQuestions((prev) =>
      prev.map((q) => (q.id === id ? { ...q, isPinned: !q.isPinned } : q))
    );
  };

  const processIncomingQuestions = (assist: CopilotAssistance) => {
    setQuestions((prev) => {
      // 1. Follow-up questions (DYNAMIC - append new questions as they arrive)
      const existingFollowUpTexts = new Set(
        prev.filter((q) => q.type === 'Follow-up').map((q) => q.text.trim().toLowerCase())
      );
      const newFollowUps: CopilotQuestionItem[] = [];

      const addFollowUps = (list: string[] | undefined) => {
        if (!Array.isArray(list)) return;
        list.forEach((text) => {
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
      };

      addFollowUps(assist.initial_suggestions);
      addFollowUps(assist.dynamic_suggestions);
      addFollowUps(assist.suggested_follow_up_questions);

      const currentFollowUps = [...prev.filter((q) => q.type === 'Follow-up'), ...newFollowUps];

      // 2. Verification questions (STATIC - exactly 5, locked once established)
      const existingVerifications = prev.filter((q) => q.type === 'Verification');
      let finalVerifications = existingVerifications;

      if (existingVerifications.length !== 5 && Array.isArray(assist.verification_questions)) {
        const validVerifs = assist.verification_questions
          .map((t) => (typeof t === 'string' ? t.trim() : ''))
          .filter(Boolean);
        if (validVerifs.length === 5) {
          const pinnedMap = new Map(existingVerifications.map((q) => [q.text.toLowerCase(), q.isPinned]));
          finalVerifications = validVerifs.map((text, idx) => ({
            id: existingVerifications[idx]?.id || `Verification-${idx}-${Date.now()}`,
            type: 'Verification' as const,
            text,
            isPinned: pinnedMap.get(text.toLowerCase()) || false,
            timestamp: existingVerifications[idx]?.timestamp || Date.now()
          }));
        }
      }

      // 3. Scenario questions (STATIC - exactly 5, locked once established)
      const existingScenarios = prev.filter((q) => q.type === 'Scenario');
      let finalScenarios = existingScenarios;

      const rawScenarios = assist.scenario_questions || assist.suggested_practical_questions;
      if (existingScenarios.length !== 5 && Array.isArray(rawScenarios)) {
        const validScenarios = rawScenarios
          .map((t) => (typeof t === 'string' ? t.trim() : ''))
          .filter(Boolean);
        if (validScenarios.length === 5) {
          const pinnedMap = new Map(existingScenarios.map((q) => [q.text.toLowerCase(), q.isPinned]));
          finalScenarios = validScenarios.map((text, idx) => ({
            id: existingScenarios[idx]?.id || `Scenario-${idx}-${Date.now()}`,
            type: 'Scenario' as const,
            text,
            isPinned: pinnedMap.get(text.toLowerCase()) || false,
            timestamp: existingScenarios[idx]?.timestamp || Date.now()
          }));
        }
      }

      // Check if anything changed
      if (
        newFollowUps.length === 0 &&
        finalVerifications === existingVerifications &&
        finalScenarios === existingScenarios
      ) {
        return prev;
      }

      return [...currentFollowUps, ...finalVerifications, ...finalScenarios];
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
    } else if (
      (data.dynamic_suggestions && Array.isArray(data.dynamic_suggestions)) ||
      (data.scenario_questions && Array.isArray(data.scenario_questions)) ||
      (data.verification_questions && Array.isArray(data.verification_questions))
    ) {
      processIncomingQuestions({
        dynamic_suggestions: data.dynamic_suggestions,
        scenario_questions: data.scenario_questions,
        verification_questions: data.verification_questions,
      } as any);
    }

    // Phase 2W: Hydrate latest previous answer accuracy if available from confirmed_qa_pairs
    if (data.confirmed_qa_pairs && Array.isArray(data.confirmed_qa_pairs)) {
      for (let i = data.confirmed_qa_pairs.length - 1; i >= 0; i--) {
        const qa = data.confirmed_qa_pairs[i];
        const score = qa?.accuracy_score;
        if (typeof score === 'number' && score >= 0) {
          setPreviousAnswerAccuracy(score);
          setPreviousAnswer({
            score,
            question: qa.question || '',
            answer: qa.answer || ''
          });
          break;
        }
      }
    } else if (typeof data.previous_answer_accuracy === 'number') {
      setPreviousAnswerAccuracy(data.previous_answer_accuracy);
      setPreviousAnswer({
        score: data.previous_answer_accuracy,
        question: data.question || '',
        answer: data.answer || ''
      });
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
              speaker_role: data.speaker_role,
              text: data.text || '',
              timestamp: data.timestamp || new Date().toISOString(),
              source: data.source || 'teams_native'
            };
            setTranscript((prev) => appendSingleTurn(prev, singleTurn));
          } else if (data.type === 'qa_evaluated') {
            console.log('[CopilotWS] Received qa_evaluated event:', data);
            if (typeof data.accuracy_score === 'number') {
              setPreviousAnswerAccuracy(data.accuracy_score);
              setPreviousAnswer({
                score: data.accuracy_score,
                question: data.question || '',
                answer: data.answer || ''
              });
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
    previousAnswer,
    previousAnswerAccuracy,
    togglePinQuestion,
    startConnection,
    stopConnection,
    sendMessage,
    updateState,
  };
};
