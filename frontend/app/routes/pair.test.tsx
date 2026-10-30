/**
 * The console's /pair page: a person approves an app's sign-in by its code (fadenstack-dev desktop-agent/23 §4.3).
 *
 * The code comes in the link's fragment, is dropped from the address bar, rides through the sign-in page in this
 * tab's sessionStorage, and only ever travels in request bodies: never in an address, a query string or a log.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { auth, type AuthUser } from "~/api/auth";
import * as adminApi from "~/api/admin";
import type { PairingLookup } from "~/api/pairing";
import { keepCode, keptCode } from "~/lib/pairCode";
import { PAIR_HELP_DOC } from "~/lib/pageHelp";

import Login from "./login";
import PairPage from "./pair";

vi.mock("~/lib/userLanguage", () => ({ applyUserLanguage: vi.fn(async () => null) }));

const CODE = "BDWP-HTQX";
const ANNA: AuthUser = {
  id: "0b0b0b0b-0000-4000-8000-000000000001",
  email: "anna@example.com",
  is_active: true,
  is_superuser: false,
  is_verified: true,
};

const FOUND: PairingLookup = {
  agent: { slug: "desk", name: "Desk" },
  available: true,
  device_label: "Desktop agent on SACHI-PC",
  client: { name: "Fadenstack Desktop", version: "0.1.0", os: "Windows 11" },
  requester_ip: "203.0.113.9",
  same_network: true,
  created_at: "2026-10-30T12:05:00Z",
  expires_at: "2026-10-30T12:15:00Z",
  sign_in_lasts: { idle_days: 7, max_days: 30 },
};

type Answer = { status: number; body?: unknown; headers?: Record<string, string> };
type Call = { url: string; method: string; body: unknown };

let me: MockInstance<typeof auth.me>;
let answers: Record<string, Answer[]>;
let calls: Call[];
/** Every address the router went to, query strings included. */
let visited: string[];

function answer(step: string, ...list: Answer[]) {
  answers[step] = [...(answers[step] ?? []), ...list];
}

beforeEach(() => {
  me = vi.spyOn(auth, "me");
  answers = {};
  calls = [];
  visited = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const step = /\/api\/agent-tokens\/pairings\/(\w+)$/.exec(url)?.[1];
    const next = step ? answers[step]?.shift() : undefined;
    if (!next) return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(next.status === 204 ? null : JSON.stringify(next.body ?? {}), {
      status: next.status,
      headers: { "Content-Type": "application/json", ...next.headers },
    });
  });
});

afterEach(() => {
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

/** Where the router is, for the assertions. */
function Probe({ name }: { name: string }) {
  const location = useLocation();
  visited.push(`${location.pathname}${location.search}${location.hash}`);
  return <div data-testid={`at-${name}`}>{`${location.pathname}${location.search}`}</div>;
}

function Track() {
  const location = useLocation();
  visited.push(`${location.pathname}${location.search}${location.hash}`);
  return null;
}

/** The page at `address` (the browser's address bar too: the page reads the fragment from there). */
function open(address = "/pair", { strict = false, login = <Probe name="login" /> } = {}) {
  window.history.replaceState(null, "", address);
  const tree = (
    <MemoryRouter initialEntries={["/pair"]}>
      <Track />
      <Routes>
        <Route path="/pair" element={<PairPage />} />
        <Route path="/login" element={login} />
      </Routes>
    </MemoryRouter>
  );
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

function field(): HTMLInputElement {
  return screen.getByLabelText(/^Code/) as HTMLInputElement;
}

async function lookUp(code = CODE) {
  fireEvent.change(await screen.findByLabelText(/^Code/), { target: { value: code } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** The code is in no address the browser or the router went to, and in no request's address. */
function expectNoCodeInAnyAddress() {
  const letters = CODE.replace("-", "");
  const leaks = [...visited, ...calls.map((c) => c.url), window.location.href].filter(
    (url) => url.toUpperCase().includes(CODE.slice(0, 4)) || url.toUpperCase().replace(/[^A-Z]/g, "").includes(letters),
  );
  expect(leaks).toEqual([]);
}

describe("the code in the link", () => {
  it("is dropped from the address bar, kept in this tab, and a signed-out person goes to the sign-in page without it", async () => {
    me.mockRejectedValue(new Error("not signed in"));
    open("/pair#code=bdwp-htqx");
    expect(await screen.findByTestId("at-login")).toHaveTextContent("/login?next=%2Fpair");
    expect(window.location.hash).toBe("");
    expect(keptCode()).toBe(CODE);
    expectNoCodeInAnyAddress();
  });

  it("fills the field once signed in, and leaves the storage", async () => {
    me.mockResolvedValue(ANNA);
    keepCode(CODE);
    open("/pair");
    await waitFor(() => expect(field()).toHaveValue(CODE));
    expect(keptCode()).toBeNull();
    expect(screen.getByTestId("pair-signed-in-as")).toHaveTextContent("You are signed in as anna@example.com.");
    // Opening the link only fills the field: nothing was looked up, let alone approved.
    expect(calls.filter((c) => c.url.includes("/pairings/"))).toEqual([]);
  });

  it("fills the field straight from the link when already signed in, in React's strict mode too", async () => {
    me.mockResolvedValue(ANNA);
    open("/pair#code=BDWP-HTQX", { strict: true });
    await waitFor(() => expect(field()).toHaveValue(CODE));
    expect(window.location.hash).toBe("");
    expect(keptCode()).toBeNull();
    expectNoCodeInAnyAddress();
  });

  it("comes back to /pair with the code after a sign-in with a provider, which lands on /login without a target", async () => {
    // 1. The link, signed out: off to the sign-in page, the code kept in this tab.
    me.mockRejectedValue(new Error("not signed in"));
    const first = open("/pair#code=bdwp-htqx");
    await screen.findByTestId("at-login");
    expect(keptCode()).toBe(CODE);
    first.unmount();

    // 2. The provider (SSO) sends the browser back to /login, with no `next` and no fragment; now signed in.
    vi.spyOn(adminApi.adminAuthProviders, "listPublic").mockResolvedValue([]);
    me.mockResolvedValue(ANNA);
    window.history.replaceState(null, "", "/login");
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Track />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/pair" element={<PairPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // 3. The sign-in page takes the person to /pair, where the field holds the code.
    await waitFor(() => expect(visited).toContain("/pair"));
    await waitFor(() => expect(screen.getByLabelText(/^Code/)).toHaveValue(CODE));
    expectNoCodeInAnyAddress();
  });

  it("does not send the sign-in page to /pair when no code waits", async () => {
    vi.spyOn(adminApi.adminAuthProviders, "listPublic").mockResolvedValue([]);
    me.mockResolvedValue(ANNA);
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Track />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/chat" element={<Probe name="chat" />} />
          <Route path="/pair" element={<Probe name="pair" />} />
        </Routes>
      </MemoryRouter>,
    );
    // A person who is no administrator lands in the chat, as before.
    expect(await screen.findByTestId("at-chat")).toBeInTheDocument();
  });
});

describe("the code field", () => {
  beforeEach(() => me.mockResolvedValue(ANNA));

  it("adds the dash while typing and takes only the code's letters, in upper case", async () => {
    const user = userEvent.setup();
    open();
    const input = await screen.findByLabelText(/^Code/);
    await user.type(input, "bdwp");
    expect(input).toHaveValue("BDWP");
    await user.type(input, "h");
    expect(input).toHaveValue("BDWP-H");
    await user.type(input, "1a-tqxz");
    expect(input).toHaveValue("BDWP-HTQX");
  });

  it("asks for all eight letters before looking anything up", async () => {
    open();
    await lookUp("BDWP-HT");
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent("Enter all 8 letters of the code.");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(calls.filter((c) => c.url.includes("/pairings/"))).toEqual([]);
  });
});

describe("looking up, approving and denying", () => {
  beforeEach(() => me.mockResolvedValue(ANNA));

  it("shows what asks, with the code to compare, and approves with the code in the body", async () => {
    answer("lookup", { status: 200, body: FOUND });
    answer("approve", { status: 204 });
    open();
    await lookUp("bdwp htqx");

    const review = await screen.findByTestId("pair-review");
    const question = within(review).getByRole("heading", { level: 2 });
    expect(question).toHaveTextContent("Sign in Desk on “Desktop agent on SACHI-PC”?");
    expect(question).toHaveFocus();
    expect(within(review).getByTestId("pair-compare")).toHaveTextContent(
      `Check that the app on your computer shows ${CODE}.`,
    );
    expect(review).toHaveTextContent("Fadenstack Desktop 0.1.0, as the app says");
    expect(review).toHaveTextContent("Desktop agent on SACHI-PC (Windows 11)");
    expect(review).toHaveTextContent(/From 203\.0\.113\.9 at \d{1,2}:\d{2}/);
    expect(review).toHaveTextContent("Up to 30 days; it ends sooner when the app goes unused for 7 days");
    expect(review).toHaveTextContent("Only approve if you started this yourself, just now.");
    expect(screen.queryByTestId("pair-other-network")).not.toBeInTheDocument();

    fireEvent.click(within(review).getByRole("button", { name: "Approve" }));
    const result = await screen.findByTestId("pair-result");
    expect(result).toHaveTextContent("Signed in. You can go back to the app.");
    expect(result).toHaveFocus();
    // Announced: the result is in a live region that was there from the start.
    expect(screen.getByRole("status")).toContainElement(result);

    const posts = calls.filter((c) => c.url.includes("/pairings/"));
    expect(posts).toEqual([
      { url: "/api/agent-tokens/pairings/lookup", method: "POST", body: { code: CODE } },
      { url: "/api/agent-tokens/pairings/approve", method: "POST", body: { code: CODE } },
    ]);
    expectNoCodeInAnyAddress();
  });

  it("denies with the code in the body", async () => {
    answer("lookup", { status: 200, body: FOUND });
    answer("deny", { status: 204 });
    open();
    await lookUp();
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
    expect(await screen.findByTestId("pair-result")).toHaveTextContent("Denied. The app was not signed in.");
    expect(calls.at(-1)).toEqual({ url: "/api/agent-tokens/pairings/deny", method: "POST", body: { code: CODE } });
    expectNoCodeInAnyAddress();
  });

  it("warns when the app asked from another network, and says a short sign-in in hours", async () => {
    answer("lookup", {
      status: 200,
      body: { ...FOUND, same_network: false, requester_ip: null, sign_in_lasts: { idle_days: 0.5, max_days: 1 } },
    });
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-other-network")).toHaveTextContent(
      "This request came from another network than yours.",
    );
    const review = screen.getByTestId("pair-review");
    expect(review).toHaveTextContent(/From an unknown address at \d{1,2}:\d{2}/);
    expect(review).toHaveTextContent("Up to 1 day; it ends sooner when the app goes unused for 12 hours");
  });
});

describe("refusals", () => {
  beforeEach(() => me.mockResolvedValue(ANNA));

  const detail = (code: string) => ({ detail: { code, message: "the server's words" } });

  it("a wrong or expired code (404): says how to get a new one, back at the field", async () => {
    answer("lookup", { status: 404, body: detail("unknown_code") });
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent(
      "No sign-in waits for this code. Check it against the app. A code works for 10 minutes: for a new one, start signing in again in the app.",
    );
    await waitFor(() => expect(field()).toHaveFocus());
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expectNoCodeInAnyAddress();
  });

  it("already decided (409)", async () => {
    answer("lookup", { status: 200, body: FOUND });
    answer("approve", { status: 409, body: detail("already_decided") });
    open();
    await lookUp();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent(
      "This sign-in was already approved or denied.",
    );
    expect(screen.queryByTestId("pair-review")).not.toBeInTheDocument();
  });

  it("too many tries (429): says when to try again, from Retry-After", async () => {
    answer("lookup", {
      status: 429,
      body: { detail: { code: "too_many_attempts", message: "Too many wrong codes", retry_after: 590 } },
      headers: { "Retry-After": "590" },
    });
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent("Too many tries. Try again in 10 minutes.");
  });

  it("too many tries at the front door (nginx's 429)", async () => {
    answer("lookup", {
      status: 429,
      body: { detail: "Too many attempts from this address. Wait a minute, then try again." },
      headers: { "Retry-After": "60" },
    });
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent("Too many tries. Try again in 1 minute.");
  });

  it("an agent not available to the person (403): says whom to ask, and Approve is off", async () => {
    answer("lookup", { status: 200, body: FOUND });
    answer("approve", { status: 403, body: detail("agent_not_available") });
    open();
    await lookUp();
    const approve = await screen.findByRole("button", { name: "Approve" });
    fireEvent.click(approve);
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent(
      "This agent is not available to you. Ask your administrator.",
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    // Deny still ends it.
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
  });

  it("says so at once when the lookup finds the agent not available", async () => {
    answer("lookup", { status: 200, body: { ...FOUND, available: false, agent: { slug: "desk", name: null } } });
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent(
      "This agent is not available to you. Ask your administrator.",
    );
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Sign in desk on");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("a console sign-in that ended (401): to the sign-in page, the code kept, not in the address", async () => {
    const fresh = vi.spyOn(auth, "meFresh").mockRejectedValue(new Error("401"));
    answer("lookup", { status: 401, body: { detail: "Unauthorized" } });
    open();
    await lookUp();
    expect(await screen.findByTestId("at-login")).toHaveTextContent("/login?next=%2Fpair");
    expect(fresh).toHaveBeenCalled();
    expect(keptCode()).toBe(CODE);
    expectNoCodeInAnyAddress();
  });

  it("a failure without an answer: try again, and the error names no code", async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    open();
    await lookUp();
    expect(await screen.findByTestId("pair-problem")).toHaveTextContent("That did not work.");
    expect(screen.getByTestId("pair-problem")).not.toHaveTextContent(CODE);
  });
});

describe("help and texts", () => {
  it("links the page's Help, in a new tab so the code stays here", async () => {
    me.mockResolvedValue(ANNA);
    open();
    const link = await screen.findByRole("link", { name: /How signing in an app works/ });
    expect(link).toHaveAttribute("href", `/help/${PAIR_HELP_DOC}/`);
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("has every text of the page in the console's five languages, formally addressed", () => {
    const i18n = resolve("../backend/i18n");
    const pair = (lang: string) =>
      (JSON.parse(readFileSync(join(i18n, lang, "common.json"), "utf-8")) as { pair: Record<string, string> }).pair;
    const english = Object.keys(pair("en"));
    expect(english.length).toBeGreaterThan(30);
    const formal: Record<string, RegExp> = { de: /\bSie\b/, es: /\busted\b/, fr: /\bvous\b/, zh: /您/ };
    const informal: Record<string, RegExp> = { de: /\b(du|dich|dir|dein\w*)\b/i, es: /\b(tú|tu|tus)\b/i, fr: /\b(tu|toi|ton|ta|tes)\b/i, zh: /你/ };
    for (const lang of ["de", "es", "fr", "zh"]) {
      const texts = pair(lang);
      expect(Object.keys(texts)).toEqual(english);
      expect(Object.values(texts).every((v) => v.trim() !== "")).toBe(true);
      const all = Object.values(texts).join(" ");
      expect(all).toMatch(formal[lang]);
      expect(all).not.toMatch(informal[lang]);
    }
  });
});

