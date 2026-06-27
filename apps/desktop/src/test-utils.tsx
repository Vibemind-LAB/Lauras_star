import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";

/**
 * A `QueryClientProvider` wrapper for tests — fresh client per call (no cross-test bleed),
 * retries off and `gcTime: 0` so failed queries fail fast and nothing lingers between tests.
 * Pass as `renderHook(fn, { wrapper: queryWrapper() })` or `render(ui, { wrapper: queryWrapper() })`.
 */
export function queryWrapper(): (props: { children: ReactNode }) => ReactElement {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }): ReactElement {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/**
 * `render` with a fresh QueryClientProvider already wrapped in. The returned `rerender` reuses
 * the same wrapper, so re-render tests work without re-supplying it.
 */
export function renderWithQuery(ui: ReactElement): RenderResult {
  return render(ui, { wrapper: queryWrapper() });
}
