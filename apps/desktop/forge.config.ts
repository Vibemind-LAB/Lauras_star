import { MakerSquirrel } from "@electron-forge/maker-squirrel";
import { MakerZIP } from "@electron-forge/maker-zip";
import { VitePlugin } from "@electron-forge/plugin-vite";
import type { ForgeConfig } from "@electron-forge/shared-types";

const config: ForgeConfig = {
  packagerConfig: {
    asar: true,
    name: "Laura",
    executableName: process.platform === "linux" ? "laura" : "Laura",
    appBundleId: "ai.laura.desktop",
    // To bundle the standalone Python service + ffmpeg/libmpv, add e.g.
    //   extraResource: ["../../dist/service", "../../dist/ffmpeg"]
    // once those are built. See docs/13-packaging.md. (Left unset so an
    // unsigned `forge package` works without prebuilt binaries.)
  },
  rebuildConfig: {},
  makers: [new MakerSquirrel({}), new MakerZIP({}, ["darwin", "win32", "linux"])],
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
