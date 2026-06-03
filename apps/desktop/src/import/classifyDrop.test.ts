import { describe, expect, it } from "vitest";

import { classifyDrop, type DropInput } from "./classifyDrop";

const input = (over: Partial<DropInput>): DropInput => ({
  filePaths: [],
  directoryPaths: [],
  uriText: "",
  ...over,
});

describe("classifyDrop", () => {
  it("classifies dropped files", () => {
    expect(classifyDrop(input({ filePaths: ["/a/x.mp4", "/a/y.mov"] }))).toEqual([
      { kind: "file", path: "/a/x.mp4" },
      { kind: "file", path: "/a/y.mov" },
    ]);
  });
  it("classifies a directory", () => {
    expect(classifyDrop(input({ directoryPaths: ["/a/folder"] }))).toEqual([
      { kind: "folder", path: "/a/folder" },
    ]);
  });
  it("classifies an http(s) url from uri-list", () => {
    expect(classifyDrop(input({ uriText: "https://x/y.mp4\r\n" }))).toEqual([
      { kind: "url", url: "https://x/y.mp4" },
    ]);
  });
  it("classifies a magnet url", () => {
    expect(classifyDrop(input({ uriText: "magnet:?xt=urn:btih:abc" }))).toEqual([
      { kind: "url", url: "magnet:?xt=urn:btih:abc" },
    ]);
  });
  it("ignores non-url text", () => {
    expect(classifyDrop(input({ uriText: "just text" }))).toEqual([]);
  });
  it("prefers files over text when both present", () => {
    expect(classifyDrop(input({ filePaths: ["/a/x.mp4"], uriText: "https://x/y" }))).toEqual([
      { kind: "file", path: "/a/x.mp4" },
    ]);
  });
});
