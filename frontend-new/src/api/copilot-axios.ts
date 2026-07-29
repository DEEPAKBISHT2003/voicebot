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

export default copilotApi;
