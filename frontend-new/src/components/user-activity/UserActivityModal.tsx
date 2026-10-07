import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Monitor, Clock, Shield, Calendar, AlertCircle } from 'lucide-react';
import { Modal } from '../Modal';
import { Button } from '../Button';
import { Skeleton } from '../Loader';
import { EmptyState } from '../EmptyState';
import { fetchUserSessions } from '../../api/activity';
import { formatTimeAgo, formatAbsoluteTime } from './UserActivityTable';
import type { UserActivityItem } from '../../api/activity';

interface UserActivityModalProps {
  user: UserActivityItem | null;
  isOpen: boolean;
  onClose: () => void;
}

export const UserActivityModal: React.FC<UserActivityModalProps> = ({
  user,
  isOpen,
  onClose,
}) => {
  const userId = user?.user_id;

  const { data, isLoading, error } = useQuery({
    queryKey: ['user-sessions', userId],
    queryFn: () => (userId ? fetchUserSessions(userId) : Promise.reject('No user ID')),
    enabled: Boolean(isOpen && userId),
    staleTime: 10000,
  });

  if (!user) return null;

  const sessions = data?.sessions || [];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`User Activity & Session History - ${user.name}`}
      size="lg"
      footer={
        <div className="flex justify-end w-full">
          <Button variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {/* User Identity Header Card */}
        <div className="p-4 rounded-xl bg-secondary/50 border border-border-gray flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-full bg-slate-200 border border-slate-300 flex items-center justify-center text-slate-700 font-bold text-sm">
              {user.name.substring(0, 2).toUpperCase()}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="font-bold text-primary text-base">{user.name}</h4>
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
              </div>
              <p className="text-xs text-muted-gray font-mono">{user.email}</p>
            </div>
          </div>

          <div>
            {user.is_online ? (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                </span>
                Currently Online
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200">
                <span className="h-2 w-2 rounded-full bg-slate-400" />
                Offline
              </span>
            )}
          </div>
        </div>

        {/* User Details Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div className="p-3 rounded-lg border border-border-gray bg-white">
            <span className="text-[11px] font-medium text-muted-gray block mb-1">Account Created</span>
            <span className="font-semibold text-primary block truncate" title={formatAbsoluteTime(user.created_at)}>
              {formatTimeAgo(user.created_at)}
            </span>
          </div>

          <div className="p-3 rounded-lg border border-border-gray bg-white">
            <span className="text-[11px] font-medium text-muted-gray block mb-1">Last Login</span>
            <span className="font-semibold text-primary block truncate" title={formatAbsoluteTime(user.last_login_at)}>
              {formatTimeAgo(user.last_login_at)}
            </span>
          </div>

          <div className="p-3 rounded-lg border border-border-gray bg-white">
            <span className="text-[11px] font-medium text-muted-gray block mb-1">Last Activity</span>
            <span className="font-semibold text-primary block truncate" title={formatAbsoluteTime(user.last_activity_at)}>
              {formatTimeAgo(user.last_activity_at)}
            </span>
          </div>

          <div className="p-3 rounded-lg border border-border-gray bg-white">
            <span className="text-[11px] font-medium text-muted-gray block mb-1">Active Sessions</span>
            <span className="font-bold text-purple-700 block">
              {user.active_session_count} {user.active_session_count === 1 ? 'session' : 'sessions'}
            </span>
          </div>
        </div>

        {/* Session History Section */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h5 className="font-bold text-sm text-primary flex items-center gap-1.5">
              <Monitor className="h-4 w-4 text-primary" />
              Session History
            </h5>
            <span className="text-xs text-muted-gray">
              Showing recent browser and device logins
            </span>
          </div>

          {isLoading ? (
            <div className="border border-border-gray rounded-lg p-4 space-y-2.5 bg-white">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex justify-between items-center py-2 border-b border-border-gray/50 last:border-none">
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-5 w-20 rounded-full" />
                </div>
              ))}
            </div>
          ) : error ? (
            <div className="p-4 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700 flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" />
              Failed to load session history for this user.
            </div>
          ) : sessions.length === 0 ? (
            <div className="border border-border-gray rounded-lg p-6 bg-white">
              <EmptyState
                title="No session records"
                description="This user has not established any recorded sessions yet."
              />
            </div>
          ) : (
            <div className="border border-border-gray rounded-lg overflow-hidden bg-white">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-border-gray bg-secondary/80 text-[11px] font-semibold text-primary">
                    <th className="px-3.5 py-2.5">Session</th>
                    <th className="px-3.5 py-2.5">Login Time</th>
                    <th className="px-3.5 py-2.5">Last Seen</th>
                    <th className="px-3.5 py-2.5 text-right">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y border-border-gray">
                  {sessions.map((sess) => (
                    <tr key={sess.id} className="hover:bg-secondary/40 transition-colors">
                      {/* Session ID */}
                      <td className="px-3.5 py-2.5 font-mono text-[11px] text-muted-gray">
                        {sess.session_id.substring(0, 8)}...
                      </td>

                      {/* Login Time */}
                      <td className="px-3.5 py-2.5 text-primary" title={formatAbsoluteTime(sess.login_at)}>
                        <div className="flex items-center gap-1">
                          <Calendar className="h-3 w-3 text-muted-gray shrink-0" />
                          <span>{formatTimeAgo(sess.login_at)}</span>
                        </div>
                      </td>

                      {/* Last Seen */}
                      <td className="px-3.5 py-2.5 text-primary" title={formatAbsoluteTime(sess.last_seen_at)}>
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3 text-muted-gray shrink-0" />
                          <span>{formatTimeAgo(sess.last_seen_at)}</span>
                        </div>
                      </td>

                      {/* Status */}
                      <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                        {sess.status === 'Active' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                            Active
                          </span>
                        ) : sess.status === 'Stale' ? (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200"
                            title="Inactive / Closed tab (> 15m ago)"
                          >
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                            Stale
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500 border border-slate-200">
                            Logged Out
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
};
