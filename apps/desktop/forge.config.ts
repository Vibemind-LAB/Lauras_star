import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { MakerSquirrel } from "@electron-forge/maker-squirrel";
import { MakerZIP } from "@electron-forge/maker-zip";
import { VitePlugin } from "@electron-forge/plugin-vite";
import type { ForgeConfig } from "@electron-forge/shared-types";

// Frozen Python service built by PyInstaller (see services/local-api/packaging).
// Bundled as an extraResource named "service" so the packaged app finds it at
// resources/service/laura-api.exe (matches resolveServiceCommand in src/service.ts).
// Only attached when present, so a plain `forge package` without a prebuilt
// service still works.
const servicePath = resolve(__dirname, "..", "..", "dist", "service");
// Static ffmpeg/ffprobe binaries, bundled as resources/ffmpeg/*.exe. The
// backend is pointed at them via LAURA_FFMPEG/LAURA_FFPROBE in src/service.ts.
const ffmpegPath = resolve(__dirname, "..", "..", "dist", "ffmpeg");
// Only attach the resources that actually exist on disk (empty => omit the key).
const extraResource = [servicePath, ffmpegPath].filter(existsSync);

const config: ForgeConfig = {
  packagerConfig: {
    asar: true,
    name: "Laura",
    executableName: process.platform === "linux" ? "laura" : "Laura",
    appBundleId: "ai.laura.desktop",
    ...(extraResource.length > 0 ? { extraResource } : {}),
  },
  rebuildConfig: {},
  makers: [
    // `name` is set explicitly: the workspace package is scoped (@laura/desktop),
    // and Squirrel would otherwise derive a ".nuspec" path containing the "@laura/"
    // segment, which nuget cannot open on Windows (ENOENT). A plain name avoids it.
    new MakerSquirrel({
      name: "Laura",
      setupExe: "Laura-Setup.exe",
      // nuget requires <authors> in the generated .nuspec; package.json has no
      // "author" field, so provide it here (and owners, which defaults to it).
      authors: "Laura",
      owners: "Laura",
    }),
    new MakerZIP({}, ["darwin", "win32", "linux"]),
  ],
  plugins: [
    new VitePlugin({
      build: [
        { entry: "src/main.ts", config: "vite.main.config.ts", target: "main" },
        { entry: "src/preload.ts", config: "vite.preload.config.ts", target: "preload" },
      ],
      renderer: [{ name: "main_window", config: "vite.renderer.config.ts" }],
    }),
  ],
};

export default config;
