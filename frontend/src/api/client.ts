export {
  type Drive,
  createCustomDrive,
  deleteDrive,
  getDrives,
  reloadDrives,
  subscribeDrive,
  unsubscribeDrive,
  updateDrive,
} from './drives'

export {
  type Job,
  type JobLogFile,
  type JobLogs,
  connectJobLogLiveWS,
  downloadJobLog,
  getJob,
  getJobLogContent,
  getJobLogs,
  getJobs,
  startIdarr,
  startIdarrSync,
  startSync,
  startSyncAll,
} from './jobs'

export {
  type LogEntry,
  clearLogs,
  getDebugStatus,
  getLogs,
  toggleDebug,
} from './logs'

export {
  type DatabaseStats,
  cleanupDatabase,
  downloadBackup,
  getDatabaseStats,
  uploadBackup,
} from './maintenance'

export {
  type DiscordNotificationConfig,
  type DiscordNotificationFeatureConfig,
  type MakerIdarrCacheStats,
  type MakerIdarrConfig,
  type MakerIdarrIgnoredItem,
  type MakerIdarrLastRun,
  type MakerIdarrPendingCandidate,
  type MakerIdarrPendingItem,
  type MakerIdarrResolutionEvent,
  type MakerIdarrSyncTarget,
  type PlexLibrary,
  type PlexLibraryConfig,
  addMakerIdarrIgnoredTitle,
  checkSetupComplete,
  clearMakerIdarrPendingMatches,
  getDiscordNotificationConfig,
  getMakerIdarrCacheStats,
  getMakerIdarrConfig,
  getMakerIdarrIgnoredTitles,
  getMakerIdarrLastRun,
  getMakerIdarrPendingCandidates,
  getMakerIdarrPendingCount,
  getMakerIdarrPendingMatches,
  getPlexLibraries,
  getPlexLibraryConfigs,
  getSettings,
  importMakerIdarrIgnoredTitles,
  removeMakerIdarrIgnoredTitle,
  replaceMakerIdarrIgnoredTitles,
  revealSensitiveSetting,
  resolveMakerIdarrPendingMatch,
  revertMakerIdarrLatestRun,
  reviewMakerIdarrPendingCandidate,
  runMakerIdarrCacheMaintenance,
  saveBulkSettings,
  saveDiscordNotificationConfig,
  saveMakerIdarrConfig,
  savePlexLibraryConfig,
  saveSettings,
  getGdriveStoragePath,
  saveGdriveStoragePath,
  testDiscordNotification,
  testPlex,
  testRadarr,
  testSonarr,
  uploadMakerIdarrFiles,
  uploadServiceAccountJson,
} from './settings'

export {
  type PosterActivityStats,
  type RecentSyncedPoster,
  type Schedule,
  type Stats,
  createSchedule,
  deleteSchedule,
  getPosterActivityStats,
  getRecentSyncedPosters,
  getSchedules,
  getStats,
  updateSchedule,
} from './schedules'

export {
  type FlowConfig,
  type FlowResult,
  type IdarrFlowJobConfig,
  type PosterConfig,
  type PosterSearchItem,
  type TmdbCandidate,
  type UnmatchedStats,
  getDrivePriority,
  getFlowConfig,
  getPosterConfig,
  getUnmatchedStats,
  runBorderReplacer,
  runFlow,
  saveDrivePriority,
  saveFlowConfig,
  savePosterConfig,
  searchPosters,
  searchUnmatchedTmdb,
  startPosterRename,
  startUnmatchedDetection,
} from './posterManager'

export {
  type PlexUploadCacheSummary,
  type PlexUploadLibraryConfig,
  type PlexSearchItem,
  type PlexWebhookDedupeEntry,
  type PlexWebhookStats,
  clearPlexUploadCache,
  clearPlexWebhookDedupe,
  downloadPlexUploadCacheExport,
  getPlexManualSettings,
  getPlexUploadCache,
  getPlexUploadCacheEntries,
  getPlexUploadLibraryOverrideSettings,
  getPlexWebhookDedupeEntries,
  getPlexWebhookSettings,
  getPlexWebhookStats,
  resetPlexWebhookStats,
  runPlexSingleUpload,
  runPlexUpload,
  savePlexManualSettings,
  savePlexUploadLibraryOverrideSettings,
  savePlexWebhookSettings,
  searchPlexItems,
} from './plexUpload'

export {
  type MakerMonitorConfig,
  type MakerMonitorRunQueuedResponse,
  type MakerMonitorRunResponse,
  type PsdExportRequest,
  type TmdbImage,
  type TmdbImagesResponse,
  type TmdbSearchFilter,
  type TmdbSearchResult,
  type TmdbSeasonInfo,
  type TmdbTvDetails,
  type PsdExportResult,
  type PsdExportNotFound,
  type PosterAvailability,
  type PosterStyleEntry,
  exportToPsd,
  uploadPsdToExportFolder,
  checkPsdExists,
  checkTmdbPosterAvailability,
  getMakerMonitorConfig,
  getMakerMonitorLastResult,
  getSeasonImages,
  getTmdbImages,
  getTmdbImageProxyUrl,
  getTvDetails,
  runMakerMonitor,
  saveMakerMonitorConfig,
  searchTmdb,
} from './makerTools'

export {
  type CommunityRequest,
  type SubmitRequestPayload,
  type SubmitRequestResponse,
  getCommunityRequests,
  getCommunityRequestCount,
  submitCommunityRequest,
} from './community'

export const getApiErrorMessage = (error: unknown, fallback: string): string => {
  const maybeError = error as {
    response?: { data?: { detail?: string } }
    message?: string
  }

  const detail = maybeError?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  const message = maybeError?.message
  if (typeof message === 'string' && message.trim()) {
    return message
  }

  return fallback
}

// Helper to get WebSocket URL from API URL
export const getWebSocketUrl = () => {
  // Always recalculate at runtime to avoid stale values
  const apiUrl = import.meta.env.VITE_API_URL || window.location.origin
  const url = new URL(apiUrl)
  const protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${url.host}/api/jobs/ws`
  return wsUrl
}

const JOB_TYPE_LABELS: Record<string, string> = {
  gdrive_sync: 'Drive Sync',
  sync: 'Drive Sync',
  sync_all: 'Drive Sync (All)',
  poster_workflow: 'Poster Workflow',
  poster_renamer: 'Poster Renamer',
  idarr: 'IDarr',
  maker_monitor: 'Maker Monitor',
  unmatched_assets: 'Unmatched Assets',
  border_replacer: 'Border Replacer',
}

export const formatJobType = (jobType: string): string =>
  JOB_TYPE_LABELS[jobType] ?? jobType