/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        // VITE_API_PROXY_TARGET 允许并行验证栈指向独立后端端口（默认 8000 不变）
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './tests/setup.ts',
    css: false,
    // 钉死测试时区（非 UTC）：本地时区契约（formatAbsoluteTime 等）在 UTC CI 上
    // 会与 UTC 原串手法不可区分，相关断言静默空转（合并裁定 F4）。
    env: { TZ: 'Asia/Shanghai' },
  },
});
