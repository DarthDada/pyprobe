import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 产物直出 pyprobe/web/static/ 并入库（FE3：npm run build 后 git diff 须为空）。
// 布局对齐 web/app.py 的伺服契约（WA1）：index.html 在根、静态资源在 assets/。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../pyprobe/web/static',
    emptyOutDir: true,
  },
  server: {
    // 开发态：vite dev server 代理到 `pyprobe web` 的后端（默认端口，WC1）。
    proxy: {
      '/api': 'http://127.0.0.1:9470',
    },
  },
})
