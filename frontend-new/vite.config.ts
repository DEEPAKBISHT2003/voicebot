import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load environment variables from parent directory (root workspace folder)
  const env = loadEnv(mode, '../', '')
  const backendUrl = env.BACKEND_URL || 'http://localhost:8000'
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
      'process.env.FRONTEND_URL': JSON.stringify(frontendUrl),
    },
    server: {
      host: '0.0.0.0',
      port: frontendPort,
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
