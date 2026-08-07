import axios from 'axios';

const getBaseUrl = (): string => {
  let url = import.meta.env.VITE_API_URL || 
            import.meta.env.VITE_BACKEND_URL || 
            (process.env as any).BACKEND_URL;

  if (!url || url.includes('localhost')) {
    if (typeof window !== 'undefined') {
      return '/api';
    }
    return 'http://127.0.0.1:8000/api';
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

export default api;
