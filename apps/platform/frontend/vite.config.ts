import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

const workspaceRoot = fileURLToPath(new URL('../../../..', import.meta.url))
const reactPath = fileURLToPath(new URL('./node_modules/react/index.js', import.meta.url))
const reactJsxRuntimePath = fileURLToPath(new URL('./node_modules/react/jsx-runtime.js', import.meta.url))
const reactJsxDevRuntimePath = fileURLToPath(new URL('./node_modules/react/jsx-dev-runtime.js', import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = (
    env.VITE_DEV_PROXY_TARGET ||
    env.VITE_API_BASE_URL ||
    'http://127.0.0.1:8002'
  ).replace(/\/$/, '')

  return {
    plugins: [react()],
    resolve: {
      alias: [
        { find: 'react/jsx-dev-runtime', replacement: reactJsxDevRuntimePath },
        { find: 'react/jsx-runtime', replacement: reactJsxRuntimePath },
        { find: 'react', replacement: reactPath },
      ],
      dedupe: ['react', 'react-dom'],
    },
    server: {
      host: '0.0.0.0',
      port: 5172,
      fs: {
        allow: [workspaceRoot],
      },
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
