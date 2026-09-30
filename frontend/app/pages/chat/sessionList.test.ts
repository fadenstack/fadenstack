import { describe, expect, it } from "vitest";

import type { ChatSession } from "~/api/chatTypes";

import { mergeSessions, withSession, withWriting } from "./sessionList";

function chat(id: string, title: string | null, updated_at: string): ChatSession {
  return {
    id, title, updated_at, tenant_id: "t", user_id: "u", project_id: null, status: "active",
    created_at: "2026-09-26T10:00:00Z",
  };
}

describe("the chat list", () => {
  it("keeps a chat started while the list was loading", () => {
    const started = chat("new", null, "2026-09-26T12:00:05Z");
    const fetched = [chat("a", "A", "2026-09-26T11:00:00Z")];
    expect(mergeSessions(fetched, [started]).map((s) => s.id)).toEqual(["new", "a"]);
  });

  it("keeps the newer copy of a chat both have", () => {
    const renamed = chat("a", "Renamed", "2026-09-26T12:00:00Z");
    const fetched = [chat("a", "A", "2026-09-26T11:00:00Z")];
    expect(mergeSessions(fetched, [renamed])[0].title).toBe("Renamed");
    expect(mergeSessions([renamed], [chat("a", "A", "2026-09-26T11:00:00Z")])[0].title).toBe("Renamed");
  });

  it("takes a chat's update even when the chat is not listed yet", () => {
    // The reply's title arrived for a chat the loading list had replaced.
    const titled = chat("new", "Name one colour of the sky", "2026-09-26T12:00:06Z");
    expect(withSession([chat("a", "A", "2026-09-26T11:00:00Z")], titled).map((s) => s.title)).toEqual([
      "Name one colour of the sky",
      "A",
    ]);
    expect(withSession([chat("new", null, "2026-09-26T12:00:05Z")], titled)).toEqual([titled]);
  });
});

describe("the chats a reply is being written in", () => {
  it("are the gateway's word, whichever copy of the chat is newer", () => {
    const renamed = { ...chat("a", "Renamed", "2026-09-26T12:00:00Z"), writing: true };
    const fetched = [{ ...chat("a", "A", "2026-09-26T11:00:00Z"), writing: false }];
    expect(mergeSessions(fetched, [renamed])[0]).toMatchObject({ title: "Renamed", writing: false });
  });

  it("are marked by the page that writes in one", () => {
    const list = [chat("a", "A", "2026-09-26T11:00:00Z"), chat("b", "B", "2026-09-26T11:00:00Z")];
    expect(withWriting(list, "b", true).map((s) => Boolean(s.writing))).toEqual([false, true]);
    // A chat whose mark does not change is left as it is (no re-render for it).
    expect(withWriting(list, "a", false)[0]).toBe(list[0]);
  });
});

