// @vitest-environment node

import type { UserConfig } from "vite";
import { describe, expect, it } from "vitest";

import rendererConfig from "../vite.renderer.config";

describe("embeddable renderer build", () => {
  it("uses relative asset paths", () => {
    const config: UserConfig = rendererConfig;

    expect(config.base).toBe("./");
  });
});
