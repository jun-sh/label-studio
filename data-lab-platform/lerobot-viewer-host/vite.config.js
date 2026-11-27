import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const PLATFORM = process.env.DATALAB_PLATFORM_URL || 'http://10.10.10.34:8080';

export default defineConfig({
  // Served by lerobot-studio/server.mjs from ROOT/next (see serveNextEngine).
  base: '/lerobot/next/',
  plugins: [react(), tailwindcss()],
  // Pre-bundling rewrites import.meta.url and breaks the package's Parquet worker URLs (dev only).
  optimizeDeps: { exclude: ['@ioai/lerobot-studio'] },
  server: { proxy: { '/lerobot/api': { target: PLATFORM, changeOrigin: true } } },
});
