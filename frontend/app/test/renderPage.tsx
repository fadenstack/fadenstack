/** Render a page component inside a memory router, at a chosen path. */
import type { ReactElement } from "react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import { render, type RenderResult } from "@testing-library/react";

export interface RenderPageOptions {
  /** The route pattern the page is registered under, e.g. `/x/:id`. */
  path?: string;
  /** The URL to start at. Defaults to `path` with no params substituted. */
  initialPath?: string;
  /** What the layout around the page passes down, e.g. `{ permissions }` for `useCan`. None: everything is allowed. */
  context?: unknown;
}

export function renderPage(
  element: ReactElement,
  { path = "/", initialPath = path, context }: RenderPageOptions = {},
): RenderResult {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<Outlet context={context} />}>
          <Route path={path} element={element} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}
