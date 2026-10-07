import React from 'react';
import { Clock, Shield, ChevronRight } from 'lucide-react';
import { Skeleton } from '../Loader';
import { EmptyState } from '../EmptyState';
import type { UserActivityItem } from '../../api/activity';

interface UserActivityTableProps {
  users: UserActivityItem[];
  isLoading: boolean;
  onSelectUser: (user: UserActivityItem) => void;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  onSortChange?: (field: string) => void;
}

export const formatTimeAgo = (isoString: string | null): string => {
  if (!isoString) return 'Never';
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSec < 0) return 'Just now';
    if (diffSec < 60) return `${diffSec} sec ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin} min ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours} hr${diffHours > 1 ? 's' : ''} ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return 'Invalid date';
  }
};

export const formatAbsoluteTime = (isoString: string | null): string => {
  if (!isoString) return 'Never recorded';
  try {
    return new Date(isoString).toLocaleString();
  } catch {
    return isoString;
  }
};

export const UserActivityTable: React.FC<UserActivityTableProps> = ({
  users,
  isLoading,
  onSelectUser,
}) => {
  if (isLoading) {
    return (
      <div className="border border-border-gray rounded-lg bg-white overflow-hidden p-4 space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="flex items-center justify-between gap-4 py-3 border-b border-border-gray/50 last:border-none">
            <div className="flex items-center gap-3">
              <Skeleton className="h-9 w-9 rounded-full" />
              <div className="space-y-1.5">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
            <Skeleton className="h-6 w-20 rounded-full" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-6 w-16 rounded-full" />
          </div>
        ))}
      </div>
    );
  }

  if (users.length === 0) {
    return (
      <div className="border border-border-gray rounded-lg bg-white p-8">
        <EmptyState
          title="No users found"
          description="No users matched the specified activity or role criteria. Try adjusting your filters."
        />
      </div>
    );
  }

  return (
    <div className="border border-border-gray rounded-lg overflow-hidden bg-white shadow-2xs">
      <table className="w-full text-left border-collapse table-fixed">
        <thead>
          <tr className="border-b border-border-gray bg-secondary text-xs font-semibold text-primary">
            <th className="px-4 py-3.5 w-[20%] whitespace-nowrap">Name</th>
            <th className="px-4 py-3.5 w-[22%] whitespace-nowrap">Email</th>
            <th className="px-3 py-3.5 w-[11%] whitespace-nowrap">Role</th>
            <th className="px-3 py-3.5 w-[13%] whitespace-nowrap">Status</th>
            <th className="px-3 py-3.5 w-[13%] whitespace-nowrap">Last Login</th>
            <th className="px-3 py-3.5 w-[13%] whitespace-nowrap">Last Activity</th>
            <th className="px-3 py-3.5 w-[8%] text-right whitespace-nowrap">Sessions</th>
          </tr>
        </thead>
        <tbody className="divide-y border-border-gray text-xs">
          {users.map((user) => {
            const initials = (user.name || user.email)
              .split(' ')
              .map((n) => n[0])
              .join('')
              .substring(0, 2)
              .toUpperCase();

            return (
              <tr
                key={user.user_id}
                onClick={() => onSelectUser(user)}
                className="hover:bg-secondary/60 transition-colors cursor-pointer group"
              >
                {/* Name */}
                <td className="px-4 py-3 font-medium text-primary">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="h-7 w-7 rounded-full bg-slate-100 border border-slate-200 text-slate-700 flex items-center justify-center text-[11px] font-bold shrink-0">
                      {initials}
                    </div>
                    <span className="truncate block font-semibold text-slate-800" title={user.name}>
                      {user.name}
                    </span>
                  </div>
                </td>

                {/* Email */}
                <td className="px-4 py-3 text-muted-gray">
                  <span className="truncate block font-mono text-[11px]" title={user.email}>
                    {user.email}
                  </span>
                </td>

                {/* Role */}
                <td className="px-3 py-3 whitespace-nowrap">
                  {user.role === 'ADMIN' ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">
                      <Shield className="h-2.5 w-2.5" />
                      Admin
                    </span>
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-700 border border-slate-200">
                      User
                    </span>
                  )}
                </td>

                {/* Online Status */}
                <td className="px-3 py-3 whitespace-nowrap">
                  {user.is_online ? (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                      <span className="relative flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                      </span>
                      Online
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-100 text-slate-500 border border-slate-200">
                      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                      Offline
                    </span>
                  )}
                </td>

                {/* Last Login */}
                <td
                  className="px-3 py-3 text-muted-gray whitespace-nowrap"
                  title={formatAbsoluteTime(user.last_login_at)}
                >
                  <div className="flex items-center gap-1 text-[11px]">
                    <Clock className="h-3 w-3 text-slate-400 shrink-0" />
                    <span>{formatTimeAgo(user.last_login_at)}</span>
                  </div>
                </td>

                {/* Last Activity */}
                <td
                  className="px-3 py-3 text-primary font-medium whitespace-nowrap"
                  title={formatAbsoluteTime(user.last_activity_at)}
                >
                  <span className="text-[11px]">{formatTimeAgo(user.last_activity_at)}</span>
                </td>

                {/* Active Session Count */}
                <td className="px-3 py-3 text-right whitespace-nowrap">
                  <div className="inline-flex items-center gap-1 justify-end">
                    <span
                      className={`inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[11px] font-bold ${
                        user.active_session_count > 0
                          ? 'bg-purple-100 text-purple-800 border border-purple-200'
                          : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      {user.active_session_count}
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 text-slate-300 group-hover:text-primary transition-colors" />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
