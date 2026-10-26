/**
 * The sign-in providers page: the module's providers, adding and changing one, and no module at all; with
 * Enterprise, provisioning (SCIM) for the identity providers.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { hasFeature } from "~/api/about";
import * as admin from "~/api/admin";
import { SCIM_PROVISIONING, scimConnectionsApi, type ScimConnection } from "~/api/scimConnections";
import { ProviderApiError, signInProviders, type SignInProvider } from "~/api/signInProviders";
import { renderPage } from "~/test/renderPage";

import AuthProvidersPage from "./AuthProvidersPage";

vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));

let list: MockInstance<typeof signInProviders.list>;
let create: MockInstance<typeof signInProviders.create>;
let update: MockInstance<typeof signInProviders.update>;

const STORED: SignInProvider = {
  id: "p1", key: "corp-ad", name: "Corp AD", protocol: "ldap", enabled: true,
  settings: {
    protocol: "ldap", url: "ldaps://dc1.example.com", start_tls: false, ca_bundle: null, bind_dn: "svc@example.com",
    base_dn: "DC=example,DC=com", preset: "ad", user_filter: null, subject_attribute: null, email_attribute: null,
    name_attribute: null, group_filter: null, group_base_dn: null, disabled_filter: null, email_verified_by_idp: true,
    timeout_s: 10, check_people: true,
  },
  policy: {
    create_account: true, default_role_ids: [], allowed_domains: [], manage_groups: false, group_rules: [],
    session_lifetime_s: 43_200, inactive_days: 45, sso_only: false,
  },
  secrets_set: ["bind_password"], created_at: "", updated_at: "",
};

/** A test that types through the whole provider dialog: ~2 s alone, past the default 20 s in a loaded full run. */
const FILLS_THE_DIALOG = 60_000;

const ENTRA: SignInProvider = {
  ...STORED, id: "p2", key: "entra", name: "Entra ID", protocol: "oidc",
  settings: {
    protocol: "oidc", issuer: "https://login.example.com/v2.0", client_id: "app-1",
    redirect_uri: "https://ai.example.internal/api/auth/sso/entra/callback", scopes: ["openid", "email"],
    token_auth_method: "client_secret_basic", userinfo: "when_missing", email_claim: "email",
    email_verified_claim: "email_verified", name_claim: "name", groups_claim: "groups", max_id_token_age_s: 300,
    ca_bundle: null,
  },
  secrets_set: ["client_secret"],
};

const CONNECTION: ScimConnection = {
  provider_key: "entra", base_url: "https://ai.example.internal/api/ee/scim/v2/entra", token_hint: "x9Qa",
  created_at: "2026-10-26T08:00:00Z", token_made_at: "2026-10-26T08:00:00Z", last_push_at: null,
  last_error: null, last_error_at: null,
};

beforeEach(() => {
  vi.mocked(hasFeature).mockResolvedValue(false);
  list = vi.spyOn(signInProviders, "list").mockResolvedValue([STORED]);
  create = vi.spyOn(signInProviders, "create");
  update = vi.spyOn(signInProviders, "update");
  vi.spyOn(admin.adminUsers, "listRoles").mockResolvedValue([]);
  vi.spyOn(admin.adminGroups, "list").mockResolvedValue([]);
});

function field(name: RegExp | string) {
  return within(screen.getByTestId("provider-dialog")).getByRole("textbox", { name });
}

describe("the sign-in providers page", () => {
  it("lists the module's providers by name, key and kind", async () => {
    renderPage(<AuthProvidersPage />);
    expect(await screen.findByText("Corp AD")).toBeInTheDocument();
    expect(screen.getByText("corp-ad")).toBeInTheDocument();
    expect(screen.getByText("Directory (LDAP, Active Directory)")).toBeInTheDocument();
  });

  it("says so when no sign-in module is switched on", async () => {
    list.mockRejectedValue(new ProviderApiError(404, "No sign-in module is switched on"));
    renderPage(<AuthProvidersPage />);
    expect(await screen.findByTestId("sign-in-module-off")).toHaveTextContent("No sign-in module is switched on");
    expect(screen.queryByRole("button", { name: "Add provider" })).not.toBeInTheDocument();
  });

  it("adds an OpenID Connect provider with this server's callback", async () => {
    create.mockResolvedValue({ ...STORED, key: "example-corp" });
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add provider" }));

    fireEvent.change(field(/^Name/), { target: { value: "Example Corp" } });
    expect(field(/^Key/)).toHaveValue("example-corp");
    expect(field(/^Server address/)).toHaveValue(window.location.origin);
    expect(screen.getByDisplayValue(`${window.location.origin}/api/auth/sso/example-corp/callback`)).toBeInTheDocument();
    fireEvent.change(field(/^Server address/), { target: { value: "https://ai.example.internal" } });
    expect(screen.getByDisplayValue("https://ai.example.internal/api/auth/sso/example-corp/callback")).toBeInTheDocument();
    expect(screen.getByTestId("provider-missing")).toHaveTextContent("the issuer");

    fireEvent.change(field(/^Issuer/), { target: { value: "https://login.example.com/v2.0" } });
    fireEvent.change(field(/^Client ID/), { target: { value: "app-1" } });
    fireEvent.change(screen.getByLabelText(/^Client secret/), { target: { value: "s3cret" } });
    expect(screen.queryByTestId("provider-missing")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({
      key: "example-corp", name: "Example Corp",
      settings: {
        protocol: "oidc", issuer: "https://login.example.com/v2.0", client_id: "app-1",
        redirect_uri: "https://ai.example.internal/api/auth/sso/example-corp/callback",
      },
      secrets: { client_secret: "s3cret" },
      policy: { create_account: true, session_lifetime_s: 43_200, inactive_days: 45 },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(list).toHaveBeenCalledTimes(2);
  }, FILLS_THE_DIALOG);

  it("shows what the module refused, as it said it, and keeps the dialog open", async () => {
    create.mockRejectedValue(new ProviderApiError(422, [{ loc: ["body", "key"], msg: "String should match pattern" }]));
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    fireEvent.change(field(/^Name/), { target: { value: "Corp" } });
    fireEvent.change(field(/^Issuer/), { target: { value: "https://login.example.com" } });
    fireEvent.change(field(/^Client ID/), { target: { value: "app" } });
    fireEvent.change(screen.getByLabelText(/^Client secret/), { target: { value: "s" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add provider" }));
    expect(await screen.findByTestId("provider-refused")).toHaveTextContent("key: String should match pattern");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("changes a directory without sending its password again", async () => {
    update.mockResolvedValue(STORED);
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change Corp AD" }));
    expect(screen.getByText("Leave empty to keep the one that is set")).toBeInTheDocument();
    expect(field(/^Key/)).toBeDisabled();
    fireEvent.change(field(/^Where people are searched/), { target: { value: "OU=Staff,DC=example,DC=com" } });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const [key, body] = update.mock.calls[0];
    expect(key).toBe("corp-ad");
    expect(body).not.toHaveProperty("key");
    expect(body).not.toHaveProperty("secrets");
    expect(body.settings).toMatchObject({ base_dn: "OU=Staff,DC=example,DC=com", timeout_s: 10, email_verified_by_idp: true });
  });

  it("names the chat role for a new provider's people, and nothing else", async () => {
    const chat = { id: "r-chat", name: "default_user" } as admin.RbacRole;
    vi.spyOn(admin.adminUsers, "listRoles").mockResolvedValue([{ id: "r-admin", name: "admin" } as admin.RbacRole, chat]);
    create.mockResolvedValue({ ...STORED, key: "example-corp" });
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add provider" }));
    fireEvent.change(field(/^Name/), { target: { value: "Example Corp" } });
    fireEvent.change(field(/^Issuer/), { target: { value: "https://login.example.com/v2.0" } });
    fireEvent.change(field(/^Client ID/), { target: { value: "app-1" } });
    fireEvent.change(screen.getByLabelText(/^Client secret/), { target: { value: "s3cret" } });
    expect(field(/^Claim that names the person/)).toHaveValue("sub");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add provider" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0].policy.default_role_ids).toEqual(["r-chat"]);
    expect(create.mock.calls[0][0].settings).toMatchObject({ subject_claim: "sub" });
  }, FILLS_THE_DIALOG);

  it("warns of a directory without TLS", async () => {
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change Corp AD" }));
    fireEvent.change(field(/^Directory address/), { target: { value: "ldap://dc1.example.com" } });
    expect(screen.getByText(/Without TLS, passwords cross the network readable/)).toBeInTheDocument();
  });
});

describe("provisioning (Enterprise)", () => {
  let connections: MockInstance<typeof scimConnectionsApi.list>;
  let makeToken: MockInstance<typeof scimConnectionsApi.makeToken>;
  let removeConnection: MockInstance<typeof scimConnectionsApi.remove>;

  beforeEach(() => {
    vi.mocked(hasFeature).mockImplementation(async (feature) => feature === SCIM_PROVISIONING);
    list.mockResolvedValue([STORED, ENTRA]);
    connections = vi.spyOn(scimConnectionsApi, "list").mockResolvedValue([]);
    makeToken = vi.spyOn(scimConnectionsApi, "makeToken");
    removeConnection = vi.spyOn(scimConnectionsApi, "remove").mockResolvedValue(undefined);
  });

  const dialogs = () => screen.getAllByRole("dialog");
  const confirm = (name: string) =>
    fireEvent.click(within(dialogs()[dialogs().length - 1]).getByRole("button", { name }));

  it("is not offered without the feature", async () => {
    vi.mocked(hasFeature).mockResolvedValue(false);
    renderPage(<AuthProvidersPage />);
    expect(await screen.findByText("Entra ID")).toBeInTheDocument();
    expect(screen.queryByText("Provisioning")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Provisioning: Entra ID" })).not.toBeInTheDocument();
    expect(connections).not.toHaveBeenCalled();
  });

  it("is offered for identity providers, not for directories", async () => {
    renderPage(<AuthProvidersPage />);
    expect(await screen.findByRole("button", { name: "Provisioning: Entra ID" })).toBeInTheDocument();
    expect(screen.getByText("Provisioning")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Provisioning: Corp AD" })).not.toBeInTheDocument();
  });

  it("switches on and shows the token once, with the address to give the provider", async () => {
    makeToken.mockResolvedValue({ provider_key: "entra", base_url: CONNECTION.base_url, token: "fscim_secret-x9Qa" });
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Provisioning: Entra ID" }));
    expect(screen.getByText(/Provisioning is off/)).toBeInTheDocument();

    connections.mockResolvedValue([CONNECTION]);
    fireEvent.click(screen.getByRole("button", { name: "Switch provisioning on" }));
    await waitFor(() => expect(makeToken).toHaveBeenCalledWith("entra"));
    expect(await screen.findByTestId("provisioning-token")).toHaveTextContent("it is shown only this once");
    expect(screen.getByDisplayValue("fscim_secret-x9Qa")).toBeInTheDocument();
    expect(screen.getByDisplayValue(CONNECTION.base_url)).toBeInTheDocument();
    expect(await screen.findByTestId("provisioning-state")).toHaveTextContent("Ends in x9Qa");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(await screen.findByRole("button", { name: "Provisioning: Entra ID" }));
    expect(screen.queryByDisplayValue("fscim_secret-x9Qa")).not.toBeInTheDocument();
    expect(screen.getByTestId("provisioning-state")).toHaveTextContent("Ends in x9Qa");
  });

  it("replaces the token only after asking, and says the old one stopped", async () => {
    connections.mockResolvedValue([
      { ...CONNECTION, last_push_at: "2026-10-26T09:00:00Z", last_error: "409 UniquenessException", last_error_at: "2026-10-26T09:00:00Z" },
    ]);
    makeToken.mockResolvedValue({ provider_key: "entra", base_url: CONNECTION.base_url, token: "fscim_new" });
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Provisioning: Entra ID" }));
    expect(screen.getByTestId("provisioning-state")).toHaveTextContent("409 UniquenessException");

    fireEvent.click(screen.getByRole("button", { name: "Replace token" }));
    expect(makeToken).not.toHaveBeenCalled();
    confirm("Replace token");
    expect(await screen.findByTestId("provisioning-token")).toHaveTextContent("The old token no longer works");
  });

  it("switches off after asking", async () => {
    connections.mockResolvedValue([CONNECTION]);
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Provisioning: Entra ID" }));
    connections.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Switch provisioning off" }));
    confirm("Switch provisioning off");
    await waitFor(() => expect(removeConnection).toHaveBeenCalledWith("entra"));
    expect(await screen.findByRole("button", { name: "Switch provisioning on" })).toBeInTheDocument();
  });

  it("offers only provisioned people for identity providers, not for directories", async () => {
    update.mockResolvedValue(ENTRA);
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change Corp AD" }));
    expect(screen.queryByLabelText(/^Only people the provider's provisioning added/)).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Change Entra ID" }));
    fireEvent.click(screen.getByLabelText(/^Only people the provider's provisioning added/));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][1].policy.provisioned_only).toBe(true);
  });

  it("ends a provider's provisioning when the provider is deleted", async () => {
    connections.mockResolvedValue([CONNECTION]);
    vi.spyOn(signInProviders, "remove").mockResolvedValue(undefined);
    renderPage(<AuthProvidersPage />);
    await screen.findByRole("button", { name: "Provisioning: Entra ID" });
    await waitFor(() => expect(connections).toHaveBeenCalled());
    fireEvent.click(screen.getAllByRole("button", { name: "Delete provider" })[1]);
    expect(await screen.findByText(/Its provisioning ends too/)).toBeInTheDocument();
    confirm("Delete");
    await waitFor(() => expect(removeConnection).toHaveBeenCalledWith("entra"));
  });
});
