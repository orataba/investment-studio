import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    resolve: { alias: [
      { find: 'react/jsx-dev-runtime', replacement: fileURLToPath(new URL('./node_modules/react/jsx-dev-runtime.js', import.meta.url)) },
      { find: 'react/jsx-runtime', replacement: fileURLToPath(new URL('./node_modules/react/jsx-runtime.js', import.meta.url)) },
      { find: 'react', replacement: fileURLToPath(new URL('./node_modules/react/index.js', import.meta.url)) },
    ], dedupe: ['react', 'react-dom'] },
    server: { host: '127.0.0.1', port: 5175, fs: { allow: [fileURLToPath(new URL('../../..', import.meta.url))] },
      proxy: { '/api': { target: env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8010', changeOrigin: true } } },
  }
})
