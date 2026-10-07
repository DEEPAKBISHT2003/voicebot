import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import api from '../api/axios';

export interface User {
  id: string;
  email: string;
  name?: string;
  role: 'ADMIN' | 'USER';
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (token: string, role: 'ADMIN' | 'USER', email: string, id: string, name?: string) => void;
  logout: (notice?: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('voicebot_token'));
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const lastHeartbeatRef = useRef<number>(0);

  const logout = useCallback(async (notice?: string) => {
    const currentToken = localStorage.getItem('voicebot_token');
    if (currentToken) {
      try {
        await api.post('/auth/logout');
      } catch (err) {
        // Ignore logout errors if token already invalidated or network down
      }
    }
    localStorage.removeItem('voicebot_token');
    setToken(null);
    setUser(null);
    if (notice) {
      sessionStorage.setItem('auth_expired_notice', notice);
    }
  }, []);

  const login = (newToken: string, role: 'ADMIN' | 'USER', email: string, id: string, name?: string) => {
    localStorage.setItem('voicebot_token', newToken);
    setToken(newToken);
    setUser({ id, email, role, name });
    sessionStorage.removeItem('auth_expired_notice');
    lastHeartbeatRef.current = Date.now();
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
            name: response.data.name,
            role: response.data.role,
          });
          lastHeartbeatRef.current = Date.now();
        } else {
          await logout('Session expired. Please sign in again.');
        }
      } catch (err: any) {
        // If 401 or invalid, clear token and state
        await logout('Session expired. Please sign in again.');
      } finally {
        setIsLoading(false);
      }
    };

    restoreAuth();
  }, [logout]);

  // Heartbeat tracking: send POST /api/auth/heartbeat every 60s only while tab is visible
  useEffect(() => {
    if (!token || !user) return;

    const sendHeartbeatIfVisible = async () => {
      if (document.visibilityState !== 'visible') {
        return;
      }
      try {
        await api.post('/auth/heartbeat');
        lastHeartbeatRef.current = Date.now();
      } catch (err) {
        // Handled by 401 interceptor if expired
      }
    };

    // Periodic heartbeat every 60s
    const intervalId = setInterval(() => {
      sendHeartbeatIfVisible();
    }, 60000);

    // Visibility change handler: trigger heartbeat immediately when becoming visible if >60s since last heartbeat
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        const timeSinceLast = Date.now() - lastHeartbeatRef.current;
        if (timeSinceLast >= 60000) {
          sendHeartbeatIfVisible();
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [token, user]);

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
