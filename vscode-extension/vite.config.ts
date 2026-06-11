import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  define: {
    'process.env.NODE_ENV': JSON.stringify('production')
  },
  build: {
    outDir: 'dist/webview',
    emptyOutDir: false,
    lib: {
      entry: 'src/webview/main.tsx',
      name: 'CodeAgentWebview',
      formats: ['iife'],
      fileName: () => 'main.js',
      cssFileName: 'style'
    },
    rollupOptions: {
      output: {
        assetFileNames: '[name][extname]'
      }
    }
  }
});
