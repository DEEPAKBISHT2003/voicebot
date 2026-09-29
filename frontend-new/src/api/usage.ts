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
 * Retrieve global dashboard usage totals across all sessions and stages.
 */
export const getUsageSummary = async (): Promise<UsageSummary> => {
  const res = await copilotApi.get<UsageSummary>('/copilot/usage/summary');
  return res.data;
};

/**
 * Retrieve paginated session-level usage rollups.
 */
export const getUsageSessions = async (
  page: number = 1,
  limit: number = 20
): Promise<UsageSessionListResponse> => {
  const res = await copilotApi.get<UsageSessionListResponse>('/copilot/usage/sessions', {
    params: { page, limit },
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
