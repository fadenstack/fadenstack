/**
 * Markdown in blocks: a finished block is not parsed again while a reply streams.
 * Pictures load only from the page's own origin, data:image and blob: URLs.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const parsed: string[] = [];
vi.mock("react-markdown", async (importOriginal) => {
  const real = (await importOriginal()) as Record<string, unknown> & {
    default: (props: { children: string }) => unknown;
  };
  return {
    ...real,
    default: (props: { children: string }) => {
      parsed.push(props.children);
      return real.default(props);
    },
  };
});

import Markdown, { isLocalImageSrc, splitBlocks } from "./Markdown";

const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

describe("splitBlocks", () => {
  it("splits a reply into its top-level blocks", () => {
    const text = "Intro **text**.\n\n1. one\n2. two\n\n```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n";
    const blocks = splitBlocks(text);
    expect(blocks).toHaveLength(4);
    expect(blocks[1]).toContain("1. one");
    expect(blocks[1]).toContain("2. two");
    expect(blocks[2]).toContain("print(1)");
  });

  it("keeps a text with reference links or footnotes whole: its blocks point at each other", () => {
    const text = "See [the docs][d] and this[^1].\n\n[d]: https://example.com\n[^1]: A note.\n";
    expect(splitBlocks(text)).toEqual([text]);
  });
});

describe("Markdown", () => {
  it("parses only the block being written when the text grows", () => {
    const first = "First paragraph, finished.\n\n- a\n- b\n\nThe last one";
    const { rerender } = render(<Markdown streaming>{first}</Markdown>);
    parsed.length = 0;

    rerender(<Markdown streaming>{first + " goes on"}</Markdown>);

    expect(parsed).toEqual(["The last one goes on"]);
    expect(screen.getByText("First paragraph, finished.")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("closes half-written syntax while streaming, and only then", () => {
    const { container, rerender } = render(<Markdown streaming>{"A **bold start"}</Markdown>);
    expect(container.querySelector("strong")?.textContent).toBe("bold start");

    rerender(<Markdown>{"A **bold start"}</Markdown>);
    expect(container.querySelector("strong")).toBeNull();
    expect(container.textContent).toBe("A **bold start");
  });

  it("renders lists as lists, headings as headings, and tables as tables", () => {
    const { container } = render(
      <Markdown>{"## Steps\n\n1. one\n2. two\n\n- x\n\n| a |\n|---|\n| 1 |\n"}</Markdown>,
    );
    expect(container.querySelector("h2")?.textContent).toBe("Steps");
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.querySelectorAll("ul > li")).toHaveLength(1);
    expect(container.querySelector("table td")?.textContent).toBe("1");
  });
});

describe("isLocalImageSrc", () => {
  const origin = "https://console.example";

  it("takes the page's own origin, relative paths included", () => {
    expect(isLocalImageSrc("/api/chat/attachments/1/content", origin)).toBe(true);
    expect(isLocalImageSrc("picture.png", origin)).toBe(true);
    expect(isLocalImageSrc("https://console.example/a.png", origin)).toBe(true);
  });

  it("takes data:image and blob: URLs, which load nothing from a host", () => {
    expect(isLocalImageSrc(PNG, origin)).toBe(true);
    expect(isLocalImageSrc("blob:https://console.example/0f7a", origin)).toBe(true);
    expect(isLocalImageSrc("data:text/html,<b>x</b>", origin)).toBe(false);
  });

  it("refuses every other host, scheme or port", () => {
    expect(isLocalImageSrc("https://evil.example/?q=secret", origin)).toBe(false);
    expect(isLocalImageSrc("//evil.example/x.png", origin)).toBe(false);
    expect(isLocalImageSrc("http://console.example/a.png", origin)).toBe(false);
    expect(isLocalImageSrc("https://console.example:8443/a.png", origin)).toBe(false);
    expect(isLocalImageSrc("https://console.example.evil.example/a.png", origin)).toBe(false);
    expect(isLocalImageSrc("javascript:alert(1)", origin)).toBe(false);
    expect(isLocalImageSrc("", origin)).toBe(false);
    expect(isLocalImageSrc(undefined, origin)).toBe(false);
  });
});

describe("Markdown pictures", () => {
  it("shows a picture from another host as a link and loads nothing", () => {
    const { container } = render(<Markdown>{"Here: ![the chart](https://evil.example/leak?q=my%20secret)"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: "the chart (evil.example)" });
    expect(link).toHaveAttribute("href", "https://evil.example/leak?q=my%20secret");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(link).toHaveAttribute("title", "A picture from evil.example, not loaded. Click to open it.");
  });

  it("names a picture without alt text 'image'", () => {
    render(<Markdown>{"![](https://evil.example/p.png)"}</Markdown>);
    expect(screen.getByRole("link", { name: "image (evil.example)" })).toBeTruthy();
  });

  it("does the same for reference pictures, while streaming, and for a protocol-relative URL", () => {
    const { container, rerender } = render(<Markdown>{"![a][r]\n\n[r]: https://evil.example/r.png\n"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a[data-remote-image]")).not.toBeNull();

    rerender(<Markdown streaming>{"Look ![b](https://evil.example/half"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();

    rerender(<Markdown>{"![c](//evil.example/c.png)"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "c (evil.example)" })).toBeTruthy();
  });

  it("keeps pictures from the page's own origin, data:image and blob: URLs", () => {
    const own = `${window.location.origin}/own.png`;
    const { container } = render(
      <Markdown>{`![rel](/api/picture.png) ![abs](${own}) ![inline](${PNG}) ![obj](blob:${window.location.origin}/9c1d)`}</Markdown>,
    );
    const sources = [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));
    expect(sources).toEqual(["/api/picture.png", own, PNG, `blob:${window.location.origin}/9c1d`]);
    expect(container.querySelector("a")).toBeNull();
  });

  it("loads no picture from raw HTML and no script URL", () => {
    const { container } = render(
      <Markdown>{'<img src="https://evil.example/raw.png">\n\n![x](javascript:alert(1))'}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
    expect(container.textContent).toContain("x");
  });

  it("leaves links as they were", () => {
    render(<Markdown>{"[docs](https://example.com/docs) and [bad](javascript:alert(1))"}</Markdown>);
    expect(screen.getByRole("link", { name: "docs" })).toHaveAttribute("href", "https://example.com/docs");
    expect(screen.getByText("bad").getAttribute("href") ?? "").toBe("");
  });
});
