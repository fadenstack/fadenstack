import { describe, expect, it } from "vitest";

import { picturesOf, withPictures } from "./messagePictures";

const DATA = "data:image/png;base64,AAAA";

describe("picturesOf", () => {
  it("reads pictures held in the message, in both shapes", () => {
    expect(
      picturesOf([
        { type: "text", text: "hi" },
        { type: "image_url", image_url: { url: DATA } },
        { type: "input_image", image_url: "blob:http://x/1", filename: "a.png" },
      ]),
    ).toEqual([{ url: DATA }, { url: "blob:http://x/1", name: "a.png" }]);
  });

  it("does not fetch a picture from another site", () => {
    expect(picturesOf([{ type: "image_url", image_url: { url: "https://tracker.example/a.png" } }])).toEqual([]);
    expect(picturesOf([{ type: "image_url", image_url: { url: "javascript:alert(1)" } }])).toEqual([]);
    expect(picturesOf(null)).toEqual([]);
  });
});

describe("withPictures", () => {
  const msg = (id: string, content: string, parts: unknown[] | null) => ({
    id, role: "user", content, content_parts: parts,
  });
  const held = [{ type: "image_url", image_url: { url: "blob:http://x/1" } }];

  it("puts a sent message's picture on the gateway's copy of it", () => {
    const out = withPictures([msg("tmp-1", "hi", held)], [msg("u1", "hi", null)]);
    expect(out[0].content_parts).toEqual(held);
    // And again, on the next read, by id.
    expect(withPictures(out, [msg("u1", "hi", null)])[0].content_parts).toEqual(held);
  });

  it("leaves other messages alone", () => {
    const after = [msg("u2", "other", null)];
    expect(withPictures([msg("tmp-1", "hi", held)], after)).toEqual(after);
  });
});
