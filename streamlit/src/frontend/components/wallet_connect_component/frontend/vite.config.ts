import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  base: '', // generate relative asset URLs (avoids absolute /assets paths that break inside Streamlit components)
  plugins: [react()],
  build: {
    outDir: 'build',
    emptyOutDir: true
  },
  server: {
    port: 5173,
    strictPort: true
  }
})
