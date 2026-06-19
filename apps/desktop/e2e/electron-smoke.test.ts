import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it } from 'node:test'

const require = createRequire(import.meta.url)
const electronBinary = require('electron') as string
const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

describe('Electron desktop smoke', () => {
  it('starts the packaged main process and loads the renderer', async () => {
    const output = await runElectronSmoke()

    assert.equal(output.code, 0, output.stderr || output.stdout)
    assert.match(output.stdout, /AMADEUS_E2E_SMOKE renderer-ready/)
  })
})

function runElectronSmoke(): Promise<{ code: number | null, stdout: string, stderr: string }> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(electronBinary, ['--no-sandbox', '.'], {
      cwd: desktopRoot,
      env: {
        ...process.env,
        AMADEUS_E2E_SMOKE: '1',
        ELECTRON_ENABLE_LOGGING: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let stdout = ''
    let stderr = ''
    const timeout = setTimeout(() => {
      child.kill('SIGTERM')
      reject(new Error(`Electron smoke timed out\nstdout:\n${stdout}\nstderr:\n${stderr}`))
    }, 15000)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk: string) => {
      stderr += chunk
    })
    child.on('error', (error) => {
      clearTimeout(timeout)
      reject(error)
    })
    child.on('close', (code) => {
      clearTimeout(timeout)
      resolvePromise({ code, stdout, stderr })
    })
  })
}
