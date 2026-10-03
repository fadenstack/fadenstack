/**
 * Letting go of a streaming reply when the reader scrolls up -- read from the
 * input, not from where the box stands.
 *
 * aefd9e1 decided from scroll events alone. A trackpad (or a smooth wheel)
 * scrolls in steps smaller than the "still at the bottom" slack, so every step
 * read as "at the bottom" and the next token put the box back: on the release
 * VM a reply still could not be scrolled away from. In a real browser with a
 * token every 15 ms, 8 px wheel steps ended 0 px from the bottom with that
 * logic and 1120 px up with this one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { followScroll, type Follower } from "./followScroll";

let box: HTMLDivElement;
let follower: Follower;
let changes: boolean[];

/** jsdom has no layout: a box 400 px high over *height* px of content. */
function content(height: number) {
  Object.defineProperty(box, "scrollHeight", { configurable: true, value: height });
}

function scrollTo(top: number) {
  box.scrollTop = top;
  box.dispatchEvent(new Event("scroll"));
}

beforeEach(() => {
  box = document.createElement("div");
  document.body.appendChild(box);
  Object.defineProperty(box, "clientHeight", { configurable: true, value: 400 });
  Object.defineProperty(box, "clientWidth", { configurable: true, value: 600 });
  content(1000);
  changes = [];
  follower = followScroll(box, (value) => changes.push(value));
  follower.follow();
});

afterEach(() => {
  follower.dispose();
  box.remove();
});

describe("followScroll", () => {
  it("follows new content from the bottom", () => {
    expect(box.scrollTop).toBe(1000);
    content(1400);
    follower.keepUp();
    expect(box.scrollTop).toBe(1400);
  });

  it("lets go at a wheel step too small to leave the bottom (the trackpad case)", () => {
    box.dispatchEvent(new WheelEvent("wheel", { deltaY: -8 }));
    scrollTo(992); // 8 px up: well inside the slack

    content(1400);
    follower.keepUp();
    expect(box.scrollTop).toBe(992);
    expect(follower.following()).toBe(false);
    expect(changes).toEqual([false]);
  });

  it("lets go when a finger drags the content down, or at an up key", () => {
    box.dispatchEvent(Object.assign(new Event("touchstart"), { touches: [{ clientY: 100 }] }));
    box.dispatchEvent(Object.assign(new Event("touchmove"), { touches: [{ clientY: 140 }] }));
    expect(follower.following()).toBe(false);

    follower.follow();
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "PageUp" }));
    expect(follower.following()).toBe(false);
  });

  it("lets go when the scrollbar is grabbed, not at a click in the conversation", () => {
    const at = (offsetX: number) => {
      const event = new Event("pointerdown") as PointerEvent;
      Object.defineProperty(event, "offsetX", { value: offsetX });
      box.dispatchEvent(event);
    };
    at(300);
    expect(follower.following()).toBe(true);
    at(605);
    expect(follower.following()).toBe(false);
  });

  it("follows again once scrolled back down to the bottom", () => {
    box.dispatchEvent(new WheelEvent("wheel", { deltaY: -100 }));
    scrollTo(400);
    expect(follower.following()).toBe(false);

    scrollTo(600); // 1000 - 600 - 400 = 0 left below
    expect(follower.following()).toBe(true);
    content(1300);
    follower.keepUp();
    expect(box.scrollTop).toBe(1300);
  });

  it("keeps following when content shrinks under it at the bottom", () => {
    content(900); // the streamed reply replaced by the stored one, a little shorter
    scrollTo(500); // the browser holds the box at the new bottom
    expect(follower.following()).toBe(true);
  });

  it("stops listening once disposed", () => {
    const onChange = vi.fn();
    const other = followScroll(box, onChange);
    other.dispose();
    box.dispatchEvent(new WheelEvent("wheel", { deltaY: -50 }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
