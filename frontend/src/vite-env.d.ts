/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "Live records" polling interval in seconds; "0" disables it. See lib/config.ts. */
  readonly VITE_LIVE_REFRESH_SECONDS?: string;
  /** Floor on the metric strip's own refetch interval, in seconds. See lib/config.ts. */
  readonly VITE_METRIC_REFRESH_SECONDS?: string;
  /** Floor on the record count's own refetch interval, in seconds. See lib/config.ts. */
  readonly VITE_COUNT_REFRESH_SECONDS?: string;
  /** Live records a table may hold before archiving is suggested; "0" never suggests it. See lib/config.ts. */
  readonly VITE_ARCHIVE_HINT_THRESHOLD?: string;
  /** "true" serves a public home page at `/`, and the guide with it. See lib/config.ts. */
  readonly VITE_HOME_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
