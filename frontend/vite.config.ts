import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

const apiProxyTarget =
  (process.env.VITE_API_PROXY_TARGET as string | undefined) ??
  "http://127.0.0.1:8000";

// This machine only by default: the dev backend signs anyone in as the
// administrator (dev-login), and the dev server proxies /api to it. For a
// dev box reached over a network you trust, `faden dev up --lan` passes
// --host 0.0.0.0; VITE_HOST sets it too.
const devHost = (process.env.VITE_HOST as string | undefined) ?? "127.0.0.1";

export default defineConfig({
  // The licences of the packages bundled into the console, served at /third-party-licenses.md
  // (the minified files keep none); public/licenses/supplement.md covers what Vite cannot see.
  build: { license: { fileName: "third-party-licenses.md" } },
  plugins: [tailwindcss(), reactRouter(), tsconfigPaths()],
  server: {
    host: devHost,
    port: 5173,
    proxy: {
      // Forward all /api requests to the FastAPI backend in dev
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        // The live log stream is a WebSocket. changeOrigin rewrites Host, so the backend
        // learns the page's own host from X-Forwarded-Host to check the Origin against.
        ws: true,
        configure: (proxy) => {
          const forwardHost = (proxyReq: any, req: any) => {
            if (req.headers?.host) proxyReq.setHeader("x-forwarded-host", req.headers.host);
          };
          proxy.on("proxyReq", forwardHost);
          proxy.on("proxyReqWs", forwardHost);
        },
      },
    },
  },
});
