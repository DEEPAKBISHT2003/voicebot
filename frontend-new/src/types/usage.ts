/**
 * TypeScript Type Definitions for Phase D/E Copilot Usage & Credit Dashboard.
 */

export interface BalanceInfo {
  currency: string;
  total_balance: string;
  granted_balance?: string;
  topped_up_balance?: string;
}

export interface BalanceResponse {
  is_available: boolean;
  balance: number | null;
  currency: string;
  total_balance: string | null;
  granted_balance: string | null;
  topped_up_balance: string | null;
  balance_infos: BalanceInfo[];
  error?: string;
  source: string;
  cached: boolean;
  stale?: boolean;
  retrieved_at: string;
}

export interface StageUsage {
  stage: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  duration_ms: number;
}

export interface UsageSummary {
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  total_llm_calls: number;
  total_meeting_duration_seconds: number;
  total_meeting_duration_minutes: number;
  total_sessions: number;
  sessions_with_usage: number;
  stage_breakdown: StageUsage[];
}

export interface UsageSessionItem {
  session_id: string;
  timestamp: string | null;
  meeting_started_at: string | null;
  meeting_ended_at: string | null;
  meeting_duration_seconds: number | null;
  meeting_duration_minutes: number | null;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  llm_call_count: number;
}

export interface UsageSessionListResponse {
  items: UsageSessionItem[];
  page: number;
  limit: number;
  total: number;
  total_pages: number;
}

export interface UsageCall {
  id: string;
  stage: string;
  call_identifier: string | null;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cost_usd: number;
  duration_ms: number;
  created_at: string | null;
}

export interface UsageSessionDetail {
  session_id: string;
  timestamp: string | null;
  meeting_started_at: string | null;
  meeting_ended_at: string | null;
  meeting_duration_seconds: number | null;
  meeting_duration_minutes: number | null;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_cost_usd: number;
  llm_call_count: number;
  stage_breakdown: StageUsage[];
  calls: UsageCall[];
}
