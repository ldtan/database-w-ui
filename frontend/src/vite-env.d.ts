/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "Live records" polling interval in seconds; "0" disables it. See lib/config.ts. */
  readonly VITE_LIVE_REFRESH_SECONDS?: string;
  /** Floor on the metric strip's own refetch interval, in seconds. See lib/config.ts. */
  readonly VITE_METRIC_REFRESH_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
