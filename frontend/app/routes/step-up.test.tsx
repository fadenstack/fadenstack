/**
 * The page a chat app opens to sign the person in again before Bypass is switched on: the link's code goes in a
 * body and leaves the address bar, the sign-in and the decision are separate steps, and only allow-listed words
 * are shown.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { renderPage } from "~/test/renderPage";

import StepUp from "./step-up";

let fetchMock: MockInstance<typeof fetch>;

const BASE = {
  password: false, providers: [], agent: null, device: null, longest_minutes: null, signed_in_as: null,
  method: null, until: null,
};
const SIGN_IN = { ...BASE, state: "sign_in", password: true };
const DECIDE = {
  ...BASE, state: "decide", agent: "Office agent", device: "Anna's laptop", longest_minutes: 30,
  signed_in_as: "anna@example.com", method: "password", until: "2026-11-09T10:05:00Z",
};

/** The answers by route, in order; every call is recorded in `fetchMock`. */
function answers(routes: Record<string, Array<{ status?: number; body: unknown }>>) {
  fetchMock.mockImplementation(async (input, init) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const next = routes[key]?.shift();
    if (!next) throw new Error(`unexpected ${key}`);
    return new Response(JSON.stringify(next.body), { status: next.status ?? 200 });
  });
}

const posted = (path: string) =>
  fetchMock.mock.calls.filter(([url, init]) => url === `/api/auth/step-up${path}` && init?.method === "POST");

function opened(address: string) {
  window.history.replaceState(null, "", address);
  renderPage(<StepUp />, { path: "/step-up" });
}

beforeEach(() => {
  fetchMock = vi.spyOn(globalThis, "fetch");
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

function signIn(email: string, password: string) {
  fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: password } });
}

describe("opening the page", () => {
  it("posts the code from the fragment in a body and takes it out of the address", async () => {
    answers({ "POST /api/auth/step-up/begin": [{ body: SIGN_IN }] });
    opened("/step-up#code=ABCD-2345");
    expect(await screen.findByText(/Sign in again to switch approvals off in a chat/)).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/step-up");
    const [url, init] = posted("/begin")[0];
    expect(url).toBe("/api/auth/step-up/begin");
    expect(JSON.parse(String(init?.body))).toEqual({ code: "ABCD-2345" });
    expect(String(url)).not.toContain("ABCD");
    expect(init?.credentials).toBe("same-origin");
  });

  it("asks where the step-up stands when there is no code", async () => {
    answers({ "GET /api/auth/step-up": [{ body: { ...BASE, state: "expired" } }] });
    opened("/step-up");
    expect(await screen.findByText("This sign-in has ended. Start it again in the app.")).toBeInTheDocument();
  });

  it("words a provider's refusal from the address, drops it and asks for the state", async () => {
    answers({ "GET /api/auth/step-up": [{ body: SIGN_IN }] });
    opened("/step-up?refused=not_fresh");
    expect(
      await screen.findByText("The sign-in provider did not confirm a fresh sign-in. Sign in again."),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("");
    await screen.findByLabelText(/^Email/);
  });

  it("shows no words a crafted refusal puts in the address", async () => {
    answers({ "GET /api/auth/step-up": [{ body: SIGN_IN }] });
    opened("/step-up?refused=Call%20%2B1%20555%200100%20now");
    expect(await screen.findByText("The sign-in did not work.")).toBeInTheDocument();
    expect(screen.queryByText(/555 0100/)).not.toBeInTheDocument();
  });
});

describe("signing in again and deciding", () => {
  it("posts the password form to the password route only, then shows what is approved", async () => {
    answers({
      "POST /api/auth/step-up/begin": [{ body: SIGN_IN }],
      "POST /api/auth/step-up/password": [{ body: DECIDE }],
    });
    opened("/step-up#code=ABCD-2345");
    await screen.findByLabelText(/^Email/);
    signIn("anna@example.com", "the-password");
    // Enter in the password field submits the form: the password route, nothing else.
    fireEvent.submit(screen.getByLabelText(/^Password/));
    expect(await screen.findByText("Office agent")).toBeInTheDocument();
    expect(screen.getByText("Anna's laptop")).toBeInTheDocument();
    expect(screen.getByText("For at most 30 minutes per chat")).toBeInTheDocument();
    expect(screen.getByText("Signed in again as anna@example.com")).toBeInTheDocument();
    expect(screen.getByText(/^Decide before /)).toBeInTheDocument();
    expect(JSON.parse(String(posted("/password")[0][1]?.body))).toEqual({
      email: "anna@example.com", password: "the-password",
    });
    expect(posted("/approve")).toHaveLength(0);
    expect(posted("/deny")).toHaveLength(0);
  });

  it("approves only when Approve is pressed", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: DECIDE }],
      "POST /api/auth/step-up/approve": [{ body: { ...BASE, state: "approved" } }],
    });
    opened("/step-up");
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toHaveAttribute("type", "button");
    expect(screen.getByRole("button", { name: "Deny" })).toHaveAttribute("type", "button");
    expect(posted("/approve")).toHaveLength(0);
    fireEvent.click(approve);
    expect(await screen.findByText("Approved. Go back to the app: it switches Bypass on.")).toBeInTheDocument();
    expect(posted("/approve")).toHaveLength(1);
  });

  it("denies when Deny is pressed", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: DECIDE }],
      "POST /api/auth/step-up/deny": [{ body: { ...BASE, state: "denied" } }],
    });
    opened("/step-up");
    fireEvent.click(await screen.findByRole("button", { name: "Deny" }));
    expect(await screen.findByText("Denied. Bypass stays off.")).toBeInTheDocument();
  });

  it("goes to a provider that must sign the person in anew", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, href: "http://localhost:3000/step-up" });
    answers({
      "GET /api/auth/step-up": [
        { body: { ...SIGN_IN, providers: [{ key: "entra", name: "Entra", kind: "redirect", posts_back: false }] } },
      ],
      "POST /api/auth/step-up/sso/entra": [{ body: { redirect_url: "https://login.example.com/authorize?x=1" } }],
    });
    opened("/step-up");
    fireEvent.click(await screen.findByRole("button", { name: "Sign in with Entra" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://login.example.com/authorize?x=1"));
    vi.unstubAllGlobals();
  });
});

describe("refusals", () => {
  it("words wrong_credentials, never the server's own text", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: SIGN_IN }],
      "POST /api/auth/step-up/password": [
        { status: 400, body: { detail: { code: "wrong_credentials", message: "SERVER WORDS" } } },
      ],
    });
    opened("/step-up");
    await screen.findByLabelText(/^Email/);
    signIn("anna@example.com", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(
      await screen.findByText("These are not the sign-in details of the account the app uses."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/SERVER WORDS/)).not.toBeInTheDocument();
  });

  it("says how long to wait after too many tries", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: SIGN_IN }],
      "POST /api/auth/step-up/password": [
        { status: 429, body: { detail: { code: "too_many_tries", message: "x", retry_after: 42 } } },
      ],
    });
    opened("/step-up");
    await screen.findByLabelText(/^Email/);
    signIn("anna@example.com", "x");
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(await screen.findByText("Too many tries. Wait 42 seconds.")).toBeInTheDocument();
  });

  it("shows the denied state when the step-up was denied for wrong sign-ins", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: SIGN_IN }],
      "POST /api/auth/step-up/password": [{ status: 400, body: { detail: { code: "denied", message: "x" } } }],
    });
    opened("/step-up");
    await screen.findByLabelText(/^Email/);
    signIn("anna@example.com", "x");
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(await screen.findByText("Denied. Bypass stays off.")).toBeInTheDocument();
  });

  it("asks for a new sign-in when the old one is too old, and reads the state again", async () => {
    answers({
      "GET /api/auth/step-up": [{ body: DECIDE }, { body: SIGN_IN }],
      "POST /api/auth/step-up/approve": [
        { status: 409, body: { detail: { code: "sign_in_too_old", message: "x" } } },
      ],
    });
    opened("/step-up");
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Your sign-in is older than 5 minutes. Sign in again.")).toBeInTheDocument();
    expect(await screen.findByLabelText(/^Email/)).toBeInTheDocument();
  });
});
