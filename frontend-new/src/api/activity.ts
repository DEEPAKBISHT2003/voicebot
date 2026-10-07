import api from './axios';

export interface UserActivityItem {
  user_id: string;
  name: string;
  email: string;
  role: 'ADMIN' | 'USER' | string;
  is_online: boolean;
  last_login_at: string | null;
  last_activity_at: string | null;
  active_session_count: number;
  created_at: string | null;
}

export interface ActivitySummary {
  total_users: number;
  online_users: number;
  offline_users: number;
  active_sessions: number;
}

export interface ActivityPagination {
  total_items: number;
  total_pages: number;
  page: number;
  page_size: number;
}

export interface UserActivityResponse {
  summary: ActivitySummary;
  pagination: ActivityPagination;
  items: UserActivityItem[];
}

export interface SessionHistoryItem {
  id: string;
  session_id: string;
  login_at: string;
  last_seen_at: string;
  logout_at: string | null;
  is_active: boolean;
  status: 'Active' | 'Logged Out' | 'Stale';
}

export interface UserDetailWithSessionsResponse {
  user: UserActivityItem;
  sessions: SessionHistoryItem[];
}

export interface ActivityFilterParams {
  status?: 'online' | 'offline' | '';
  role?: string;
  search?: string;
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export const fetchUserActivity = async (
  params: ActivityFilterParams = {}
): Promise<UserActivityResponse> => {
  const queryParams: Record<string, any> = {};
  if (params.status) queryParams.status = params.status;
  if (params.role) queryParams.role = params.role;
  if (params.search) queryParams.search = params.search;
  if (params.page) queryParams.page = params.page;
  if (params.page_size) queryParams.page_size = params.page_size;
  if (params.sort_by) queryParams.sort_by = params.sort_by;
  if (params.sort_order) queryParams.sort_order = params.sort_order;

  const response = await api.get('/admin/users/activity', { params: queryParams });
  return response.data;
};

export const fetchUserSessions = async (
  userId: string
): Promise<UserDetailWithSessionsResponse> => {
  const response = await api.get(`/admin/users/${userId}/sessions`);
  return response.data;
};
