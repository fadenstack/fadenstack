/**
 * Checking content as a person reads it: the verdict in plain words, each
 * finding with its layer, method and ids, a signed result to download, and
 * every refusal as a sentence. Signed in, texts and files; on the public
 * page, pictures and files only.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ApiError } from "~/api/http";
import type { ContentCheckBody, ContentCheckResult } from "~/api/provenance";
import { scoreLabel } from "~/lib/contentCheck";

import ContentCheck from "./ContentCheck";

const MAX = 20 * 1024 * 1024;

const RESULT: ContentCheckResult = {
  contract: "provenance/v1",
  verdict: "ai-generated",
  findings: [
    {
      layer: "metadata",
      method: "receipt-hash",
      found: true,
      detail: "This text is exactly what qwen3 generated on 2026-10-18.",
      record_id: null,
      receipt_id: "6f1c2b9e-0000-4000-8000-000000000001",
      score: null,
    },
    {
      layer: "watermark",
      method: "acme-mark",
      found: false,
      detail: "Something an engine plugin looked for.",
      record_id: "0d9a1b2c-0000-4000-8000-000000000002",
      score: 0.42,
    },
  ],
  content_hash: "ab".repeat(32),
  detector: "fadenstack-provenance/0.5.0",
  checked_at: "2026-10-18T12:00:00Z",
  signature: null,
};

let submit: Mock<(body: ContentCheckBody) => Promise<ContentCheckResult>>;

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

beforeEach(() => {
  submit = vi.fn(async (body: ContentCheckBody) => ({
    ...RESULT,
    signature: body.signed ? "header.payload.signature" : null,
  }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderCheck(mode: "account" | "public" = "account") {
  return render(<ContentCheck mode={mode} submit={submit} maxBytes={MAX} />);
}

async function checkText(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "Text to check" }), text);
  await user.click(screen.getByTestId("content-check-submit"));
  return user;
}

describe("ContentCheck, signed in", () => {
  it("checks a pasted text and says what was found in plain words", async () => {
    renderCheck();
    await checkText("A paragraph to check.");

    expect(submit).toHaveBeenCalledWith({ text: "A paragraph to check.", signed: false });
    const verdict = await screen.findByTestId("content-check-verdict");
    expect(verdict).toHaveAttribute("data-verdict", "ai-generated");
    expect(verdict).toHaveTextContent("Marked as AI-generated");
    expect(verdict).toHaveTextContent("the mark can be confirmed");

    const [receipt, plugin] = screen.getAllByTestId("content-check-finding");
    expect(receipt).toHaveTextContent("Matches a generation receipt");
    expect(receipt).toHaveTextContent("Metadata");
    expect(receipt).toHaveTextContent("Points to AI generation");
    expect(receipt).toHaveTextContent("This text is exactly what qwen3 generated on 2026-10-18.");
    expect(receipt).toHaveTextContent("Receipt 6f1c2b9e-0000-4000-8000-000000000001");
    // A method the console does not know is named by its id.
    expect(plugin).toHaveTextContent("acme-mark");
    expect(plugin).toHaveTextContent("Watermark");
    expect(plugin).toHaveTextContent("Does not say AI made it");
    expect(plugin).toHaveTextContent("Record 0d9a1b2c-0000-4000-8000-000000000002");
    expect(plugin).toHaveTextContent("Measure: 0.42");

    expect(screen.getByTestId("content-check-hash")).toHaveTextContent(`SHA-256 of the content: ${"ab".repeat(32)}`);
    expect(screen.getByText(/by fadenstack-provenance\/0\.5\.0/)).toBeInTheDocument();
  });

  it("says a watermark's tiny p-value as a power of ten, not in seventeen digits", () => {
    expect(scoreLabel(6.365925187123848e-9)).toBe("6.4e-9");
    expect(scoreLabel(0.06390998445107827)).toBe("0.064");
    expect(scoreLabel(0.42)).toBe("0.42");
    expect(scoreLabel(1)).toBe("1");
    expect(scoreLabel(0)).toBe("0");
  });

  it.each([
    ["no-mark-found", "No mark found", "does not prove that a person made it"],
    ["inconclusive", "Inconclusive", "none that can be confirmed here"],
  ] as const)("says what %s means", async (verdict, title, body) => {
    submit.mockResolvedValue({ ...RESULT, verdict, findings: [] });
    renderCheck();
    await checkText("x");

    const shown = await screen.findByTestId("content-check-verdict");
    expect(shown).toHaveTextContent(title);
    expect(shown).toHaveTextContent(body);
    expect(screen.getByTestId("content-check-no-findings")).toBeInTheDocument();
  });

  it("checks a chosen picture as base64 with its media type", async () => {
    const user = userEvent.setup();
    renderCheck();
    await user.click(screen.getByRole("tab", { name: "Picture or file" }));
    const picture = new File([new Uint8Array([137, 80, 78, 71])], "chart.png", { type: "image/png" });
    await user.upload(screen.getByTestId("content-check-file"), picture);
    expect(screen.getByTestId("content-check-file-name")).toHaveTextContent("chart.png (1 KB)");
    await user.click(screen.getByTestId("content-check-submit"));

    await screen.findByTestId("content-check-result");
    expect(submit).toHaveBeenCalledWith({ file: "iVBORw==", media_type: "image/png", signed: false });
  });

  it("names a Markdown file's type when the browser gives none", async () => {
    const user = userEvent.setup();
    renderCheck();
    await user.click(screen.getByRole("tab", { name: "Picture or file" }));
    await user.upload(screen.getByTestId("content-check-file"), new File(["# notes"], "notes.md", { type: "" }));
    await user.click(screen.getByTestId("content-check-submit"));

    await screen.findByTestId("content-check-result");
    expect(submit.mock.calls[0][0]).toMatchObject({ media_type: "text/markdown" });
  });

  it("asks for the signed result and saves it as JSON", async () => {
    const created: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return "blob:result";
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    renderCheck();
    const user = await checkText("A paragraph to check.");
    await screen.findByTestId("content-check-result");
    await user.click(screen.getByTestId("content-check-download"));

    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenLastCalledWith({ text: "A paragraph to check.", signed: true });
    const saved = JSON.parse(await readBlob(created[0])) as ContentCheckResult;
    expect(saved.signature).toBe("header.payload.signature");
    expect(saved.verdict).toBe("ai-generated");
    expect(created[0].type).toBe("application/json");
  });

  it("says so when the result cannot be signed", async () => {
    submit.mockResolvedValue(RESULT); // never signed
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderCheck();
    const user = await checkText("x");
    await screen.findByTestId("content-check-result");
    await user.click(screen.getByTestId("content-check-download"));

    expect(await screen.findByTestId("content-check-notice")).toHaveTextContent("has no signing key");
    expect(click).not.toHaveBeenCalled();
  });

  it.each([
    [new ApiError("busy", 503, "busy"), "The marking engine is busy."],
    [new ApiError("Too many", 429, "rate_limited"), "Too many checks in a minute."],
    [new ApiError("Too many", 429), "Too many checks in a minute."],
    [new ApiError("no key", 503, "no_signing_key"), "has no keys set up"],
    [new ApiError("off", 503, "module_off"), "the provenance module is switched off"],
    [new ApiError("big", 413, "too_large"), "larger than 20 MB"],
    [new ApiError("Something odd", 500, "brand_new_code"), "The check failed: Something odd"],
  ])("says what a refusal means (%s)", async (error, sentence) => {
    submit.mockRejectedValue(error);
    renderCheck();
    await checkText("x");

    expect(await screen.findByTestId("content-check-error")).toHaveTextContent(sentence);
    expect(screen.queryByTestId("content-check-result")).not.toBeInTheDocument();
  });

  it("does not send a file over the size limit", async () => {
    const user = userEvent.setup();
    render(<ContentCheck mode="account" submit={submit} maxBytes={4} />);
    await user.click(screen.getByRole("tab", { name: "Picture or file" }));
    await user.upload(screen.getByTestId("content-check-file"), new File(["12345"], "big.png", { type: "image/png" }));
    await user.click(screen.getByTestId("content-check-submit"));

    expect(await screen.findByTestId("content-check-error")).toHaveTextContent("cannot be checked");
    expect(submit).not.toHaveBeenCalled();
  });

  it("explains what is checked and that nothing is kept", () => {
    renderCheck();
    const explanation = screen.getByTestId("content-check-explanation");
    expect(explanation).toHaveTextContent("What is checked");
    expect(explanation).toHaveTextContent("text watermarks");
    expect(explanation).toHaveTextContent("Nothing you submit is kept or logged");
    expect(explanation).toHaveTextContent("does not prove that a person made the content");
  });

  it("starts over", async () => {
    renderCheck();
    const user = await checkText("x");
    await screen.findByTestId("content-check-result");
    await user.click(screen.getByRole("button", { name: "Check something else" }));

    expect(screen.queryByTestId("content-check-result")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Text to check" })).toHaveValue("");
  });
});

describe("ContentCheck, public page", () => {
  it("takes pictures and files, not texts", () => {
    renderCheck("public");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Text to check" })).not.toBeInTheDocument();
    expect(screen.getByTestId("content-check-file")).toHaveAttribute("accept", expect.not.stringContaining(".txt"));
    expect(screen.getByText(/Word, PowerPoint and Excel documents and PDF, up to 20 MB/)).toBeInTheDocument();
    expect(within(screen.getByTestId("content-check-explanation")).queryByText(/text watermarks/)).toBeNull();
  });

  it("refuses a text file before sending it", async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderCheck("public");
    await user.upload(screen.getByTestId("content-check-file"), new File(["notes"], "notes.txt", { type: "text/plain" }));
    await user.click(screen.getByTestId("content-check-submit"));

    expect(await screen.findByTestId("content-check-error")).toHaveTextContent(
      "Texts are checked here only for people with an account.",
    );
    expect(submit).not.toHaveBeenCalled();
  });

  it("checks a picture", async () => {
    const user = userEvent.setup();
    renderCheck("public");
    await user.upload(screen.getByTestId("content-check-file"), new File(["jpg"], "photo.jpg", { type: "image/jpeg" }));
    await user.click(screen.getByTestId("content-check-submit"));

    await screen.findByTestId("content-check-result");
    expect(submit).toHaveBeenCalledWith({ file: "anBn", media_type: "image/jpeg", signed: false });
  });
});
