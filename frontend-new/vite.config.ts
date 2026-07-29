import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load environment variables from parent directory (root workspace folder)
  const env = loadEnv(mode, '../', '')
  const backendUrl = env.BACKEND_URL || 'http://localhost:8000'
  const copilotUrl = env.COPILOT_URL || 'http://localhost:8001'
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
    },
    server: {
      host: '0.0.0.0',
      port: frontendPort,
      proxy: {
        // Copilot service routes → port 8001
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
  }
})
