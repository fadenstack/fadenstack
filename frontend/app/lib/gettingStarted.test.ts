import { beforeEach, describe, expect, it, vi } from "vitest";

// Every step reads the server; these stand in for it.
const sessions = vi.fn<() => Promise<unknown[]>>();
const prefs = vi.fn<() => Promise<{ preferences: Record<string, unknown> }>>();

vi.mock("~/api/chatClient", () => ({ chatApi: { listSessions: () => sessions() } }));
vi.mock("~/api/preferences", () => ({ preferences: { get: () => prefs() } }));
vi.mock("~/api/admin", () => ({ adminUsers: { list: async () => [], listMyApiTokens: async () => [] } }));
vi.mock("~/api/agents", () => ({ agentsApi: { list: async () => [] } }));
vi.mock("~/api/inference", () => ({ inferenceApi: { listEnvironments: async () => [], listDeployments: async () => [] } }));
vi.mock("~/api/llm", () => ({ providers: { list: async () => [] } }));
vi.mock("~/api/mcp", () => ({ listServers: async () => [] }));
vi.mock("~/api/nodes", () => ({ nodesApi: { list: async () => [] } }));
vi.mock("~/api/pii", () => ({ listPIIPolicyTenants: async () => [] }));
vi.mock("~/api/skills", () => ({ listSkills: async () => [] }));

const { loadChecklistState } = await import("./gettingStarted");
const noModules = () => false;

describe("Try it in the chat", () => {
  beforeEach(() => {
    sessions.mockReset();
    prefs.mockResolvedValue({ preferences: {} });
  });

  it("ticks once the person has a chat, however they got there", async () => {
    // On .61 it stayed open after two chats: only the list's own button ticked it.
    sessions.mockResolvedValue([{ id: "s1" }]);
    expect((await loadChecklistState(noModules)).state.chat).toBe(true);
  });

  it("stays open without a chat", async () => {
    sessions.mockResolvedValue([]);
    expect((await loadChecklistState(noModules)).state.chat).toBe(false);
  });

  it("still ticks from the list's button, and when the chats cannot be read", async () => {
    sessions.mockRejectedValue(new Error("API 403"));
    prefs.mockResolvedValue({ preferences: { getting_started: { chat_opened: true } } });
    expect((await loadChecklistState(noModules)).state.chat).toBe(true);
  });
});
