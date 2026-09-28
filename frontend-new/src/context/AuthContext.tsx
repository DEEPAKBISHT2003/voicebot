import React, { createContext, useContext, useState, useEffect } from 'react';
import api from '../api/axios';

export interface User {
  id: string;
  email: string;
  role: 'ADMIN' | 'USER';
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (token: string, role: 'ADMIN' | 'USER', email: string, id: string) => void;
  logout: (notice?: string) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('voicebot_token'));
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const logout = (notice?: string) => {
    localStorage.removeItem('voicebot_token');
    setToken(null);
    setUser(null);
    if (notice) {
      sessionStorage.setItem('auth_expired_notice', notice);
    }
  };

  const login = (newToken: string, role: 'ADMIN' | 'USER', email: string, id: string) => {
    localStorage.setItem('voicebot_token', newToken);
    setToken(newToken);
    setUser({ id, email, role });
    sessionStorage.removeItem('auth_expired_notice');
  };

  useEffect(() => {
    const restoreAuth = async () => {
      const storedToken = localStorage.getItem('voicebot_token');
      if (!storedToken) {
        setUser(null);
        setIsLoading(false);
        return;
      }

      try {
        // Call /api/auth/me to restore session and verify validity
        const response = await api.get('/auth/me');
        if (response.data && response.data.email) {
          setUser({
            id: response.data.id,
            email: response.data.email,
            role: response.data.role,
          });
        } else {
          logout('Session expired. Please sign in again.');
        }
      } catch (err: any) {
        // If 401 or invalid, clear token and state
        logout('Session expired. Please sign in again.');
      } finally {
        setIsLoading(false);
      }
    };

    restoreAuth();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated: !!user && !!token,
        isLoading,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
