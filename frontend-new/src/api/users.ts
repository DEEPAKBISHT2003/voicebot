import api from './axios';

export interface User {
  id: string;
  name?: string | null;
  employee_id?: number | null;
  email: string;
  role: 'ADMIN' | 'USER';
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface UpdateUserPayload {
  role?: 'ADMIN' | 'USER';
  is_active?: boolean;
}

export interface CreateUserPayload {
  name?: string;
  employee_id?: number;
  email: string;
  password: string;
  role?: 'ADMIN' | 'USER';
  is_active?: boolean;
}

export const getUsers = async (): Promise<User[]> => {
  const response = await api.get<User[]>('/users');
  return response.data;
};

export const updateUser = async (
  userId: string,
  payload: UpdateUserPayload
): Promise<User> => {
  const response = await api.patch<User>(`/users/${userId}`, payload);
  return response.data;
};

export const createUser = async (
  payload: CreateUserPayload
): Promise<User> => {
  const response = await api.post<User>('/users', payload);
  return response.data;
};

export const deleteUser = async (userId: string): Promise<void> => {
  await api.delete(`/users/${userId}`);
};
