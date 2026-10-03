/** React Router's server build, as its Vite plugin resolves it (for lib/build.server.ts). */
declare module "virtual:react-router/server-build" {
  export const assets: { version: string };
}
