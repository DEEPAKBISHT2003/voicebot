import axios from 'axios';

const getCopilotBaseUrl = (): string => {
  let url = import.meta.env.VITE_COPILOT_URL ||
            (process.env as any).COPILOT_URL;

  if (!url) {
    // Fall back to relative path so Vite proxy handles it
    return '/api';
  }

  if (url.endsWith('/')) {
    url = url.slice(0, -1);
  }

  if (!url.endsWith('/api')) {
    url = `${url}/api`;
  }

  return url;
};

const copilotApi = axios.create({
  baseURL: getCopilotBaseUrl(),
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request Interceptor: Attach JWT Bearer token if present
copilotApi.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('voicebot_token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response Interceptor: Handle 401s (token expiry or unauthorized)
copilotApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const isLoginRequest = error.config?.url?.includes('/auth/login');
      if (!isLoginRequest && !window.location.pathname.startsWith('/login')) {
        localStorage.removeItem('voicebot_token');
        sessionStorage.setItem('auth_expired_notice', 'Session expired. Please sign in again.');
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export default copilotApi;
