import { resolve } from 'node:path'

import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

const rootDir = resolve(__dirname, '../..')

export default defineConfig({
  main: {
    envDir: rootDir,
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, 'src/main/index.ts'),
      },
    },
  },
  preload: {
    envDir: rootDir,
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, 'src/preload/index.ts'),
      },
    },
  },
  renderer: {
    envDir: rootDir,
    build: {
      rollupOptions: {
        input: {
          companion: resolve(__dirname, 'src/renderer/companion/index.html'),
        },
      },
    },
  },
})
