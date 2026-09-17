import copilotApi from './copilot-axios';
import type { TranscriptEntry } from '../types';

export interface StartCopilotRequest {
  jd: string;
  resume: string;
  custom_prompt?: string;
  session_id?: string;
}

export interface StartCopilotResponse {
  session_id: string;
  status: string;
}

export interface CopilotStatusResponse {
  session_id?: string;
  is_active?: boolean;
  service_off?: boolean;
  status: string;
  transcript: TranscriptEntry[];
  custom_prompt?: string;
  final_report?: any;
  intelligence?: any;
  assistance?: any;
}

export const startCopilot = async (data: StartCopilotRequest): Promise<StartCopilotResponse> => {
  const res = await copilotApi.post<StartCopilotResponse>('/copilot/start', data);
  return res.data;
};

export const stopCopilot = async (sessionId: string): Promise<{ status: string }> => {
  const res = await copilotApi.post<{ status: string }>(`/copilot/${sessionId}/stop`);
  return res.data;
};

export const serviceOffCopilot = async (sessionId: string): Promise<{ status: string }> => {
  const res = await copilotApi.post<{ status: string }>(`/copilot/${sessionId}/service-off`);
  return res.data;
};

export const getCopilotStatus = async (sessionId: string): Promise<CopilotStatusResponse> => {
  const res = await copilotApi.get<CopilotStatusResponse>(`/copilot/${sessionId}/status`);
  return res.data;
};

export const finalizeCopilotReport = async (sessionId: string): Promise<any> => {
  const res = await copilotApi.post(`/copilot/${sessionId}/finalize`);
  return res.data;
};

export const updateCopilotPrompt = async (sessionId: string, custom_prompt: string): Promise<{ status: string; custom_prompt: string }> => {
  const res = await copilotApi.patch<{ status: string; custom_prompt: string }>(`/copilot/${sessionId}/prompt`, {
    custom_prompt,
  });
  return res.data;
};

export const uploadSimulationAudio = async (sessionId: string, file: File): Promise<{ status: string }> => {
  const formData = new FormData();
  formData.append('file', file);
  const res = await copilotApi.post<{ status: string }>(`/copilot/${sessionId}/upload-audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

export interface JoinCopilotMeetingRequest {
  meeting_url: string;
  bot_role?: string;
  bot_name?: string;
}

export interface JoinCopilotMeetingResponse {
  status: string;
  session_id: string;
  pid?: number;
  bot_name?: string;
  bot_role?: string;
}

export const joinCopilotMeeting = async (
  sessionId: string,
  meetingUrl: string,
  botRole: string = 'observer',
  botName: string = 'Appzlogic Moderator'
): Promise<JoinCopilotMeetingResponse> => {
  const res = await copilotApi.post<JoinCopilotMeetingResponse>(`/copilot/${sessionId}/join-meeting`, {
    meeting_url: meetingUrl,
    bot_role: botRole,
    bot_name: botName,
  });
  return res.data;
};

export const getCopilotWebSocketUrl = (sessionId: string): string => {
  // Always use unified port 8000 for copilot WebSocket
  const base = import.meta.env.VITE_COPILOT_URL || '';
  if (base) {
    try {
      const parsedUrl = new URL(base);
      const wsProtocol = parsedUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsProtocol}//${parsedUrl.host}/api/ws/copilot/${sessionId}`;
    } catch (e) {
      console.warn('[CopilotWS] Failed to parse VITE_COPILOT_URL, using default backend port 8000', e);
    }
  }

  const host = window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${host}/api/ws/copilot/${sessionId}`;
};

export const getSimulationWebSocketUrl = (sessionId: string): string => {
  const base = import.meta.env.VITE_COPILOT_URL || '';
  if (base) {
    try {
      const parsedUrl = new URL(base);
      const wsProtocol = parsedUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsProtocol}//${parsedUrl.host}/api/ws/copilot/${sessionId}/simulate`;
    } catch (e) {
      console.warn('[SimulationWS] Failed to parse VITE_COPILOT_URL, using default backend port 8000', e);
    }
  }

  const host = window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${host}/api/ws/copilot/${sessionId}/simulate`;
};
