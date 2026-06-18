// Vitest/jsdom global setup. jsdom lacks a few browser APIs that components rely on; polyfill
// them with minimal no-op stubs so component tests render instead of throwing.

// ResizeObserver — used by CaptionPreview to measure its box; jsdom does not implement it.
if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
}

// Object-URL helpers — components (e.g. CaptionPreview, thumbnails) create/revoke blob URLs for
// <img> sources; jsdom implements neither. No-op stubs keep effect cleanup from throwing.
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:stub";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}
