/**
 * API Client Functions for Phase D/E Copilot Usage & Credit Dashboard.
 */

import copilotApi from './copilot-axios';
import type {
  BalanceResponse,
  UsageSummary,
  UsageSessionListResponse,
  UsageSessionDetail,
} from '../types/usage';

export interface DateFilterParams {
  preset?: string;
  start_date?: string;
  end_date?: string;
  timezone?: string;
}

/**
 * Retrieve current DeepSeek balance (with 60-second in-memory cache).
 */
export const getUsageBalance = async (forceRefresh: boolean = false): Promise<BalanceResponse> => {
  const res = await copilotApi.get<BalanceResponse>('/copilot/usage/balance', {
    params: forceRefresh ? { force_refresh: true } : undefined,
  });
  return res.data;
};

/**
 * Retrieve global dashboard usage totals across all sessions and stages,
 * optionally filtered by date range or preset.
 */
export const getUsageSummary = async (filter?: DateFilterParams): Promise<UsageSummary> => {
  const res = await copilotApi.get<UsageSummary>('/copilot/usage/summary', {
    params: filter,
  });
  return res.data;
};

/**
 * Retrieve paginated session-level usage rollups, optionally filtered by date range.
 */
export const getUsageSessions = async (
  page: number = 1,
  limit: number = 20,
  filter?: DateFilterParams
): Promise<UsageSessionListResponse> => {
  const res = await copilotApi.get<UsageSessionListResponse>('/copilot/usage/sessions', {
    params: { page, limit, ...filter },
  });
  return res.data;
};

/**
 * Retrieve detailed session breakdown with stage-level aggregation and individual LLM calls.
 */
export const getUsageSessionDetail = async (
  sessionId: string
): Promise<UsageSessionDetail> => {
  const res = await copilotApi.get<UsageSessionDetail>(`/copilot/usage/sessions/${sessionId}`);
  return res.data;
};
