/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HAKO_MODE?: "mock" | "api";
  readonly VITE_HAKO_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
