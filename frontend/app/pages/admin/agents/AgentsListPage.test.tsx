/** The agents list: what each agent's users get, and creating one. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentsApi } from "~/api/agents";
import { agentFixture } from "~/test/agentFixtures";

import AgentsListPage from "./AgentsListPage";

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return { ...actual, agentsApi: { ...actual.agentsApi, list: vi.fn(), create: vi.fn() } };
});

const api = vi.mocked(agentsApi);

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
}

function renderList() {
  render(
    <MemoryRouter initialEntries={["/admin/agents"]}>
      <Routes>
        <Route path="/admin/agents" element={<AgentsListPage />} />
        <Route path="/admin/agents/:slug" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AgentsListPage", () => {
  it("shows each agent with its status and versions", async () => {
    api.list.mockResolvedValue([
      agentFixture(),
      agentFixture({ id: "a-2", slug: "word-writer", name: "Word writer", status: "draft", published_version: null, latest_version: 2 }),
    ]);
    renderList();

    expect(await screen.findByText("Excel assistant")).toBeInTheDocument();
    expect(screen.getByText("v1 live")).toBeInTheDocument();
    expect(screen.getByText("Word writer")).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("v2 not published")).toBeInTheDocument();
  });

  it("says so when there are none", async () => {
    api.list.mockResolvedValue([]);
    renderList();
    expect(await screen.findByText(/No agents yet/)).toBeInTheDocument();
  });

  it("creates an agent and opens its definition", async () => {
    api.list.mockResolvedValue([]);
    api.create.mockResolvedValue(agentFixture({ slug: "pp-helper", status: "draft" }));
    renderList();

    await userEvent.click(await screen.findByRole("button", { name: "New agent" }));
    await userEvent.type(screen.getByRole("textbox", { name: /Name/ }), "PP Helper");
    expect(screen.getByRole("textbox", { name: /Address/ })).toHaveValue("pp-helper");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/admin/agents/pp-helper?tab=definition"));
    expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({ slug: "pp-helper", name: "PP Helper", description: null }),
    );
  });
});
