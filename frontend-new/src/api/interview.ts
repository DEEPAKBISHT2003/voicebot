import api from './axios';
import type {
  StartSessionRequest,
  StartSessionResponse,
  InterviewSession,
  SessionStatusResponse,
} from '../types';

export const startInterview = async (data: StartSessionRequest): Promise<StartSessionResponse> => {
  const res = await api.post<StartSessionResponse>('/interviews/start', data);
  return res.data;
};

export const stopInterview = async (sessionId: string): Promise<{ status: string }> => {
  const res = await api.post<{ status: string }>(`/interviews/${sessionId}/stop`);
  return res.data;
};

export const getInterviewStatus = async (sessionId: string): Promise<SessionStatusResponse> => {
  const res = await api.get<SessionStatusResponse>(`/interviews/${sessionId}/status`);
  return res.data;
};

export const getInterview = async (sessionId: string): Promise<InterviewSession> => {
  const res = await api.get<InterviewSession>(`/interviews/${sessionId}`);
  return res.data;
};

export const listInterviews = async (): Promise<InterviewSession[]> => {
  const res = await api.get<InterviewSession[]>('/interviews');
  return res.data;
};

export const getRecordingUrl = (sessionId: string): string => {
  const base = import.meta.env.VITE_API_URL || '/api';
  return `${base}/interviews/${sessionId}/recording`;
};

export const getResumeUrl = (sessionId: string): string => {
  const base = import.meta.env.VITE_API_URL || '/api';
  return `${base}/interviews/${sessionId}/resume`;
};

export const parseResumeFile = async (file: File): Promise<string> => {
  const formData = new FormData();
  formData.append('file', file);

  const res = await api.post<{ text: string }>('/interviews/parse-resume', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return res.data.text;
};

export const uploadInterviewAudio = async (sessionId: string, file: File): Promise<{ status: string }> => {
  const formData = new FormData();
  formData.append('file', file);

  const res = await api.post<{ status: string }>(`/interviews/${sessionId}/upload-audio`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return res.data;
};
