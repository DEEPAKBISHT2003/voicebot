import React, { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Users, ChevronLeft, ChevronRight, AlertCircle, RefreshCw } from 'lucide-react';
import { fetchUserActivity } from '../api/activity';
import type { UserActivityItem } from '../api/activity';
import { UserActivitySummary } from '../components/user-activity/UserActivitySummary';
import { UserActivityFilters } from '../components/user-activity/UserActivityFilters';
import { UserActivityTable } from '../components/user-activity/UserActivityTable';
import { UserActivityModal } from '../components/user-activity/UserActivityModal';
import { Button } from '../components/Button';

export const UserActivityDashboard: React.FC = () => {
  const [currentPage, setCurrentPage] = useState<number>(1);
  const pageSize = 15;
  const [search, setSearch] = useState<string>('');
  const [debouncedSearch, setDebouncedSearch] = useState<string>('');
  const [status, setStatus] = useState<'online' | 'offline' | ''>('');
  const [role, setRole] = useState<string>('');
  const [sortBy, setSortBy] = useState<string>('last_activity');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [selectedUser, setSelectedUser] = useState<UserActivityItem | null>(null);

  // Debounce search input by 300ms
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedSearch(search);
      setCurrentPage(1);
    }, 300);
    return () => clearTimeout(handler);
  }, [search]);

  // Reset to page 1 on filter changes
  const handleStatusChange = (newStatus: 'online' | 'offline' | '') => {
    setStatus(newStatus);
    setCurrentPage(1);
  };

  const handleRoleChange = (newRole: string) => {
    setRole(newRole);
    setCurrentPage(1);
  };

  const handleSortChange = (field: string) => {
    if (sortBy === field) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      setSortOrder('desc');
    }
  };

  const {
    data,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: [
      'admin-user-activity',
      {
        page: currentPage,
        pageSize,
        search: debouncedSearch,
        status,
        role,
        sortBy,
        sortOrder,
      },
    ],
    queryFn: () =>
      fetchUserActivity({
        page: currentPage,
        page_size: pageSize,
        search: debouncedSearch || undefined,
        status: status || undefined,
        role: role || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
      }),
    refetchInterval: 30000, // Auto-refresh presence every 30 seconds
  });

  const pagination = data?.pagination;
  const totalPages = pagination?.total_pages || 1;
  const totalItems = pagination?.total_items || 0;

  return (
    <div className="h-full flex flex-col overflow-y-auto">
      <div className="max-w-6xl mx-auto w-full p-6 space-y-6">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-border-gray">
          <div>
            <div className="flex items-center gap-2.5">
              <div className="h-9 w-9 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-600">
                <Users className="h-5 w-5" />
              </div>
              <h1 className="text-xl font-bold text-primary">User Activity & Presence</h1>
            </div>
            <p className="text-xs text-muted-gray mt-1">
              Real-time monitoring of user presence, active browser sessions, and login timestamps.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-gray">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              Auto-sync: 30s
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
              className="flex items-center gap-1.5 text-xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>

        {/* Error Notification */}
        {error && (
          <div className="p-4 rounded-lg bg-rose-50 border border-rose-200 text-rose-800 text-xs flex items-center gap-2">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>Failed to load user activity data. Please try again or verify administrator privileges.</span>
          </div>
        )}

        {/* Summary Cards */}
        <UserActivitySummary summary={data?.summary || null} isLoading={isLoading} />

        {/* Filters Bar */}
        <UserActivityFilters
          search={search}
          onSearchChange={setSearch}
          status={status}
          onStatusChange={handleStatusChange}
          role={role}
          onRoleChange={handleRoleChange}
          onRefresh={() => refetch()}
          isRefreshing={isFetching}
        />

        {/* User Activity Table */}
        <div className="space-y-3">
          <UserActivityTable
            users={data?.items || []}
            isLoading={isLoading}
            onSelectUser={(user) => setSelectedUser(user)}
            sortBy={sortBy}
            sortOrder={sortOrder}
            onSortChange={handleSortChange}
          />

          {/* Pagination Controls */}
          {pagination && totalPages > 1 && (
            <div className="flex items-center justify-between px-3 py-3 bg-white border border-border-gray rounded-lg">
              <div className="text-xs text-muted-gray">
                Showing{' '}
                <span className="font-semibold text-primary">
                  {(currentPage - 1) * pageSize + 1}
                </span>{' '}
                to{' '}
                <span className="font-semibold text-primary">
                  {Math.min(currentPage * pageSize, totalItems)}
                </span>{' '}
                of <span className="font-semibold text-primary">{totalItems}</span> users
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={currentPage <= 1 || isFetching}
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  className="flex items-center gap-1 text-xs"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>

                <span className="text-xs font-medium text-muted-gray px-2">
                  Page {currentPage} of {totalPages}
                </span>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={currentPage >= totalPages || isFetching}
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  className="flex items-center gap-1 text-xs"
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* User Detail & Session History Modal */}
        <UserActivityModal
          user={selectedUser}
          isOpen={Boolean(selectedUser)}
          onClose={() => setSelectedUser(null)}
        />
      </div>
    </div>
  );
};
