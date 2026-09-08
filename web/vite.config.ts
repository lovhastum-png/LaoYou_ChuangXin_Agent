import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 可连接源码后端（8000）或便携包（18080），HTTP与信令使用同一服务。
const backend = process.env.LAOYOU_DEV_BACKEND || 'http://localhost:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
      },
      '/ws': {
        target: backend.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
