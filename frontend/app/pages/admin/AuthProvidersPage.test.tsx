/** The sign-in providers page: the module's providers, adding and changing one, and no module at all. */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import * as admin from "~/api/admin";
import { ProviderApiError, signInProviders, type SignInProvider } from "~/api/signInProviders";
import { renderPage } from "~/test/renderPage";

import AuthProvidersPage from "./AuthProvidersPage";

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

beforeEach(() => {
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
    expect(screen.getByDisplayValue(`${window.location.origin}/api/auth/sso/example-corp/callback`)).toBeInTheDocument();
    expect(screen.getByTestId("provider-missing")).toHaveTextContent("the issuer");

    fireEvent.change(field(/^Issuer/), { target: { value: "https://login.example.com/v2.0" } });
    fireEvent.change(field(/^Client ID/), { target: { value: "app-1" } });
    fireEvent.change(screen.getByLabelText(/^Client secret/), { target: { value: "s3cret" } });
    expect(screen.queryByTestId("provider-missing")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({
      key: "example-corp", name: "Example Corp",
      settings: { protocol: "oidc", issuer: "https://login.example.com/v2.0", client_id: "app-1" },
      secrets: { client_secret: "s3cret" },
      policy: { create_account: true, session_lifetime_s: 43_200, inactive_days: 45 },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(list).toHaveBeenCalledTimes(2);
  });

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

  it("warns of a directory without TLS", async () => {
    renderPage(<AuthProvidersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Change Corp AD" }));
    fireEvent.change(field(/^Directory address/), { target: { value: "ldap://dc1.example.com" } });
    expect(screen.getByText(/Without TLS, passwords cross the network readable/)).toBeInTheDocument();
  });
});
