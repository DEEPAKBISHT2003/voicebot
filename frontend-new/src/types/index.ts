export interface TranscriptEntry {
  role: 'user' | 'assistant';
  text: string;
}

export interface InterviewSession {
  session_id: string;
  timestamp: string | null;
  jd: string;
  resume: string;
  custom_prompt: string | null;
  transcript: TranscriptEntry[];
}

export interface StartSessionRequest {
  jd: string;
  resume: string;
  custom_prompt?: string;
  resume_filename?: string;
  resume_base64?: string;
}

export interface StartSessionResponse {
  session_id: string;
  status: string;
}

export interface SessionStatusResponse {
  session_id: string;
  is_active: boolean;
  status: string;
  transcript: TranscriptEntry[];
}
