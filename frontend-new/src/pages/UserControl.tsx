import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
  UserCheck,
  UserX,
  Search,
  Shield,
  User as UserIcon,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { getUsers, updateUser, type User } from '../api/users';
import { useAuth } from '../context/AuthContext';
import { Badge } from '../components/Badge';

export const UserControl: React.FC = () => {
  const queryClient = useQueryClient();
  const { user: currentAuthUser } = useAuth();
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState<'ALL' | 'ACTIVE' | 'INACTIVE'>('ALL');
  const [actionError, setActionError] = useState<string | null>(null);
  const [successNotice, setSuccessNotice] = useState<string | null>(null);

  // 1. Fetch Users
  const {
    data: users = [],
    isLoading,
    isError,
    refetch,
    isFetching,
  } = useQuery<User[]>({
    queryKey: ['users'],
    queryFn: getUsers,
  });

  // 2. Toggle Status Mutation
  const toggleStatusMutation = useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      updateUser(userId, { is_active: isActive }),
    onSuccess: (updatedUser) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setActionError(null);
      setSuccessNotice(
        `User ${updatedUser.name || updatedUser.email} is now ${
          updatedUser.is_active ? 'Active' : 'Inactive'
        }.`
      );
      setTimeout(() => setSuccessNotice(null), 4000);
    },
    onError: (err: any) => {
      const msg =
        err.response?.data?.detail ||
        'Failed to update user status. Please check permissions.';
      setActionError(typeof msg === 'string' ? msg : JSON.stringify(msg));
      setTimeout(() => setActionError(null), 5000);
    },
  });

  // 3. Stats Calculations
  const stats = useMemo(() => {
    const total = users.length;
    const active = users.filter((u) => u.is_active).length;
    const inactive = total - active;
    return { total, active, inactive };
  }, [users]);

  // 4. Filtering
  const filteredUsers = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return users.filter((u) => {
      const matchesSearch =
        !query ||
        (u.name && u.name.toLowerCase().includes(query)) ||
        u.email.toLowerCase().includes(query) ||
        (u.employee_id && u.employee_id.toString().includes(query));

      const matchesStatus =
        filterStatus === 'ALL' ||
        (filterStatus === 'ACTIVE' && u.is_active) ||
        (filterStatus === 'INACTIVE' && !u.is_active);

      return matchesSearch && matchesStatus;
    });
  }, [users, searchQuery, filterStatus]);

  const handleToggle = (user: User) => {
    // Guard against self deactivation
    if (user.id === currentAuthUser?.id && user.is_active) {
      if (!window.confirm('Warning: You are about to deactivate your own account. Continue?')) {
        return;
      }
    }
    toggleStatusMutation.mutate({
      userId: user.id,
      isActive: !user.is_active,
    });
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <Users className="h-7 w-7 text-indigo-600" />
            User Control
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Manage user accounts, corporate access, and activate or deactivate platform permissions.
          </p>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50 transition-colors shadow-sm disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin text-indigo-600' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Notifications */}
      {actionError && (
        <div className="rounded-lg bg-red-50 p-4 border border-red-200 flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-red-600 shrink-0 mt-0.5" />
          <p className="text-sm font-medium text-red-800">{actionError}</p>
        </div>
      )}

      {successNotice && (
        <div className="rounded-lg bg-emerald-50 p-4 border border-emerald-200 flex items-start gap-3">
          <CheckCircle2 className="h-5 w-5 text-emerald-600 shrink-0 mt-0.5" />
          <p className="text-sm font-medium text-emerald-800">{successNotice}</p>
        </div>
      )}

      {/* Metrics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="p-3 bg-indigo-50 rounded-lg text-indigo-600">
            <Users className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Total Users</p>
            <p className="text-2xl font-bold text-slate-900">{stats.total}</p>
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="p-3 bg-emerald-50 rounded-lg text-emerald-600">
            <UserCheck className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Active Accounts</p>
            <p className="text-2xl font-bold text-emerald-600">{stats.active}</p>
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center gap-4">
          <div className="p-3 bg-amber-50 rounded-lg text-amber-600">
            <UserX className="h-6 w-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-slate-500">Inactive / Pending Approval</p>
            <p className="text-2xl font-bold text-amber-600">{stats.inactive}</p>
          </div>
        </div>
      </div>

      {/* Filters and Search Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-col sm:flex-row gap-4 justify-between items-center">
        <div className="relative w-full sm:w-96">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by name, email, or employee ID..."
            className="w-full pl-9 pr-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600"
          />
        </div>

        {/* Status Tab Filter */}
        <div className="flex items-center space-x-1 border border-slate-200 p-1 rounded-lg bg-slate-50 w-full sm:w-auto">
          <button
            onClick={() => setFilterStatus('ALL')}
            className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
              filterStatus === 'ALL'
                ? 'bg-white text-indigo-700 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            All ({stats.total})
          </button>
          <button
            onClick={() => setFilterStatus('ACTIVE')}
            className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
              filterStatus === 'ACTIVE'
                ? 'bg-white text-emerald-700 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Active ({stats.active})
          </button>
          <button
            onClick={() => setFilterStatus('INACTIVE')}
            className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
              filterStatus === 'INACTIVE'
                ? 'bg-white text-amber-700 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Inactive ({stats.inactive})
          </button>
        </div>
      </div>

      {/* Users Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {isLoading ? (
          <div className="p-12 text-center space-y-3">
            <Loader2 className="h-8 w-8 text-indigo-600 animate-spin mx-auto" />
            <p className="text-sm text-slate-500">Loading user records...</p>
          </div>
        ) : isError ? (
          <div className="p-12 text-center space-y-3">
            <AlertCircle className="h-8 w-8 text-red-500 mx-auto" />
            <p className="text-sm font-semibold text-slate-900">Failed to load users</p>
            <p className="text-xs text-slate-500">Please check your permissions or network connection.</p>
          </div>
        ) : filteredUsers.length === 0 ? (
          <div className="p-12 text-center space-y-3">
            <Users className="h-10 w-10 text-slate-300 mx-auto" />
            <p className="text-base font-semibold text-slate-800">No users found</p>
            <p className="text-sm text-slate-500 max-w-sm mx-auto">
              {searchQuery
                ? 'No users matched your search criteria.'
                : 'There are currently no users in this view.'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/75 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  <th className="py-3.5 px-6">Name</th>
                  <th className="py-3.5 px-6">Employee ID</th>
                  <th className="py-3.5 px-6">Email</th>
                  <th className="py-3.5 px-6">Role</th>
                  <th className="py-3.5 px-6">Status</th>
                  <th className="py-3.5 px-6 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-sm text-slate-700">
                {filteredUsers.map((u) => {
                  const isCurrent = u.id === currentAuthUser?.id;
                  const isPending = toggleStatusMutation.isPending && toggleStatusMutation.variables?.userId === u.id;

                  return (
                    <tr
                      key={u.id}
                      className="hover:bg-slate-50/50 transition-colors"
                    >
                      {/* Name */}
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-3">
                          <div className="h-9 w-9 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center text-slate-600 font-semibold text-xs shrink-0">
                            {u.name
                              ? u.name
                                  .split(' ')
                                  .map((n) => n[0])
                                  .slice(0, 2)
                                  .join('')
                                  .toUpperCase()
                              : <UserIcon className="h-4 w-4 text-slate-400" />}
                          </div>
                          <div>
                            <div className="font-semibold text-slate-900 flex items-center gap-1.5">
                              {u.name || 'Unnamed User'}
                              {isCurrent && (
                                <span className="text-[10px] bg-indigo-50 text-indigo-700 px-1.5 py-0.5 rounded font-medium">
                                  You
                                </span>
                              )}
                            </div>
                            <div className="text-xs text-slate-400">
                              Registered: {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                            </div>
                          </div>
                        </div>
                      </td>

                      {/* Employee ID */}
                      <td className="py-4 px-6">
                        {u.employee_id !== null && u.employee_id !== undefined ? (
                          <span className="font-mono text-xs bg-slate-100 text-slate-700 px-2 py-1 rounded border border-slate-200 font-medium">
                            {u.employee_id}
                          </span>
                        ) : (
                          <span className="text-slate-400 text-xs italic">N/A</span>
                        )}
                      </td>

                      {/* Email */}
                      <td className="py-4 px-6 font-medium text-slate-800">
                        {u.email}
                      </td>

                      {/* Role */}
                      <td className="py-4 px-6">
                        {u.role === 'ADMIN' ? (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-full bg-purple-50 text-purple-700 border border-purple-200">
                            <Shield className="h-3 w-3" />
                            Admin
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
                            User
                          </span>
                        )}
                      </td>

                      {/* Status Badge */}
                      <td className="py-4 px-6">
                        {u.is_active ? (
                          <Badge variant="success" className="gap-1">
                            <CheckCircle2 className="h-3 w-3" />
                            Active
                          </Badge>
                        ) : (
                          <Badge variant="warning" className="gap-1">
                            <XCircle className="h-3 w-3" />
                            Inactive
                          </Badge>
                        )}
                      </td>

                      {/* Action Button */}
                      <td className="py-4 px-6 text-right">
                        <button
                          onClick={() => handleToggle(u)}
                          disabled={isPending}
                          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-colors shadow-sm disabled:opacity-50 ${
                            u.is_active
                              ? 'bg-white border-amber-300 text-amber-700 hover:bg-amber-50 hover:border-amber-400'
                              : 'bg-emerald-600 border-emerald-600 text-white hover:bg-emerald-700 hover:border-emerald-700'
                          }`}
                        >
                          {isPending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : u.is_active ? (
                            <>
                              <UserX className="h-3.5 w-3.5" />
                              Deactivate
                            </>
                          ) : (
                            <>
                              <UserCheck className="h-3.5 w-3.5" />
                              Make Active
                            </>
                          )}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
