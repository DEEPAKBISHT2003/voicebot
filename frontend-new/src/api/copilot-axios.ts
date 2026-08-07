import axios from 'axios';

const getCopilotBaseUrl = (): string => {
  let url = import.meta.env.VITE_COPILOT_URL ||
            (process.env as any).COPILOT_URL;

  if (!url || url.includes('localhost')) {
    if (typeof window !== 'undefined') {
      return '/api';
    }
    return 'http://127.0.0.1:8001/api';
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

export default copilotApi;
