import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: env.VITE_HAKO_PROXY_TARGET || "http://127.0.0.1:8080",
          changeOrigin: false,
        },
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
    },
  };
});
