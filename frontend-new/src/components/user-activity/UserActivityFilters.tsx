import React from 'react';
import { Search, Filter, RefreshCw, X } from 'lucide-react';
import { Button } from '../Button';

interface UserActivityFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  status: 'online' | 'offline' | '';
  onStatusChange: (status: 'online' | 'offline' | '') => void;
  role: string;
  onRoleChange: (role: string) => void;
  onRefresh: () => void;
  isRefreshing?: boolean;
}

export const UserActivityFilters: React.FC<UserActivityFiltersProps> = ({
  search,
  onSearchChange,
  status,
  onStatusChange,
  role,
  onRoleChange,
  onRefresh,
  isRefreshing = false,
}) => {
  const hasActiveFilters = Boolean(search || status || role);

  const clearFilters = () => {
    onSearchChange('');
    onStatusChange('');
    onRoleChange('');
  };

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-3 rounded-lg border border-border-gray">
      {/* Search Input */}
      <div className="relative flex-1 max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-gray" />
        <input
          type="text"
          placeholder="Search by user name or email..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="flex h-9 w-full rounded-lg border border-border-gray bg-white pl-9 pr-8 py-1.5 text-xs text-primary placeholder:text-muted-gray focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary transition-all"
        />
        {search && (
          <button
            onClick={() => onSearchChange('')}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-gray hover:text-primary p-0.5"
            title="Clear search"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Filter Controls */}
      <div className="flex items-center flex-wrap gap-2">
        {/* Status Filter */}
        <div className="flex items-center gap-1.5">
          <Filter className="h-3.5 w-3.5 text-muted-gray shrink-0" />
          <select
            value={status}
            onChange={(e) => onStatusChange(e.target.value as 'online' | 'offline' | '')}
            className="h-9 px-2.5 py-1 text-xs border border-border-gray rounded-lg bg-white text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary cursor-pointer"
          >
            <option value="">All Status</option>
            <option value="online">Online</option>
            <option value="offline">Offline</option>
          </select>
        </div>

        {/* Role Filter */}
        <select
          value={role}
          onChange={(e) => onRoleChange(e.target.value)}
          className="h-9 px-2.5 py-1 text-xs border border-border-gray rounded-lg bg-white text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary cursor-pointer"
        >
          <option value="">All Roles</option>
          <option value="ADMIN">Admin</option>
          <option value="USER">User</option>
        </select>

        {/* Clear Filters Button */}
        {hasActiveFilters && (
          <Button
            variant="outline"
            size="sm"
            onClick={clearFilters}
            className="h-9 px-2.5 text-xs text-muted-gray hover:text-primary"
            title="Reset filters"
          >
            Reset
          </Button>
        )}

        {/* Refresh Button */}
        <Button
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="h-9 px-3 text-xs gap-1.5"
          title="Refresh activity data"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} />
          <span className="hidden sm:inline">Refresh</span>
        </Button>
      </div>
    </div>
  );
};
