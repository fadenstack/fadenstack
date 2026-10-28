/**
 * Settings > Mail: shown only while the server offers it; the whole form in one save, with the stored secrets
 * kept unless typed; the server's problems on the fields they name; a test mail's failure in words with the
 * mail server's own reply; and why no mail goes out.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { hasFeature } from "~/api/about";
import { ApiError } from "~/api/http";
import { llmSettings } from "~/api/llm";
import {
  MAIL_SETTINGS,
  mailApi,
  type MailCertificate,
  type MailCertificatesRead,
  type MailFailure,
  type MailView,
} from "~/api/mailSettings";
import { systemSettingsApi } from "~/api/systemSettings";
import { renderPage } from "~/test/renderPage";

import SettingsPage from "../SettingsPage";
import { READ_DELAY_MS } from "./Certificates";
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
let certificates: MockInstance<typeof mailApi.certificates>;

beforeEach(() => {
  vi.mocked(hasFeature).mockResolvedValue(true);
  get = vi.spyOn(mailApi, "get").mockResolvedValue(VIEW);
  save = vi.spyOn(mailApi, "save").mockResolvedValue(VIEW);
  test = vi.spyOn(mailApi, "test");
  certificates = vi.spyOn(mailApi, "certificates").mockResolvedValue({ certificates: null, problem: null });
  vi.spyOn(systemSettingsApi, "schema").mockResolvedValue([]);
  vi.spyOn(systemSettingsApi, "values").mockResolvedValue({ items: {} });
  vi.spyOn(llmSettings, "getHFToken").mockResolvedValue({ configured: false } as Awaited<
    ReturnType<typeof llmSettings.getHFToken>
  >);
});

/** The tab in the admin layout, which polls whether root mode is on (as the server's VIEW says: on). */
function renderTab(rootModeActive = true) {
  return renderPage(<MailSettingsTab />, { context: { userEmail: "admin@example.com", rootModeActive } });
}

/** Picks *option* in the select labelled *name*. */
function choose(name: string, option: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name }));
  fireEvent.click(within(screen.getByRole("listbox")).getByText(option));
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
    const { unmount } = renderTab(false);
    expect(await screen.findByTestId("mail-root-mode-hint")).toHaveTextContent("needs root mode");
    unmount();

    // Switched on in the top bar after the page loaded: the layout says so.
    renderTab(true);
    await screen.findByDisplayValue("mail.example.com");
    expect(screen.queryByTestId("mail-root-mode-hint")).not.toBeInTheDocument();
  });

  it("says it again once root mode has ended while the page was open", async () => {
    // The server said on when the page loaded; the layout's poll says it is off now.
    renderTab(false);
    expect(await screen.findByTestId("mail-root-mode-hint")).toHaveTextContent("needs root mode");
  });

  it("goes by the server's word on root mode outside the admin layout", async () => {
    get.mockResolvedValue({ ...VIEW, root_mode: false });
    renderPage(<MailSettingsTab />);
    expect(await screen.findByTestId("mail-root-mode-hint")).toBeInTheDocument();
  });

  it("says that changed trusted certificates need root mode, and where the server refused it", async () => {
    save.mockRejectedValue(
      new ApiError("root mode", 403, undefined, { problems: [{ field: "ca_bundle", code: "root_mode" }] }),
    );
    renderTab(false);
    await screen.findByDisplayValue("mail.example.com");
    expect(screen.queryByTestId("mail-certificates-root-mode-hint")).not.toBeInTheDocument();

    fireEvent.change(textbox(/^Certificates/), { target: { value: "-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----" } });
    expect(screen.getByTestId("mail-certificates-root-mode-hint")).toHaveTextContent(
      "Changing the trusted certificates needs root mode",
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("mail-save-result")).toHaveTextContent(
      "A new password or client secret, or changed trusted certificates, need root mode",
    );
    expect(textbox(/^Certificates/)).toHaveAccessibleDescription("Changing it needs root mode: switch it on in the top bar.");
  });

  it("says in words that the trusted certificates hold something else than certificates", async () => {
    save.mockRejectedValue(
      new ApiError("refused", 422, undefined, { problems: [{ field: "ca_bundle", code: "not_certificate" }] }),
    );
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.change(textbox(/^Certificates/), { target: { value: "-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("mail-save-result")).toHaveTextContent("Some fields need attention");
    expect(textbox(/^Certificates/)).toHaveAccessibleDescription(/^Only certificates: this text holds another kind of PEM block/);
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
    expect(within(result).getByTestId("mail-failure-reason-label")).toHaveTextContent("The mail server's own words:");
    expect(within(result).getByTestId("mail-failure-reason")).toHaveTextContent(
      "535 5.7.8 Authentication credentials invalid",
    );
    // The form's values go with it, unsaved: the admin tries them before saving.
    expect(test).toHaveBeenCalledWith("admin@example.com", expect.objectContaining({ host: "mail.example.com", password: null }));
  });

  it("does not pass the mail service's own words off as the mail server's", async () => {
    test.mockResolvedValue({
      ok: false,
      code: "tls_failed",
      reason: "TLSV1_ALERT_PROTOCOL_VERSION (this port may expect STARTTLS)",
      tls_version: "",
      auth: "",
      elapsed_ms: null,
    });
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));

    const result = await screen.findByTestId("mail-test-result");
    expect(within(result).getByTestId("mail-failure-text")).toHaveTextContent(
      "and that the mail server offers TLS 1.2 or newer",
    );
    expect(within(result).getByTestId("mail-failure-reason-label")).toHaveTextContent(/^Details:$/);
    expect(within(result).getByTestId("mail-failure-reason")).toHaveTextContent("TLSV1_ALERT_PROTOCOL_VERSION");
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

  it("asks to wait while another test mail is still on its way", async () => {
    test.mockRejectedValue(new ApiError("running", 429, "test_running", { code: "test_running" }));
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));
    const result = await screen.findByTestId("mail-test-result");
    expect(result).toHaveTextContent("Another test mail of yours is still on its way: wait for its result");
    expect(result).not.toHaveTextContent("Too many");
  });

  it("shows a server without a saved sign-in as one without, and saves it so", async () => {
    get.mockResolvedValue({
      ...VIEW,
      settings: { ...VIEW.settings, host: "relay.example.internal", auth: "none", username: "", password_set: false,
        secrets_server: "" },
      root_mode: false,
    });
    renderTab(false);
    await screen.findByDisplayValue("relay.example.internal");
    expect(screen.getByRole("combobox", { name: "Sign in with" })).toHaveTextContent("No sign-in");
    expect(screen.getByRole("combobox", { name: "Sign in with" })).toHaveAccessibleDescription(
      /^For a relay in your network/,
    );
    expect(screen.queryByRole("textbox", { name: /^User name/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("mail-password")).not.toBeInTheDocument();
    expect(screen.queryByTestId("mail-root-mode-hint")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({ auth: "none", username: "", password: null, m365_client_secret: null });
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
    choose("Sign in with", "Microsoft 365");
    expect(textbox(/^Server/)).toHaveValue("smtp.office365.com");
    expect(textbox(/^Port/)).toHaveValue("587");
    expect(textbox(/^Tenant/)).toBeInTheDocument();
    expect(screen.getByTestId("mail-m365_client_secret")).toBeInTheDocument();
    expect(screen.queryByTestId("mail-m365-server")).not.toBeInTheDocument();
  });

  it("switching to Microsoft 365 replaces another server, and says so", async () => {
    get.mockResolvedValue({
      ...VIEW,
      settings: { ...VIEW.settings, host: "smtp.gmail.com", port: 465, security: "tls", secrets_server: "smtp.gmail.com:465" },
    });
    renderTab();
    await screen.findByDisplayValue("smtp.gmail.com");
    choose("Sign in with", "Microsoft 365");

    expect(textbox(/^Server/)).toHaveValue("smtp.office365.com");
    expect(textbox(/^Port/)).toHaveValue("587");
    expect(screen.getByRole("combobox", { name: "Encryption" })).toHaveTextContent("STARTTLS (port 587)");
    expect(screen.getByTestId("mail-m365-server")).toHaveTextContent(
      "The server is now smtp.office365.com, port 587, STARTTLS, in place of smtp.gmail.com",
    );
  });

  it("never sends a Microsoft 365 sign-in toward another server", async () => {
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    choose("Sign in with", "Microsoft 365");
    fireEvent.change(textbox(/^Server/), { target: { value: "smtp.gmail.com" } });
    expect(screen.queryByTestId("mail-m365-server")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Send a test mail" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByTestId("mail-save-result")).toHaveTextContent("Some fields need attention");
    expect(textbox(/^Server/)).toHaveAccessibleDescription(
      "With Microsoft 365 the server must be smtp.office365.com: the sign-in goes nowhere else.",
    );
    expect(test).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();

    // Microsoft's own server, as typed by hand (any case, a trailing dot): fine.
    fireEvent.change(textbox(/^Server/), { target: { value: "SMTP.Office365.com." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  });

  it("puts the server's refusal of another server for Microsoft 365 on the Server field", async () => {
    save.mockRejectedValue(new ApiError("refused", 422, undefined, { problems: [{ field: "host", code: "m365_host" }] }));
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByTestId("mail-save-result");
    expect(textbox(/^Server/)).toHaveAccessibleDescription(/^With Microsoft 365 the server must be smtp\.office365\.com/);
  });

  it("links the Help page, and its Microsoft 365 steps", async () => {
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    expect(screen.getByTestId("mail-help")).toHaveAttribute("href", "/help/administer/mail/");
    choose("Sign in with", "Microsoft 365");
    expect(screen.getByTestId("mail-m365-help")).toHaveAttribute("href", "/help/administer/mail/#microsoft-365");
  });

  it("takes at most 50 alert recipients, and no longer texts than the server", async () => {
    const many = Array.from({ length: 49 }, (_, i) => `ops${i}@example.com`);
    get.mockResolvedValue({ ...VIEW, settings: { ...VIEW.settings, admin_recipients: many } });
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    const add = textbox(/^Add an address/);

    fireEvent.change(add, { target: { value: "a@example.com, b@example.com" } });
    fireEvent.keyDown(add, { key: "Enter" });
    expect(add).toHaveAccessibleDescription("At most 50 addresses.");
    expect(add).toHaveValue("a@example.com, b@example.com");
    expect(screen.queryByText("a@example.com")).not.toBeInTheDocument();

    fireEvent.change(add, { target: { value: "a@example.com" } });
    fireEvent.keyDown(add, { key: "Enter" });
    expect(screen.getByText("a@example.com")).toBeInTheDocument();

    expect(textbox(/^Server/)).toHaveAttribute("maxLength", "253");
    expect(textbox(/^Sender name/)).toHaveAttribute("maxLength", "200");
    expect(screen.getByTestId("mail-password")).toHaveAttribute("maxLength", "1024");
  });
});

describe("pasted certificates", () => {
  const PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n";
  const CA: MailCertificate = {
    subject: "CN=Example Root CA",
    issuer: "CN=Example Root CA",
    not_after: "2030-01-01T00:00:00+00:00",
    expired: false,
    ca_without_key_usage: true,
  };
  const field = () => textbox(/^Certificates/);

  it("are read before Save, with the warning under a CA the mail service refuses", async () => {
    certificates.mockResolvedValue({ certificates: [CA], problem: null });
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.change(field(), { target: { value: PEM } });

    const pasted = await screen.findByTestId("mail-certificates-pasted");
    expect(certificates).toHaveBeenCalledTimes(1);
    expect(certificates).toHaveBeenCalledWith(PEM);
    expect(within(pasted).getByRole("heading", { name: "Pasted certificates, not saved yet:" })).toBeInTheDocument();
    expect(within(pasted).getByTestId("mail-certificate")).toHaveTextContent("CN=Example Root CA");
    expect(within(pasted).getByTestId("mail-certificate")).toHaveTextContent("Valid until");
    expect(within(pasted).getByTestId("mail-certificate-no-key-usage")).toHaveTextContent(
      "The mail service refuses this CA: it may not sign certificates (no key usage, RFC 5280).",
    );
    expect(screen.queryByText("Changed: not saved yet.")).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("says a paste that is no certificate, and one that cannot be read", async () => {
    renderTab();
    await screen.findByDisplayValue("mail.example.com");

    certificates.mockResolvedValueOnce({ certificates: null, problem: "not_certificate" });
    fireEvent.change(field(), { target: { value: "-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----" } });
    expect(await screen.findByTestId("mail-certificates-pasted-not-certificate")).toHaveTextContent(
      "The pasted text holds something other than certificates, e.g. a private key, and cannot be saved",
    );

    certificates.mockResolvedValueOnce({ certificates: null, problem: null });
    fireEvent.change(field(), { target: { value: "-----BEGIN CERTIFICATE-----\ncut off" } });
    expect(await screen.findByTestId("mail-certificates-pasted-unreadable")).toHaveTextContent(
      "The pasted text holds no certificate that can be read",
    );
    expect(screen.queryByTestId("mail-certificates-pasted-not-certificate")).not.toBeInTheDocument();
  });

  it("reads once typing pauses, and shows the answer for the latest text whichever answers last", async () => {
    const answers: ((read: MailCertificatesRead) => void)[] = [];
    certificates.mockImplementation(() => new Promise((resolve) => answers.push(resolve)));
    renderTab();
    await screen.findByDisplayValue("mail.example.com");

    // Typed in quick succession: one read, of the last text.
    fireEvent.change(field(), { target: { value: "-----BEGIN" } });
    fireEvent.change(field(), { target: { value: PEM } });
    await waitFor(() => expect(certificates).toHaveBeenCalledTimes(1));
    expect(certificates).toHaveBeenLastCalledWith(PEM);

    fireEvent.change(field(), { target: { value: PEM + PEM } });
    await waitFor(() => expect(certificates).toHaveBeenCalledTimes(2));
    await act(async () => answers[1]({ certificates: [{ ...CA, subject: "CN=second" }], problem: null }));
    await act(async () => answers[0]({ certificates: [{ ...CA, subject: "CN=first" }], problem: null }));

    const pasted = await screen.findByTestId("mail-certificates-pasted");
    expect(pasted).toHaveTextContent("CN=second");
    expect(pasted).not.toHaveTextContent("CN=first");
  });

  it("asks nothing for the saved text or an empty field", async () => {
    get.mockResolvedValue({ ...VIEW, settings: { ...VIEW.settings, ca_bundle: PEM }, certificates: [CA] });
    renderTab();
    await screen.findAllByTestId("mail-certificate");

    fireEvent.change(field(), { target: { value: "" } });
    expect(screen.getByText("Changed: not saved yet.")).toBeInTheDocument();
    fireEvent.change(field(), { target: { value: PEM } });
    expect(screen.queryByText("Changed: not saved yet.")).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, READ_DELAY_MS + 200));

    expect(certificates).not.toHaveBeenCalled();
    expect(screen.queryByTestId("mail-certificates-pasted")).not.toBeInTheDocument();
  });

  it("says only that the change is not saved yet when the text cannot be read now", async () => {
    certificates.mockRejectedValue(new ApiError("not found", 404));
    renderTab();
    await screen.findByDisplayValue("mail.example.com");
    fireEvent.change(field(), { target: { value: PEM } });

    await waitFor(() => expect(certificates).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Changed: not saved yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("mail-certificates-pasted-unreadable")).not.toBeInTheDocument();
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
          sending: false,
          created_at: "2026-10-27T08:00:00Z",
          next_attempt_at: null,
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

  const FAILURE: MailFailure = {
    kind: "password_reset",
    code: "connect_failed",
    reason: "mail.example.com:587: Connection refused",
    attempts: 2,
    gave_up: false,
    sending: false,
    created_at: "2026-10-27T08:00:00Z",
    next_attempt_at: "2026-10-27T09:00:00Z",
    after_last_sent: true,
  };

  async function lastFailure(failure: Partial<MailFailure>) {
    get.mockResolvedValue({ ...VIEW, state: { ...VIEW.state, waiting: 1, last_failure: { ...FAILURE, ...failure } } });
    renderTab();
    return within(await screen.findByTestId("mail-state")).findByTestId("mail-last-failure");
  }

  it("says when a failed mail is tried again", async () => {
    const failure = await lastFailure({});
    expect(within(failure).getByTestId("mail-failure-outcome")).toHaveTextContent(/^Tries again /);
  });

  it("says a failed mail is being sent right now, not when it is tried again", async () => {
    const failure = await lastFailure({ sending: true, next_attempt_at: null });
    expect(within(failure).getByTestId("mail-failure-outcome")).toHaveTextContent("Being sent now.");
  });

  it("claims no retry for a mail the mail server refused for good", async () => {
    const failure = await lastFailure({
      code: "login_refused", reason: "535 5.7.8 Authentication credentials invalid", attempts: 1, gave_up: true,
      next_attempt_at: null,
    });
    expect(within(failure).getByTestId("mail-failure-outcome")).toHaveTextContent(
      "Not tried again: the mail server refused it for good.",
    );
    expect(failure).not.toHaveTextContent("Tries again");
  });

  it("names nothing next for a mail that is neither waiting nor being sent", async () => {
    const failure = await lastFailure({ next_attempt_at: null });
    expect(within(failure).queryByTestId("mail-failure-outcome")).not.toBeInTheDocument();
    expect(failure).not.toHaveTextContent("Tries again");
  });

  it("words a mail service that cannot be reached, without calling its reason the mail server's", async () => {
    const failure = await lastFailure({ code: "mailer_unreachable", reason: "the mail service answered 403" });
    expect(within(failure).getByTestId("mail-failure-text")).toHaveTextContent("The mail service cannot be reached");
    expect(within(failure).getByTestId("mail-failure-reason-label")).toHaveTextContent(/^Details:$/);
    expect(within(failure).getByTestId("mail-failure-reason")).toHaveTextContent("the mail service answered 403");
  });

  it("words a refusal for now, with the mail server's reply", async () => {
    const failure = await lastFailure({ code: "temporarily_refused", reason: "421 4.7.0 Too many connections" });
    expect(within(failure).getByTestId("mail-failure-text")).toHaveTextContent(
      "The mail server refused for now: mail is tried again later by itself.",
    );
    expect(within(failure).getByTestId("mail-failure-reason-label")).toHaveTextContent("The mail server's own words:");
  });

  it("words mail that waits after a refused sign-in, and tries it again", async () => {
    const failure = await lastFailure({
      code: "login_paused",
      reason:
        "the server refused this sign-in at 08:00 UTC (535 5.7.8 Authentication credentials invalid); it is not " +
        "tried again before 08:15 UTC, unless the sign-in changes or a test mail goes through",
    });
    const text = within(failure).getByTestId("mail-failure-text");
    expect(text).toHaveTextContent("The mail server refused this sign-in a short while ago");
    expect(text).toHaveTextContent("mail waits up to 15 minutes before the next sign-in");
    expect(text).toHaveTextContent("send a test mail to try at once");
    expect(within(failure).getByTestId("mail-failure-reason-label")).toHaveTextContent(/^Details:$/);
    expect(within(failure).getByTestId("mail-failure-outcome")).toHaveTextContent(/^Tries again /);
  });

  it("words a mail the mail service is still sending, with nothing to do", async () => {
    const failure = await lastFailure({ code: "still_sending", reason: "an earlier attempt is still being sent" });
    expect(within(failure).getByTestId("mail-failure-text")).toHaveTextContent(
      "This mail is still being sent by the mail service: it is checked again shortly, nothing to do.",
    );
    expect(within(failure).getByTestId("mail-failure-outcome")).toHaveTextContent(/^Tries again /);
  });
});
