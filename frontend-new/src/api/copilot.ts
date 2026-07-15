import api from './axios';
import type { TranscriptEntry } from '../types';

export interface StartCopilotRequest {
  jd: string;
  resume: string;
  custom_prompt?: string;
}

export interface StartCopilotResponse {
  session_id: string;
  status: string;
}

export interface CopilotStatusResponse {
  status: string;
  transcript: TranscriptEntry[];
}

export const startCopilot = async (data: StartCopilotRequest): Promise<StartCopilotResponse> => {
  const res = await api.post<StartCopilotResponse>('/copilot/start', data);
  return res.data;
};

export const stopCopilot = async (sessionId: string): Promise<{ status: string }> => {
  const res = await api.post<{ status: string }>(`/copilot/${sessionId}/stop`);
  return res.data;
};

export const getCopilotStatus = async (sessionId: string): Promise<CopilotStatusResponse> => {
  const res = await api.get<CopilotStatusResponse>(`/copilot/${sessionId}/status`);
  return res.data;
};

export const getCopilotWebSocketUrl = (sessionId: string): string => {
  const base = import.meta.env.VITE_API_URL || import.meta.env.VITE_BACKEND_URL || '';
  if (base) {
    try {
      const parsedUrl = new URL(base);
      const wsProtocol = parsedUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsProtocol}//${parsedUrl.host}/api/copilot/ws/session/${sessionId}`;
    } catch (e) {
      console.warn('[CopilotWS] Failed to parse env backend URL, using relative path fallback', e);
    }
  }

  const host = window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

  if (host.includes('localhost') || host.includes('127.0.0.1')) {
    const backendPort = import.meta.env.VITE_BACKEND_PORT || '8000';
    return `ws://localhost:${backendPort}/api/copilot/ws/session/${sessionId}`;
  }

  return `${protocol}//${host}/api/copilot/ws/session/${sessionId}`;
};
