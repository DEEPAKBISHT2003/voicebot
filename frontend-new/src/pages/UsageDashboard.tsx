import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Wallet,
  DollarSign,
  Clock,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Eye,
  AlertCircle,
  Copy,
  Check,
  Calendar,
  RotateCcw,
} from 'lucide-react';
import {
  getUsageBalance,
  getUsageSummary,
  getUsageSessions,
  getUsageSessionDetail,
} from '../api/usage';
import type { DateFilterParams } from '../api/usage';
import type {
  BalanceResponse,
  UsageSummary,
  UsageSessionListResponse,
  UsageSessionDetail,
  UsageCall,
} from '../types/usage';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { Badge } from '../components/Badge';
import { Skeleton } from '../components/Loader';
import { EmptyState } from '../components/EmptyState';
import { Modal } from '../components/Modal';

export type DatePreset =
  | 'today'
  | 'yesterday'
  | 'last_7_days'
  | 'last_30_days'
  | 'this_month'
  | 'custom';

export const UsageDashboard: React.FC = () => {
  const queryClient = useQueryClient();
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Date Filter State
  const [selectedPreset, setSelectedPreset] = useState<DatePreset>('last_7_days');
  const [customStartDate, setCustomStartDate] = useState<string>('');
  const [customEndDate, setCustomEndDate] = useState<string>('');
  const [customError, setCustomError] = useState<string | null>(null);
  const [appliedCustomRange, setAppliedCustomRange] = useState<{ start: string; end: string } | null>(null);

  // Client timezone
  const clientTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
      return 'UTC';
    }
  }, []);

  // Compute Active Filter Parameters for Backend Queries
  const filterParams = useMemo<DateFilterParams>(() => {
    if (selectedPreset === 'custom') {
      if (appliedCustomRange) {
        return {
          start_date: appliedCustomRange.start,
          end_date: appliedCustomRange.end,
          timezone: clientTimezone,
        };
      }
      // If custom is chosen but not yet applied, fallback safely to last_7_days
      return {
        preset: 'last_7_days',
        timezone: clientTimezone,
      };
    }
    return {
      preset: selectedPreset,
      timezone: clientTimezone,
    };
  }, [selectedPreset, appliedCustomRange, clientTimezone]);

  // 1. Balance Query (Global Provider Credit)
  const {
    data: balanceData,
    isLoading: isLoadingBalance,
    isError: isErrorBalance,
  } = useQuery<BalanceResponse>({
    queryKey: ['usage-balance'],
    queryFn: () => getUsageBalance(false),
    staleTime: 30000,
  });

  // 2. Summary Query (Filtered by Date Range / Preset)
  const {
    data: summaryData,
    isLoading: isLoadingSummary,
    isFetching: isFetchingSummary,
    isError: isErrorSummary,
  } = useQuery<UsageSummary>({
    queryKey: ['usage-summary', filterParams],
    queryFn: () => getUsageSummary(filterParams),
    staleTime: 30000,
  });

  // 3. Paginated Sessions Query (Filtered by Date Range / Preset)
  const {
    data: sessionsData,
    isLoading: isLoadingSessions,
    isFetching: isFetchingSessions,
    isError: isErrorSessions,
  } = useQuery<UsageSessionListResponse>({
    queryKey: ['usage-sessions', currentPage, filterParams],
    queryFn: () => getUsageSessions(currentPage, 20, filterParams),
    staleTime: 30000,
  });

  // 4. Session Detail Query
  const {
    data: sessionDetailData,
    isLoading: isLoadingDetail,
  } = useQuery<UsageSessionDetail>({
    queryKey: ['usage-session-detail', selectedSessionId],
    queryFn: () => getUsageSessionDetail(selectedSessionId!),
    enabled: !!selectedSessionId,
    staleTime: 30000,
  });

  // Manual Balance Refresh Mutation
  const refreshMutation = useMutation({
    mutationFn: () => getUsageBalance(true),
    onSuccess: (data) => {
      queryClient.setQueryData(['usage-balance'], data);
    },
  });

  // Preset Selection Handler
  const handlePresetChange = (newPreset: DatePreset) => {
    setSelectedPreset(newPreset);
    setCustomError(null);
    setCurrentPage(1);
    if (newPreset !== 'custom') {
      setAppliedCustomRange(null);
    }
  };

  // Custom Range Apply Handler
  const handleApplyCustom = () => {
    if (!customStartDate || !customEndDate) {
      setCustomError('Please select both a start date and an end date.');
      return;
    }
    if (customStartDate > customEndDate) {
      setCustomError('Start date cannot be after end date.');
      return;
    }
    setCustomError(null);
    setAppliedCustomRange({
      start: customStartDate,
      end: customEndDate,
    });
    setCurrentPage(1);
  };

  // Reset Filter to Default
  const handleResetFilter = () => {
    setSelectedPreset('last_7_days');
    setAppliedCustomRange(null);
    setCustomStartDate('');
    setCustomEndDate('');
    setCustomError(null);
    setCurrentPage(1);
  };

  // Copy helper
  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(text);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Format Helpers
  const formatTokens = (val: number | null | undefined): string => {
    if (val === null || val === undefined) return '0';
    return Number(val).toLocaleString();
  };

  const formatCost = (val: number | null | undefined): string => {
    if (val === null || val === undefined) return '$0.00';
    const num = Number(val);
    if (num > 0 && num < 0.01) {
      return `$${num.toFixed(4)}`;
    }
    return `$${num.toFixed(2)}`;
  };

  const formatDuration = (seconds: number | null | undefined): string => {
    if (seconds === null || seconds === undefined || seconds <= 0) return '—';
    const mins = Math.floor(seconds / 60);
    const remainingSecs = seconds % 60;
    const hours = Math.floor(mins / 60);
    const remainingMins = mins % 60;

    if (hours > 0) {
      return `${hours}h ${remainingMins}m`;
    }
    if (remainingMins > 0) {
      return `${remainingMins}m ${remainingSecs}s`;
    }
    return `${remainingSecs}s`;
  };

  const formatDate = (isoString: string | null | undefined): string => {
    if (!isoString) return '—';
    try {
      const date = new Date(isoString);
      return date.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  const formatDateRangeLabel = (startIso?: string | null, endIso?: string | null): string => {
    if (!startIso || !endIso) return '';
    try {
      const s = new Date(startIso);
      const e = new Date(endIso);
      // For display, adjust end boundary backward by 1 second to show the true inclusive last day
      const adjustedEnd = new Date(e.getTime() - 1000);
      return `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })} – ${adjustedEnd.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
    } catch {
      return '';
    }
  };

  const formatStageName = (stage: string): string => {
    const map: Record<string, string> = {
      precompiler: 'Precompiler & JD Analysis',
      role_identifier: 'Speaker Role Identification',
      unified_qa: 'Unified Q/A Worker',
      qa_accuracy: 'Q/A Accuracy Evaluation',
      final_evaluation: 'Final Dossier Evaluation',
      final_eval_shadow: 'Final Eval Shadow',
      assistance: 'Live Copilot Assistance',
      dynamic_suggestions_fallback: 'Dynamic Suggestions Fallback',
      initial_suggestions_fallback: 'Initial Suggestions Fallback',
      static_questions_fallback: 'Static Verification Fallback',
      intelligence: 'Conversation Intelligence',
    };
    return map[stage] || stage.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
  };

  const isDataFetching = isFetchingSummary || isFetchingSessions;

  return (
    <div className="space-y-6">
      {/* Header Section */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-2 border-b border-border-gray">
        <div>
          <h1 className="text-xl font-bold text-primary flex items-center gap-2.5">
            <DollarSign className="h-6 w-6 text-primary" />
            Usage & Cost Analytics
          </h1>
          <p className="text-sm text-muted-gray mt-1">
            Real-time telemetry for DeepSeek token usage, API expenses, and bot-in-meeting durations.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refreshMutation.mutate()}
            isLoading={refreshMutation.isPending}
            className="flex items-center gap-2"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
            Refresh Balance
          </Button>
        </div>
      </div>

      {/* Date-Range Filter Control Bar */}
      <div className="p-3 bg-white rounded-xl border border-border-gray shadow-xs space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center flex-wrap gap-2.5">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-primary">
              <Calendar className="h-4 w-4 text-primary" />
              <span>Time Period:</span>
            </div>

            {/* Single Dropdown Selector */}
            <select
              value={selectedPreset}
              onChange={(e) => handlePresetChange(e.target.value as DatePreset)}
              className="h-9 px-3 py-1.5 text-xs font-medium border border-border-gray rounded-lg bg-white text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary cursor-pointer shadow-xs"
              aria-label="Filter usage by date range"
            >
              <option value="today">Today</option>
              <option value="yesterday">Yesterday</option>
              <option value="last_7_days">Last 7 Days</option>
              <option value="last_30_days">Last 30 Days</option>
              <option value="this_month">This Month</option>
              <option value="custom">Custom Date Range</option>
            </select>

            {/* Active Range Dates Indicator */}
            {summaryData?.start_date && summaryData?.end_date && (
              <span className="text-xs text-muted-gray hidden md:inline-block">
                ({formatDateRangeLabel(summaryData.start_date, summaryData.end_date)})
              </span>
            )}

            {isDataFetching && (
              <RefreshCw className="h-3.5 w-3.5 text-primary animate-spin ml-1" />
            )}
          </div>

          {/* Reset Action */}
          {selectedPreset !== 'last_7_days' && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleResetFilter}
              className="h-8 px-2.5 text-xs text-muted-gray flex items-center gap-1.5 self-start sm:self-auto"
            >
              <RotateCcw className="h-3 w-3" />
              Reset to Last 7 Days
            </Button>
          )}
        </div>

        {/* Custom Date Pickers Drawer (Revealed only when 'custom' is selected) */}
        {selectedPreset === 'custom' && (
          <div className="pt-3 border-t border-border-gray/60 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <label htmlFor="custom-start-date" className="text-xs font-medium text-muted-gray">
                Start Date:
              </label>
              <input
                id="custom-start-date"
                type="date"
                value={customStartDate}
                onChange={(e) => {
                  setCustomStartDate(e.target.value);
                  setCustomError(null);
                }}
                className="h-8 px-2.5 text-xs border border-border-gray rounded-lg bg-white text-primary focus:outline-primary"
              />
            </div>

            <div className="flex items-center gap-1.5">
              <label htmlFor="custom-end-date" className="text-xs font-medium text-muted-gray">
                End Date:
              </label>
              <input
                id="custom-end-date"
                type="date"
                value={customEndDate}
                onChange={(e) => {
                  setCustomEndDate(e.target.value);
                  setCustomError(null);
                }}
                className="h-8 px-2.5 text-xs border border-border-gray rounded-lg bg-white text-primary focus:outline-primary"
              />
            </div>

            <Button
              size="sm"
              onClick={handleApplyCustom}
              disabled={!customStartDate || !customEndDate}
              className="h-8 px-3.5 text-xs"
            >
              Apply
            </Button>

            {customError && (
              <span className="text-xs text-red-600 font-medium">
                {customError}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Top KPI Metrics Cards (3-card Grid: DeepSeek Balance, Balance Consumed, Meeting Time) */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {/* Card 1: DeepSeek Balance (Live Provider Balance) */}
        <Card className="flex flex-col justify-between p-4 bg-white border border-border-gray shadow-xs">
          <div className="flex items-center justify-between text-muted-gray mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider">DeepSeek Balance</span>
            <Wallet className="h-4 w-4 text-primary" />
          </div>
          {isLoadingBalance ? (
            <Skeleton className="h-7 w-24 my-1" />
          ) : isErrorBalance || !balanceData?.is_available || balanceData?.balance === null ? (
            <div>
              <div className="text-lg font-bold text-red-600">Unavailable</div>
              <span className="text-[11px] text-muted-gray">{balanceData?.error || 'Provider offline'}</span>
            </div>
          ) : (
            <div>
              <div className="text-2xl font-bold text-primary">
                ${balanceData.balance.toFixed(2)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <Badge variant={balanceData.cached ? 'info' : 'success'} className="text-[10px] px-1.5 py-0">
                  {balanceData.cached ? 'Cached (60s)' : 'Live'}
                </Badge>
                <span className="text-[11px] text-muted-gray">Remaining Provider Credit</span>
              </div>
            </div>
          )}
        </Card>

        {/* Card 2: Balance Consumed (DeepSeek API Cost for Selected Date Range) */}
        <Card className="flex flex-col justify-between p-4 bg-white border border-border-gray shadow-xs">
          <div className="flex items-center justify-between text-muted-gray mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider">Balance Consumed</span>
            <DollarSign className="h-4 w-4 text-emerald-600" />
          </div>
          {isLoadingSummary ? (
            <Skeleton className="h-7 w-24 my-1" />
          ) : isErrorSummary ? (
            <div className="text-sm text-red-600">Error calculating consumption</div>
          ) : (
            <div>
              <div className="text-2xl font-bold text-emerald-700">
                {formatCost(summaryData?.balance_consumed_usd ?? summaryData?.total_cost_usd)}
              </div>
              <div className="flex items-center gap-1.5 mt-1">
                <span className="text-[11px] text-muted-gray">
                  {formatTokens(summaryData?.total_tokens)} tokens ({summaryData?.deepseek_llm_calls ?? summaryData?.total_llm_calls ?? 0} calls)
                </span>
              </div>
            </div>
          )}
        </Card>

        {/* Card 3: Total Meeting Time */}
        <Card className="flex flex-col justify-between p-4 bg-white border border-border-gray shadow-xs">
          <div className="flex items-center justify-between text-muted-gray mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider">Total Meeting Time</span>
            <Clock className="h-4 w-4 text-amber-600" />
          </div>
          {isLoadingSummary ? (
            <Skeleton className="h-7 w-20 my-1" />
          ) : isErrorSummary ? (
            <div className="text-sm text-red-600">Error</div>
          ) : (
            <div>
              <div className="text-2xl font-bold text-primary">
                {formatDuration(summaryData?.total_meeting_duration_seconds)}
              </div>
              <span className="text-[11px] text-muted-gray">
                {summaryData?.total_meeting_duration_minutes ?? 0} mins across {summaryData?.total_sessions ?? 0} sessions
              </span>
            </div>
          )}
        </Card>
      </div>

      {/* Session Usage Table Section */}
      <Card className="space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold text-primary">Session Consumption & Audit Trail</h2>
            <p className="text-xs text-muted-gray mt-0.5">
              Historical usage per interview session with meeting duration and itemized LLM costs.
            </p>
          </div>
          {sessionsData && sessionsData.total > 0 && (
            <div className="text-xs text-muted-gray">
              Showing {(currentPage - 1) * 20 + 1}–{Math.min(currentPage * 20, sessionsData.total)} of {sessionsData.total} sessions
            </div>
          )}
        </div>

        {isLoadingSessions ? (
          <div className="space-y-3 py-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : isErrorSessions ? (
          <div className="p-8 text-center text-sm text-red-600 bg-red-50 rounded-lg border border-red-200">
            <AlertCircle className="h-6 w-6 mx-auto mb-2 text-red-500" />
            Unable to load session usage data. Please try again.
          </div>
        ) : !sessionsData?.items || sessionsData.items.length === 0 ? (
          <EmptyState
            title="No Usage Sessions Found"
            description="Interview and copilot sessions for the selected period will appear here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-border-gray text-xs font-semibold text-muted-gray uppercase tracking-wider bg-secondary/50">
                  <th className="py-3 px-4">Session ID</th>
                  <th className="py-3 px-4">Start Time</th>
                  <th className="py-3 px-4">Meeting Duration</th>
                  <th className="py-3 px-4">Tokens</th>
                  <th className="py-3 px-4">LLM Calls</th>
                  <th className="py-3 px-4">Total Cost</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-gray text-sm">
                {sessionsData.items.map((session) => (
                  <tr
                    key={session.session_id}
                    className="hover:bg-secondary/40 transition-colors cursor-pointer group"
                    onClick={() => setSelectedSessionId(session.session_id)}
                  >
                    {/* Session ID */}
                    <td className="py-3.5 px-4 font-mono text-xs text-primary flex items-center gap-2">
                      <span>{session.session_id.substring(0, 8)}...</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCopy(session.session_id);
                        }}
                        className="text-muted-gray hover:text-primary opacity-0 group-hover:opacity-100 transition-opacity"
                        title="Copy full Session ID"
                      >
                        {copiedId === session.session_id ? (
                          <Check className="h-3.5 w-3.5 text-green-600" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </td>

                    {/* Start Time */}
                    <td className="py-3.5 px-4 text-muted-gray text-xs">
                      {formatDate(session.meeting_started_at || session.timestamp)}
                    </td>

                    {/* Meeting Duration */}
                    <td className="py-3.5 px-4 text-primary font-medium text-xs">
                      {formatDuration(session.meeting_duration_seconds)}
                    </td>

                    {/* Tokens */}
                    <td className="py-3.5 px-4 text-primary text-xs">
                      {formatTokens(session.total_tokens)}
                    </td>

                    {/* LLM Calls */}
                    <td className="py-3.5 px-4 text-primary text-xs">
                      {session.llm_call_count}
                    </td>

                    {/* Cost */}
                    <td className="py-3.5 px-4 font-semibold text-primary text-xs">
                      {formatCost(session.total_cost_usd)}
                    </td>

                    {/* Actions */}
                    <td className="py-3.5 px-4 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs h-7 px-2.5"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedSessionId(session.session_id);
                        }}
                      >
                        <Eye className="h-3.5 w-3.5 mr-1" />
                        Breakdown
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Controls */}
        {sessionsData && sessionsData.total_pages > 1 && (
          <div className="flex items-center justify-between pt-4 border-t border-border-gray">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))}
              className="flex items-center gap-1 text-xs"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Previous
            </Button>

            <span className="text-xs font-medium text-muted-gray">
              Page {currentPage} of {sessionsData.total_pages}
            </span>

            <Button
              variant="outline"
              size="sm"
              disabled={currentPage >= sessionsData.total_pages}
              onClick={() => setCurrentPage((prev) => Math.min(sessionsData.total_pages, prev + 1))}
              className="flex items-center gap-1 text-xs"
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </Card>

      {/* Session Usage Detail Modal */}
      <Modal
        isOpen={!!selectedSessionId}
        onClose={() => setSelectedSessionId(null)}
        title="Session Usage Breakdown"
        size="lg"
      >
        {isLoadingDetail ? (
          <div className="space-y-4 py-4">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : !sessionDetailData ? (
          <div className="py-8 text-center text-sm text-red-600">
            Unable to load detailed session usage metrics.
          </div>
        ) : (
          <div className="space-y-6">
            {/* Session Summary Header */}
            <div className="p-4 rounded-lg bg-secondary border border-border-gray space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border-gray/60 pb-2">
                <div>
                  <span className="text-[11px] text-muted-gray uppercase font-semibold">Session ID</span>
                  <div className="font-mono text-xs text-primary font-bold">{sessionDetailData.session_id}</div>
                </div>
                <div className="text-right">
                  <span className="text-[11px] text-muted-gray uppercase font-semibold">Total Session Cost</span>
                  <div className="text-lg font-bold text-emerald-600">{formatCost(sessionDetailData.total_cost_usd)}</div>
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div>
                  <span className="text-muted-gray block">Meeting Duration</span>
                  <span className="font-semibold text-primary">{formatDuration(sessionDetailData.meeting_duration_seconds)}</span>
                </div>
                <div>
                  <span className="text-muted-gray block">Total Tokens</span>
                  <span className="font-semibold text-primary">{formatTokens(sessionDetailData.total_tokens)}</span>
                </div>
                <div>
                  <span className="text-muted-gray block">Input / Output</span>
                  <span className="text-primary">{formatTokens(sessionDetailData.prompt_tokens)} / {formatTokens(sessionDetailData.completion_tokens)}</span>
                </div>
                <div>
                  <span className="text-muted-gray block">LLM Calls</span>
                  <span className="font-semibold text-primary">{sessionDetailData.llm_call_count}</span>
                </div>
              </div>
            </div>

            {/* Stage Breakdown for this session */}
            <div>
              <h4 className="text-xs font-bold text-primary uppercase tracking-wider mb-2">Stage Breakdown</h4>
              {sessionDetailData.stage_breakdown.length === 0 ? (
                <div className="text-xs text-muted-gray py-2">No stage data recorded.</div>
              ) : (
                <div className="space-y-2">
                  {sessionDetailData.stage_breakdown.map((st) => (
                    <div key={st.stage} className="flex items-center justify-between p-2 rounded-md bg-secondary/60 text-xs border border-border-gray/40">
                      <span className="font-medium text-primary">{formatStageName(st.stage)}</span>
                      <div className="flex items-center gap-3">
                        <span className="text-muted-gray">{st.calls} calls</span>
                        <span className="text-muted-gray">{formatTokens(st.total_tokens)} tokens</span>
                        <span className="font-bold text-primary">{formatCost(st.cost_usd)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Chronological Itemized Calls Table */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-xs font-bold text-primary uppercase tracking-wider">
                  Itemized LLM Call Events ({sessionDetailData.calls.length})
                </h4>
                <span className="text-[11px] text-muted-gray">Immutable event log</span>
              </div>

              {sessionDetailData.calls.length === 0 ? (
                <div className="text-xs text-muted-gray py-4 text-center border border-dashed border-border-gray rounded-lg">
                  No LLM invocations recorded for this session.
                </div>
              ) : (
                <div className="overflow-x-auto max-h-72 overflow-y-auto border border-border-gray rounded-lg">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 bg-secondary border-b border-border-gray text-muted-gray font-semibold">
                      <tr>
                        <th className="p-2">Time</th>
                        <th className="p-2">Stage & Identifier</th>
                        <th className="p-2">Model</th>
                        <th className="p-2">Tokens (Hit/Miss/Out)</th>
                        <th className="p-2">Cost</th>
                        <th className="p-2">Latency</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-gray/60 font-mono text-[11px]">
                      {sessionDetailData.calls.map((call: UsageCall, idx: number) => (
                        <tr key={call.id || idx} className="hover:bg-secondary/30">
                          <td className="p-2 text-muted-gray whitespace-nowrap">
                            {call.created_at ? new Date(call.created_at).toLocaleTimeString() : '—'}
                          </td>
                          <td className="p-2 text-primary font-sans font-medium">
                            <div>{formatStageName(call.stage)}</div>
                            {call.call_identifier && (
                              <div className="text-[10px] text-muted-gray font-mono">{call.call_identifier}</div>
                            )}
                          </td>
                          <td className="p-2 text-muted-gray whitespace-nowrap">{call.model}</td>
                          <td className="p-2 text-primary whitespace-nowrap font-sans">
                            {formatTokens(call.total_tokens)}
                            <span className="text-[10px] text-muted-gray block">
                              ({call.cache_hit_tokens}h / {call.cache_miss_tokens}m / {call.completion_tokens}out)
                            </span>
                          </td>
                          <td className="p-2 font-semibold text-emerald-700 whitespace-nowrap">
                            {formatCost(call.cost_usd)}
                          </td>
                          <td className="p-2 text-muted-gray whitespace-nowrap">{call.duration_ms}ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};
