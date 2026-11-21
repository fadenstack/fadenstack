/**
 * A project's files, in its settings: listed, added, and removed after a question.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatApiError, chatApi } from "~/api/chatClient";
import type { ChatAttachment } from "~/api/chatTypes";
import ProjectFiles from "./ProjectFiles";

function file(id: string, filename: string, extra: Partial<ChatAttachment> = {}): ChatAttachment {
  return {
    id,
    tenant_id: "t",
    user_id: "u",
    session_id: null,
    project_id: "p1",
    filename,
    content_type: "application/pdf",
    size_bytes: 2048,
    extraction_status: "completed",
    scope: "project",
    page_count: null,
    truncated: false,
    created_at: "2026-11-20T10:00:00Z",
    ...extra,
  };
}

beforeEach(() => {
  vi.spyOn(chatApi, "listProjectAttachments").mockResolvedValue([]);
});

describe("ProjectFiles list", () => {
  it("says so when there are no files", async () => {
    render(<ProjectFiles projectId="p1" />);

    expect(await screen.findByTestId("project-files-empty")).toHaveTextContent("No files yet.");
    expect(chatApi.listProjectAttachments).toHaveBeenCalledWith("p1");
  });

  it("shows each file's size and whether its text was read", async () => {
    vi.spyOn(chatApi, "listProjectAttachments").mockResolvedValue([
      file("a1", "notes.pdf"),
      file("a2", "scan.pdf", { extraction_status: "failed" }),
      file("a3", "logo.png", { extraction_status: "skipped", size_bytes: 3 * 1024 * 1024 }),
      file("a4", "new.docx", { extraction_status: "pending" }),
    ]);
    render(<ProjectFiles projectId="p1" />);

    expect(await screen.findByText("notes.pdf")).toBeInTheDocument();
    expect(screen.getAllByText("2 KB")).toHaveLength(3);
    expect(screen.getByText("3 MB")).toBeInTheDocument();
    const states = screen.getAllByTestId("project-file-state").map((el) => el.textContent);
    expect(states).toEqual(["Text read", "Text could not be read", "Picture", "Reading text…"]);
    expect(screen.getByRole("list", { name: "Files" })).toBeInTheDocument();
  });

  it("says when the list could not be loaded, and tries again", async () => {
    const list = vi
      .spyOn(chatApi, "listProjectAttachments")
      .mockRejectedValueOnce(new Error("gateway down"))
      .mockResolvedValue([file("a1", "notes.pdf")]);
    render(<ProjectFiles projectId="p1" />);

    expect(await screen.findByText(/could not be loaded: gateway down/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("notes.pdf")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("ProjectFiles upload", () => {
  const pick = (name: string) =>
    fireEvent.change(screen.getByTestId("project-file-input"), {
      target: { files: [new File(["x"], name, { type: "application/pdf" })] },
    });

  it("shows the file as uploading, then in the list", async () => {
    let finish!: (v: Awaited<ReturnType<typeof chatApi.uploadProjectAttachment>>) => void;
    const upload = vi.spyOn(chatApi, "uploadProjectAttachment").mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<ProjectFiles projectId="p1" />);
    await screen.findByTestId("project-files-empty");

    pick("brief.pdf");

    expect(await screen.findByText("Uploading brief.pdf…")).toBeInTheDocument();
    expect(upload).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "brief.pdf" }));
    finish({ attachment: file("a9", "brief.pdf"), extracted_text_length: 10, token_estimate: 3 });

    expect(await screen.findByText("brief.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Uploading brief.pdf…")).toBeNull();
  });

  it("shows the gateway's message when a file is refused, and lets it be dismissed", async () => {
    vi.spyOn(chatApi, "uploadProjectAttachment").mockRejectedValue(
      new ChatApiError("File type not supported.", { code: "attachment_error", status: 415 }),
    );
    render(<ProjectFiles projectId="p1" />);
    await screen.findByTestId("project-files-empty");

    pick("tool.exe");

    expect(await screen.findByText("tool.exe could not be added: File type not supported.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss the message about tool.exe" }));
    expect(screen.queryByText(/tool.exe could not be added/)).toBeNull();
  });
});

describe("ProjectFiles remove", () => {
  beforeEach(() => {
    vi.spyOn(chatApi, "listProjectAttachments").mockResolvedValue([file("a1", "notes.pdf"), file("a2", "b.pdf")]);
  });

  it("asks first, says the file is deleted at once, and deletes nothing when cancelled", async () => {
    const del = vi.spyOn(chatApi, "deleteProjectAttachment").mockResolvedValue(undefined);
    render(<ProjectFiles projectId="p1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove notes.pdf" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/notes\.pdf is deleted at once/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText("notes.pdf")).toBeInTheDocument();
  });

  it("deletes the file once confirmed", async () => {
    const del = vi.spyOn(chatApi, "deleteProjectAttachment").mockResolvedValue(undefined);
    render(<ProjectFiles projectId="p1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove notes.pdf" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.queryByText("notes.pdf")).toBeNull());
    expect(del).toHaveBeenCalledWith("p1", "a1");
    expect(screen.getByText("b.pdf")).toBeInTheDocument();
  });

  it("drops a file the server no longer has (404) from the list", async () => {
    vi.spyOn(chatApi, "deleteProjectAttachment").mockRejectedValue(
      new ChatApiError("Not found", { status: 404 }),
    );
    render(<ProjectFiles projectId="p1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove notes.pdf" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(screen.queryByText("notes.pdf")).toBeNull());
    expect(screen.queryByText(/could not be removed/)).toBeNull();
  });

  it("keeps the file and says why when removing fails", async () => {
    vi.spyOn(chatApi, "deleteProjectAttachment").mockRejectedValue(new ChatApiError("Server error", { status: 500 }));
    render(<ProjectFiles projectId="p1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove notes.pdf" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("The file could not be removed: Server error")).toBeInTheDocument();
    expect(screen.getByText("notes.pdf")).toBeInTheDocument();
  });
});
