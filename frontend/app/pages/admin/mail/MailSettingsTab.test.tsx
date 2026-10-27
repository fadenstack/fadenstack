/**
 * Settings > Mail: shown only while the server offers it; the whole form in one save, with the stored secrets
 * kept unless typed; the server's problems on the fields they name; a test mail's failure in words with the
 * mail server's own reply; and why no mail goes out.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { hasFeature } from "~/api/about";
import { ApiError } from "~/api/http";
import { llmSettings } from "~/api/llm";
import { MAIL_SETTINGS, mailApi, type MailView } from "~/api/mailSettings";
import { systemSettingsApi } from "~/api/systemSettings";
import { renderPage } from "~/test/renderPage";

import SettingsPage from "../SettingsPage";
import MailSettingsTab from "./MailSettingsTab";

vi.mock("~/api/about", () => ({ hasFeature: vi.fn() }));
// The Modules tab reads the modules; not what these tests are about.
vi.mock("~/lib/ServicesContext", () => ({
  useServices: () => ({ services: [], loading: false, error: null, refresh: async () => {} }),
}));

const VIEW: MailView = {
  settings: {
    host: "mail.example.com",
    port: 587,
    security: "starttls",
    auth: "password",
    username: "noreply@example.com",
    password_set: true,
    ca_bundle: "",
    helo_name: "",
    from_email: "noreply@example.com",
    from_name: "Fadenstack",
    link_base: "https://ai.example.com",
    admin_recipients: ["ops@example.com"],
    m365_tenant_id: "",
    m365_client_id: "",
    m365_client_secret_set: false,
    secrets_server: "mail.example.com:587",
  },
  certificates: [],
  state: { goes_out: true, reasons: [], waiting: 0, last_sent_at: null, last_failure: null },
  root_mode: true,
};

let get: MockInstance<typeof mailApi.get>;
let save: MockInstance<typeof mailApi.save>;
let test: MockInstance<typeof mailApi.test>;

beforeEach(() => {
  vi.mocked(hasFeature).mockResolvedValue(true);
  get = vi.spyOn(mailApi, "get").mockResolvedValue(VIEW);
  save = vi.spyOn(mailApi, "save").mockResolvedValue(VIEW);
  test = vi.spyOn(mailApi, "test");
  vi.spyOn(systemSettingsApi, "schema").mockResolvedValue([]);
  vi.spyOn(systemSettingsApi, "values").mockResolvedValue({ items: {} });
  vi.spyOn(llmSettings, "getHFToken").mockResolvedValue({ configured: false } as Awaited<
    ReturnType<typeof llmSettings.getHFToken>
  >);
});

function renderTab() {
  return renderPage(<MailSettingsTab />, { context: { userEmail: "admin@example.com", rootModeActive: false } });
}

const textbox = (name: RegExp) => screen.getByRole("textbox", { name });

describe("the Mail tab", () => {
  it("shows only while the server offers mail settings", async () => {
    vi.mocked(hasFeature).mockResolvedValue(false);
    const { unmount } = renderPage(<SettingsPage />, { path: "/admin/settings" });
    await waitFor(() => expect(hasFeature).toHaveBeenCalledWith(MAIL_SETTINGS));
    expect(await screen.findByRole("tab", { name: "Modules" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Mail" })).not.toBeInTheDocument();
    unmount();

    vi.mocked(hasFeature).mockResolvedValue(true);
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=mail" });
    expect(await screen.findByRole("tab", { name: "Mail", selected: true })).toBeInTheDocument();
    expect(await screen.findByTestId("mail-settings")).toBeInTheDocument();
    expect(get).toHaveBeenCalled();
  });

  it("opens General for a link to the Mail tab on a server without it", async () => {
    vi.mocked(hasFeature).mockResolvedValue(false);
    renderPage(<SettingsPage />, { path: "/admin/settings", initialPath: "/admin/settings?tab=mail" });
    expect(await screen.findByRole("tab", { name: "General", selected: true })).toBeInTheDocument();
    expect(screen.queryByTestId("mail-settings")).not.toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });
});

describe("the mail form", () => {
  it("loads the settings and saves the whole form, keeping the stored password", async () => {
    renderTab();
    expect(await screen.findByDisplayValue("mail.example.com")).toBeInTheDocument();
    expect(screen.getByTestId("mail-password")).toHaveValue("");
    expect(screen.getByTestId("mail-password")).toHaveAccessibleDescription(/^Stored\./);
    expect(screen.getByText("ops@example.com")).toBeInTheDocument();

    fireEvent.change(textbox(/^Sender name/), { target: { value: "AI at Example" } });
    fireEvent.change(textbox(/^Add an address/), { target: { value: "security@example.com" } });
    fireEvent.keyDown(textbox(/^Add an address/), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toEqual({
      host: "mail.example.com",
      port: 587,
      security: "starttls",
      auth: "password",
      username: "noreply@example.com",
      password: null,
      ca_bundle: "",
      helo_name: "",
      from_email: "noreply@example.com",
      from_name: "AI at Example",
      link_base: "https://ai.example.com",
      admin_recipients: ["ops@example.com", "security@example.com"],
      m365_tenant_id: "",
      m365_client_id: "",
      m365_client_secret: null,
    });
    expect(await screen.findByTestId("mail-save-result")).toHaveTextContent("Mail settings saved.");
  });

  it("sends a typed password, and suggests the port that goes with the encryption", async () => {
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.change(screen.getByTestId("mail-password"), { target: { value: "s3cret" } });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Encryption" }));
    fireEvent.click(within(screen.getByRole("listbox")).getByText("TLS from the start (port 465)"));
    expect(textbox(/^Port/)).toHaveValue("465");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({ security: "tls", port: 465, password: "s3cret" });
  });

  it("puts a stored password saved for another server on the password field", async () => {
    save.mockRejectedValue(
      new ApiError("refused", 422, undefined, { problems: [{ field: "password", code: "again_for_new_server" }] }),
    );
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.change(textbox(/^Server/), { target: { value: "smtp.other.example" } });
    expect(screen.getByTestId("mail-password")).not.toHaveAttribute("aria-invalid", "true");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByTestId("mail-save-result")).toHaveTextContent("Some fields need attention");
    const password = screen.getByTestId("mail-password");
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password).toHaveAccessibleDescription(
      "The stored one was saved for another server: type it again to use it with this one.",
    );
    expect(save.mock.calls[0][0]).toMatchObject({ host: "smtp.other.example", password: null });
  });

  it("says that a new password needs root mode while it is off", async () => {
    get.mockResolvedValue({ ...VIEW, root_mode: false });
    const { unmount } = renderTab();
    expect(await screen.findByTestId("mail-root-mode-hint")).toHaveTextContent("needs root mode");
    unmount();

    // Switched on in the top bar after the page loaded: the layout says so.
    renderPage(<MailSettingsTab />, { context: { userEmail: "admin@example.com", rootModeActive: true } });
    await screen.findByDisplayValue("mail.example.com");
    expect(screen.queryByTestId("mail-root-mode-hint")).not.toBeInTheDocument();
  });

  it("words a failed test mail and shows the mail server's own reply", async () => {
    test.mockResolvedValue({
      ok: false,
      code: "login_refused",
      reason: "535 5.7.8 Authentication credentials invalid",
      tls_version: "",
      auth: "",
      elapsed_ms: null,
    });
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    expect(textbox(/^Send to/)).toHaveValue("admin@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));

    const result = await screen.findByTestId("mail-test-result");
    expect(result).toHaveTextContent("The test mail did not go out");
    expect(within(result).getByTestId("mail-failure-text")).toHaveTextContent(
      "The mail server refused the sign-in: check the user name and the password",
    );
    expect(within(result).getByTestId("mail-failure-reason")).toHaveTextContent(
      "535 5.7.8 Authentication credentials invalid",
    );
    // The form's values go with it, unsaved: the admin tries them before saving.
    expect(test).toHaveBeenCalledWith("admin@example.com", expect.objectContaining({ host: "mail.example.com", password: null }));
  });

  it("says how a test mail went out", async () => {
    test.mockResolvedValue({ ok: true, code: null, reason: "", tls_version: "TLSv1.3", auth: "password", elapsed_ms: 412 });
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));
    const result = await screen.findByTestId("mail-test-result");
    expect(result).toHaveTextContent("The test mail went out to admin@example.com.");
    expect(result).toHaveTextContent("Encrypted with TLSv1.3 · Signed in: User name and password · 412 ms");
  });

  it("asks to wait after too many test mails", async () => {
    test.mockRejectedValue(new ApiError("too many", 429, "too_many_tests", { code: "too_many_tests" }));
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));
    expect(await screen.findByTestId("mail-test-result")).toHaveTextContent("Too many test mails in ten minutes");
  });

  it("lists the saved certificates, warning of a CA the mail service refuses", async () => {
    get.mockResolvedValue({
      ...VIEW,
      settings: { ...VIEW.settings, ca_bundle: "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n" },
      certificates: [
        {
          subject: "CN=Example Root CA", issuer: "CN=Example Root CA", not_after: "2030-01-01T00:00:00+00:00",
          expired: false, ca_without_key_usage: true,
        },
        {
          subject: "CN=mail.example.com", issuer: "CN=Example Root CA", not_after: "2027-01-01T00:00:00+00:00",
          expired: false, ca_without_key_usage: false,
        },
      ],
    });
    renderTab();
    const [ca, server] = await screen.findAllByTestId("mail-certificate");
    expect(ca).toHaveTextContent("CN=Example Root CA");
    expect(within(ca).getByTestId("mail-certificate-no-key-usage")).toHaveTextContent(
      "The mail service refuses this CA: it may not sign certificates (no key usage, RFC 5280).",
    );
    expect(within(server).queryByTestId("mail-certificate-no-key-usage")).not.toBeInTheDocument();
  });

  it("switching to Microsoft 365 with no server set brings Microsoft's", async () => {
    get.mockResolvedValue({ ...VIEW, settings: { ...VIEW.settings, host: "", password_set: false, secrets_server: "" } });
    renderTab();
    await screen.findByTestId("mail-settings");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Sign in with" }));
    fireEvent.click(within(screen.getByRole("listbox")).getByText("Microsoft 365"));
    expect(textbox(/^Server/)).toHaveValue("smtp.office365.com");
    expect(textbox(/^Port/)).toHaveValue("587");
    expect(textbox(/^Tenant/)).toBeInTheDocument();
    expect(screen.getByTestId("mail-m365_client_secret")).toBeInTheDocument();
  });
});

describe("the mail state", () => {
  it("says why no mail goes out, and the last failure in words", async () => {
    get.mockResolvedValue({
      ...VIEW,
      certificates: null,
      state: {
        goes_out: false,
        reasons: ["module_off", "no_link_base"],
        waiting: 3,
        last_sent_at: null,
        last_failure: {
          kind: "password_reset",
          code: "tls_untrusted",
          reason: "certificate verify failed: unable to get local issuer certificate",
          attempts: 5,
          gave_up: true,
          created_at: "2026-10-27T08:00:00Z",
          next_attempt_at: "2026-10-27T09:00:00Z",
          after_last_sent: true,
        },
      },
    });
    renderTab();
    const state = await screen.findByTestId("mail-state");
    expect(within(state).getByTestId("mail-goes-out")).toHaveTextContent("No mail goes out");
    expect(state).toHaveTextContent("The mail module is switched off");
    expect(state).toHaveTextContent("The console's address for links is missing");
    expect(within(state).getByTestId("mail-waiting")).toHaveTextContent("3");
    const failure = within(state).getByTestId("mail-last-failure");
    expect(failure).toHaveTextContent("Password reset");
    expect(failure).toHaveTextContent("The mail server's certificate is not trusted");
    expect(within(failure).getByTestId("mail-failure-reason")).toHaveTextContent("unable to get local issuer certificate");
    expect(failure).toHaveTextContent("Given up: this mail is not sent.");
    expect(screen.getByTestId("mail-certificates-unreadable")).toBeInTheDocument();
  });
});
