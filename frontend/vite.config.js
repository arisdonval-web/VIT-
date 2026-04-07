import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://localhost:5000'

  return {
    plugins: [react()],
    build: {
      cssMinify: false,
    },
    server: {
      port: 5173,
      proxy: {
        '/predict': backendUrl,
        '/history': backendUrl,
        '/health':  backendUrl,
        '/fetch':   backendUrl,
        '/odds':    backendUrl,
        '/admin':   backendUrl,
        '/results': backendUrl,
        '/system':  backendUrl,
      },
    },
  }
})
