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

export default api;
