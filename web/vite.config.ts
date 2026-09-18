import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 独立前端服务：dev 时代理 /api 到本地后端；生产由 nginx 反代（见 nginx.conf）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
  },
});
