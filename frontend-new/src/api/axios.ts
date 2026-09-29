import axios from 'axios';

const getBaseUrl = (): string => {
  let url = import.meta.env.VITE_API_URL || 
            import.meta.env.VITE_BACKEND_URL || 
            (process.env as any).BACKEND_URL;

  if (!url) {
    return '/api';
  }

  // Clean trailing slash
  if (url.endsWith('/')) {
    url = url.slice(0, -1);
  }

  // Ensure path ends with /api
  if (!url.endsWith('/api')) {
    url = `${url}/api`;
  }

  return url;
};

const api = axios.create({
  baseURL: getBaseUrl(),
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request Interceptor: Attach JWT Bearer token if present
api.interceptors.request.use(
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
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Do not redirect if already on login page or attempting login
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

export default api;
