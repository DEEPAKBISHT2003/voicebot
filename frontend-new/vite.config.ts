import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load environment variables from parent directory (root workspace folder) and local frontend directory
  const rootEnv = loadEnv(mode, '../', '')
  const localEnv = loadEnv(mode, process.cwd(), '')
  const env = { ...localEnv, ...rootEnv }
  const backendUrl = env.BACKEND_URL || 'http://localhost:8000'
  const copilotUrl = env.COPILOT_URL || 'http://localhost:8000'
  const frontendUrl = env.FRONTEND_URL || 'http://localhost:3000'

  // Extract port from FRONTEND_URL if it's a valid web URL (e.g. not "*")
  let frontendPort = 3000
  if (frontendUrl && (frontendUrl.startsWith('http://') || frontendUrl.startsWith('https://'))) {
    try {
      const parsedUrl = new URL(frontendUrl)
      if (parsedUrl.port) {
        frontendPort = parseInt(parsedUrl.port, 10)
      }
    } catch (e) {
      console.warn('[Vite] Failed to parse FRONTEND_URL port, using default port 3000', e)
    }
  }

  return {
    plugins: [react(), tailwindcss()],
    envDir: '../',
    define: {
      'process.env.BACKEND_URL': JSON.stringify(backendUrl),
      'process.env.COPILOT_URL': JSON.stringify(copilotUrl),
      'process.env.FRONTEND_URL': JSON.stringify(frontendUrl),
      'import.meta.env.VITE_MICROSOFT_CLIENT_ID': JSON.stringify(
        env.VITE_MICROSOFT_CLIENT_ID || env.MICROSOFT_CLIENT_ID || process.env.VITE_MICROSOFT_CLIENT_ID || process.env.MICROSOFT_CLIENT_ID || ''
      ),
      'import.meta.env.VITE_MICROSOFT_TENANT_ID': JSON.stringify(
        env.VITE_MICROSOFT_TENANT_ID || env.MICROSOFT_TENANT_ID || process.env.VITE_MICROSOFT_TENANT_ID || process.env.MICROSOFT_TENANT_ID || ''
      ),
      'import.meta.env.VITE_MICROSOFT_REDIRECT_URL': JSON.stringify(
        env.VITE_MICROSOFT_REDIRECT_URL || process.env.VITE_MICROSOFT_REDIRECT_URL || ''
      ),
      'import.meta.env.VITE_MICROSOFT_API_SCOPE': JSON.stringify(
        env.VITE_MICROSOFT_API_SCOPE || process.env.VITE_MICROSOFT_API_SCOPE || ''
      ),
    },
    server: {
      host: '0.0.0.0',
      port: frontendPort,
      proxy: {
        // Copilot service routes → port 8000
        '/api/copilot': {
          target: copilotUrl,
          changeOrigin: true,
          secure: false,
        },
        '/ws/copilot': {
          target: copilotUrl.replace('http', 'ws'),
          changeOrigin: true,
          ws: true,
        },        // Interview service routes → port 8000
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
        },
        '/ws': {
          target: backendUrl.replace('http', 'ws'),
          changeOrigin: true,
          ws: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      pool: 'forks',
      setupFiles: ['./src/test/setup.ts'],
    },
  }
})
