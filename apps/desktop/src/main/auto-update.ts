import { app } from 'electron'
import electronUpdater from 'electron-updater'

const { autoUpdater } = electronUpdater

export interface AutoUpdateOptions {
  enabled?: boolean
  feedUrl?: string
  logger?: (message: string) => void
}

function boolFromEnv(value: string | undefined): boolean {
  return value === '1' || value === 'true'
}

function logUpdate(logger: ((message: string) => void) | undefined, message: string): void {
  logger?.(`[auto-update] ${message}`)
}

export function configureAutoUpdates(options: AutoUpdateOptions = {}): boolean {
  const enabled = options.enabled ?? boolFromEnv(process.env.AMADEUS_AUTO_UPDATE)
  const feedUrl = (options.feedUrl ?? process.env.AMADEUS_UPDATE_FEED_URL ?? '').trim()
  const logger = options.logger

  if (!enabled) {
    logUpdate(logger, 'disabled')
    return false
  }
  if (!app.isPackaged) {
    logUpdate(logger, 'skipped outside packaged app')
    return false
  }
  if (!feedUrl) {
    logUpdate(logger, 'skipped because AMADEUS_UPDATE_FEED_URL is not configured')
    return false
  }

  autoUpdater.autoDownload = false
  autoUpdater.setFeedURL({
    provider: 'generic',
    url: feedUrl,
  })

  autoUpdater.on('checking-for-update', () => logUpdate(logger, 'checking'))
  autoUpdater.on('update-available', (info) => logUpdate(logger, `available ${info.version}`))
  autoUpdater.on('update-not-available', (info) => logUpdate(logger, `not available ${info.version}`))
  autoUpdater.on('error', (error) => logUpdate(logger, `error ${error.message}`))
  autoUpdater.on('download-progress', (progress) => logUpdate(logger, `download ${Math.round(progress.percent)}%`))
  autoUpdater.on('update-downloaded', (info) => logUpdate(logger, `downloaded ${info.version}`))

  void autoUpdater.checkForUpdates().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error)
    logUpdate(logger, `check failed ${message}`)
  })
  return true
}
