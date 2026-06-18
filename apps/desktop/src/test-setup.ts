// Vitest/jsdom global setup. jsdom lacks a few browser APIs that components rely on; polyfill
// them with minimal no-op stubs so component tests render instead of throwing.

import { configure } from "@testing-library/react";

// Testing Library's waitFor defaults to a 1000 ms deadline. Under full-suite CPU contention,
// real-timer async work (e.g. useJobStatus polling) can momentarily exceed that, flaking tests
// that pass fine in isolation. Raise the async-utility timeout suite-wide: waitFor still resolves
// the instant its condition is met (no slowdown for passing tests) — only the failure deadline
// grows, so genuine successes under load stop being misreported as failures.
configure({ asyncUtilTimeout: 5000 });

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
