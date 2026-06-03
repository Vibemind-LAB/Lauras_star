import { describe, expect, it } from "vitest";

import { formatBytes, formatEta, formatSpeed } from "./format";

describe("format helpers", () => {
  it("formats bytes in binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(30 * 1024 ** 3)).toBe("30.0 GiB");
  });
  it("formats speed per second", () => {
    expect(formatSpeed(5 * 1024 ** 2)).toBe("5.0 MiB/s");
    expect(formatSpeed(null)).toBe("");
  });
  it("formats eta as m:ss / h:mm:ss", () => {
    expect(formatEta(0)).toBe("0:00");
    expect(formatEta(75)).toBe("1:15");
    expect(formatEta(3661)).toBe("1:01:01");
    expect(formatEta(null)).toBe("");
  });
});
