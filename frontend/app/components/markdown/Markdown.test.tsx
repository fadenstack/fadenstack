/**
 * Markdown in blocks: a finished block is not parsed again while a reply streams.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const parsed: string[] = [];
vi.mock("react-markdown", async (importOriginal) => {
  const real = (await importOriginal()) as { default: (props: { children: string }) => unknown };
  return {
    default: (props: { children: string }) => {
      parsed.push(props.children);
      return real.default(props);
    },
  };
});

import Markdown, { splitBlocks } from "./Markdown";

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
