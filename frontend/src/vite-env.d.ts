/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "Live records" polling interval in seconds; "0" disables it. See lib/config.ts. */
  readonly VITE_LIVE_REFRESH_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
