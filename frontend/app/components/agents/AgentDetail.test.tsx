/**
 * An agent's page: the definition is edited as a draft of a new version,
 * survives a look at another tab, and is read-only for those who do not
 * manage the agent.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hasFeature } from "~/api/about";
import { AGENT_TOKEN_LIFETIMES, agentsApi } from "~/api/agents";
import { ApiError } from "~/api/http";
import { agentSkills, skillsStatus } from "~/api/skills";
import { agentFixture, appSeenFixture, appsReviewFixture, toolReviewFixture, versionFixture } from "~/test/agentFixtures";
import { renderPage } from "~/test/renderPage";

import { AgentDetail } from "./AgentDetail";

vi.mock("~/api/agents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/agents")>();
  return {
    ...actual,
    agentsApi: {
      ...actual.agentsApi,
      get: vi.fn(),
      versions: vi.fn(),
      addVersion: vi.fn(),
      publish: vi.fn(),
      retire: vi.fn(),
      turnOff: vi.fn(),
      turnOn: vi.fn(),
      namePrincipals: vi.fn(),
      remove: vi.fn(),
      apps: vi.fn(),
    },
  };
});
vi.mock("~/api/chatClient", () => ({ chatApi: { listModels: vi.fn().mockResolvedValue([]) } }));
vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));
vi.mock("~/api/skills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("~/api/skills")>();
  return {
    ...actual,
    skillsStatus: vi.fn(),
    agentSkills: vi.fn().mockResolvedValue([]),
    listSkills: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 500 }),
  };
});

const api = vi.mocked(agentsApi);

function openAgent(tab = "definition") {
  renderPage(<AgentDetail slug="excel-assistant" basePath="/admin/agents" />, {
    path: "/admin/agents/:slug",
    initialPath: `/admin/agents/excel-assistant?tab=${tab}`,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  api.get.mockResolvedValue(agentFixture());
  api.versions.mockResolvedValue([versionFixture(1)]);
  api.apps.mockResolvedValue(appsReviewFixture());
  vi.mocked(hasFeature).mockResolvedValue(false);
  vi.mocked(skillsStatus).mockResolvedValue({ enabled: true });
});

describe("AgentDetail", () => {
  it("edits the latest version and saves the change as a new one", async () => {
    api.addVersion.mockResolvedValue(versionFixture(2));
    openAgent();

    const instructions = await screen.findByRole("textbox", { name: "Instructions" });
    expect(instructions).toHaveValue("Instructions v1");
    expect(screen.getByText(/Editing from version 1/)).toBeInTheDocument();

    await userEvent.clear(instructions);
    await userEvent.type(instructions, "Be brief.");
    expect(screen.getByText("Unsaved changes: Instructions")).toBeInTheDocument();

    api.get.mockResolvedValue(agentFixture({ latest_version: 2 }));
    api.versions.mockResolvedValue([versionFixture(2, { instructions: "Be brief." }), versionFixture(1)]);
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() =>
      expect(api.addVersion).toHaveBeenCalledWith("excel-assistant", expect.objectContaining({ instructions: "Be brief." })),
    );
    expect(api.publish).not.toHaveBeenCalled();
    // The new version is the one edited from now on, and v2 waits to be published.
    expect(await screen.findByText(/Editing from version 2/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish v2" })).toBeInTheDocument();
  });

  it("keeps an unsaved draft while another tab is open", async () => {
    openAgent();
    const instructions = await screen.findByRole("textbox", { name: "Instructions" });
    await userEvent.type(instructions, " More.");

    await userEvent.click(screen.getByRole("tab", { name: "Versions" }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Definition" }));

    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveValue("Instructions v1 More.");
  });

  it("saves whether conversations may change the PII mode as part of a new version", async () => {
    api.addVersion.mockResolvedValue(versionFixture(2, { pii: { allow_mode_override: false } }));
    openAgent();

    const select = await screen.findByRole("combobox", { name: "Changing the PII mode" });
    expect(select).toHaveTextContent("As the tenant's PII policy says");
    expect(screen.getByText(/Follows "Let chats and agents change the PII mode"/)).toBeInTheDocument();

    await userEvent.click(select);
    await userEvent.click(await screen.findByRole("option", { name: "Not allowed" }));
    expect(screen.getByText("Unsaved changes: Privacy")).toBeInTheDocument();
    expect(screen.getByText("Conversations always get the mode of the tenant's PII policy.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() =>
      expect(api.addVersion).toHaveBeenCalledWith(
        "excel-assistant",
        expect.objectContaining({ pii: { allow_mode_override: false } }),
      ),
    );
  });

  it("sets the agent's own sign-in lifetimes where the server takes them, as part of a new version", async () => {
    vi.mocked(hasFeature).mockImplementation(async (f) => f === AGENT_TOKEN_LIFETIMES);
    api.addVersion.mockResolvedValue(versionFixture(2));
    openAgent();

    const access = await screen.findByRole("spinbutton", { name: "Access token lifetime (s)" });
    expect(access).toHaveValue(null);
    await userEvent.type(access, "30");
    expect(screen.getByText("A sign-in lifetime must be at least 60 seconds.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save as new version" })).toBeDisabled();
    await userEvent.type(access, "0");
    expect(screen.getByText("Unsaved changes: Enterprise settings")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() =>
      expect(api.addVersion).toHaveBeenCalledWith(
        "excel-assistant",
        expect.objectContaining({ extensions: { token: { access_s: 300 } } }),
      ),
    );
  });

  it("offers no sign-in lifetimes where the server does not take them", async () => {
    openAgent();
    expect(await screen.findByRole("textbox", { name: "Instructions" })).toBeInTheDocument();
    expect(screen.queryByText("Sign-in lifetimes")).not.toBeInTheDocument();
  });

  it("is read-only for someone who does not manage the agent", async () => {
    api.get.mockResolvedValue(agentFixture({ can_manage: false }));
    openAgent();

    expect(await screen.findByText(/You can see this agent but not change it/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Retire" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("retires the agent after a confirmation", async () => {
    api.retire.mockResolvedValue(agentFixture({ status: "retired" }));
    openAgent();

    await userEvent.click(await screen.findByRole("button", { name: "Retire" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Retire Excel assistant?")).toBeInTheDocument();
    api.get.mockResolvedValue(agentFixture({ status: "retired" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Retire" }));

    await waitFor(() => expect(api.retire).toHaveBeenCalledWith("excel-assistant"));
    expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
  });

  describe("turning the agent off and on", () => {
    const OFF = { enabled: false, turned_off_at: "2026-11-16T09:30:00Z", turned_off_by: null };

    it("asks first, then turns the agent off and reloads it", async () => {
      api.turnOff.mockResolvedValue(agentFixture(OFF));
      openAgent();

      await userEvent.click(await screen.findByRole("button", { name: "Turn off" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText("Turn off Excel assistant?")).toBeInTheDocument();
      expect(within(dialog).getByText(/Your organisation turned this agent off/)).toBeInTheDocument();
      expect(api.turnOff).not.toHaveBeenCalled();
      api.get.mockResolvedValue(agentFixture(OFF));
      await userEvent.click(within(dialog).getByRole("button", { name: "Turn off" }));

      await waitFor(() => expect(api.turnOff).toHaveBeenCalledWith("excel-assistant"));
      expect(await screen.findByRole("button", { name: "Turn on" })).toBeInTheDocument();
      expect(screen.getByTestId("agent-off-notice")).toBeInTheDocument();
    });

    it("turns on without asking", async () => {
      api.get.mockResolvedValue(agentFixture(OFF));
      api.turnOn.mockImplementation(async () => {
        api.get.mockResolvedValue(agentFixture());
        return agentFixture();
      });
      openAgent();

      await userEvent.click(await screen.findByRole("button", { name: "Turn on" }));
      await waitFor(() => expect(api.turnOn).toHaveBeenCalledWith("excel-assistant"));
      expect(await screen.findByRole("button", { name: "Turn off" })).toBeInTheDocument();
      expect(screen.queryByTestId("agent-off-notice")).not.toBeInTheDocument();
    });

    it("shows the server's text when turning on is refused", async () => {
      const detail = "The agent stays off: the gateway could not end its grants from before. Try again in a moment.";
      api.get.mockResolvedValue(agentFixture(OFF));
      api.turnOn.mockRejectedValue(new ApiError(detail, 503));
      openAgent();

      await userEvent.click(await screen.findByRole("button", { name: "Turn on" }));
      expect(await screen.findByText(detail)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Turn on" })).toBeInTheDocument();
    });

    it("shows the Off chip and a warning with the time while it is off, for those who cannot manage it too", async () => {
      api.get.mockResolvedValue(agentFixture({ ...OFF, can_manage: false }));
      openAgent();

      const notice = await screen.findByTestId("agent-off-notice");
      expect(notice).toHaveTextContent(/Turned off .*2026.*\./);
      expect(notice).toHaveTextContent("Its apps are refused until it is turned on again; their sign-ins stay.");
      expect(notice).not.toHaveTextContent(" by ");
      expect(screen.getByText("Off")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on" })).not.toBeInTheDocument();
    });

    it("names who turned it off, when told", async () => {
      api.namePrincipals.mockResolvedValue([{ type: "user", id: "u-boss", label: "boss@plant.example" }]);
      api.get.mockResolvedValue(agentFixture({ ...OFF, turned_off_by: "u-boss" }));
      openAgent();

      expect(await screen.findByText(/by boss@plant\.example\./)).toBeInTheDocument();
    });

    it("has no Off chip or notice while it is on", async () => {
      openAgent();
      await screen.findByRole("button", { name: "Turn off" });
      expect(screen.queryByText("Off")).not.toBeInTheDocument();
      expect(screen.queryByTestId("agent-off-notice")).not.toBeInTheDocument();
    });

    it("offers neither button on a retired agent or to someone who cannot manage it", async () => {
      api.get.mockResolvedValue(agentFixture({ status: "retired" }));
      const { unmount } = renderPage(<AgentDetail slug="excel-assistant" basePath="/admin/agents" />, {
        path: "/admin/agents/:slug",
        initialPath: "/admin/agents/excel-assistant",
      });
      await screen.findByRole("button", { name: "Restore" });
      expect(screen.queryByRole("button", { name: "Turn off" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on" })).not.toBeInTheDocument();
      unmount();

      api.get.mockResolvedValue(agentFixture({ can_manage: false }));
      openAgent();
      await screen.findByText(/You can see this agent but not change it/);
      expect(screen.queryByRole("button", { name: "Turn off" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on" })).not.toBeInTheDocument();
    });
  });

  it("counts on the Apps tab what the gateway still holds back, and shows it there", async () => {
    api.apps.mockResolvedValue(
      appsReviewFixture({
        apps: [appSeenFixture()],
        pending: 2,
        live_pending: 2,
        tools: [toolReviewFixture("write_range"), toolReviewFixture("delete_sheet")],
      }),
    );
    openAgent();
    const tab = await screen.findByRole("tab", { name: /Apps/ });
    expect(await within(tab).findByLabelText("2 tool changes not live")).toBeInTheDocument();
    await userEvent.click(tab);
    expect(await screen.findByText("The apps bring 2 tool changes that version 1 does not have.")).toBeInTheDocument();
  });

  it("deletes a retired agent for good once its address is typed, and goes back to the list", async () => {
    api.get.mockResolvedValue(agentFixture({ status: "retired" }));
    api.remove.mockResolvedValue(undefined);
    renderPage(<AgentDetail slug="excel-assistant" basePath="/admin/agents" />, {
      path: "/admin/agents/:slug",
      initialPath: "/admin/agents/excel-assistant",
    });

    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Delete Excel assistant for good?")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Delete" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Type excel-assistant to confirm" }), "excel-assistant");
    await userEvent.click(confirm);

    await waitFor(() => expect(api.remove).toHaveBeenCalledWith("excel-assistant"));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Excel assistant" })).not.toBeInTheDocument());
  });

  it("offers deleting only on a retired agent, and only to a role with agents:delete", async () => {
    const open = (permissions: string[]) =>
      renderPage(<AgentDetail slug="excel-assistant" basePath="/admin/agents" />, {
        path: "/admin/agents/:slug",
        initialPath: "/admin/agents/excel-assistant",
        context: { permissions: new Set(permissions) },
      });

    // A live agent: retire first, whoever asks.
    const live = open(["agents:delete"]);
    expect(await screen.findByRole("button", { name: "Retire" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    live.unmount();

    // Retired, seen by its maintainer: restore, but no delete.
    api.get.mockResolvedValue(agentFixture({ status: "retired" }));
    const maintainer = open(["agents:maintain"]);
    expect(await screen.findByRole("button", { name: "Restore" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    maintainer.unmount();

    // Retired, seen by a role that may delete but does not maintain it.
    api.get.mockResolvedValue(agentFixture({ status: "retired", can_manage: false }));
    open(["agents:delete"]);
    expect(await screen.findByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
  });

  it("has a Skills tab, after Access, for those who manage the agent while skills are on", async () => {
    openAgent("skills");
    await waitFor(() => expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toContain("Skills"));
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Definition",
      "Versions",
      "Apps",
      "Access",
      "Skills",
      "Activity",
    ]);
    expect(await screen.findByText("For everyone in your organisation")).toBeInTheDocument();
    expect(agentSkills).toHaveBeenCalledWith("a-1");
  });

  it("has no Skills tab for those who only use the agent", async () => {
    api.get.mockResolvedValue(agentFixture({ can_manage: false }));
    openAgent();
    await screen.findByRole("tab", { name: "Access" });
    expect(screen.queryByRole("tab", { name: "Skills" })).not.toBeInTheDocument();
    expect(skillsStatus).not.toHaveBeenCalled();
  });

  it("has no Skills tab while the Skills module is off, or when the caller may not ask", async () => {
    vi.mocked(skillsStatus).mockResolvedValue({ enabled: false });
    openAgent();
    await waitFor(() => expect(skillsStatus).toHaveBeenCalled());
    await screen.findByRole("tab", { name: "Access" });
    expect(screen.queryByRole("tab", { name: "Skills" })).not.toBeInTheDocument();
  });
});
