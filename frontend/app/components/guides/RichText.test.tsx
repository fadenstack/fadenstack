import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RichText } from "./RichText";

describe("RichText", () => {
  it("renders the few emphasis tags", () => {
    const { container } = render(
      <RichText text="Click <strong>Next</strong>, then <em>wait</em>: <code>gpu-room</code> <kbd>F1</kbd>" />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("Next");
    expect(container.querySelector("em")?.textContent).toBe("wait");
    expect(container.querySelector("code")?.textContent).toBe("gpu-room");
    expect(container.querySelector("kbd")?.textContent).toBe("F1");
    expect(container.textContent).toBe("Click Next, then wait: gpu-room F1");
  });

  it("shows anything else as text, never as markup", () => {
    const { container } = render(<RichText text={'<img src=x onerror="alert(1)"> and <b>bold</b>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe('<img src=x onerror="alert(1)"> and <b>bold</b>');
  });

  it("splits paragraphs on a blank line", () => {
    const { container } = render(<RichText text={"First.\n\nSecond."} />);
    expect(container.querySelectorAll("span")).toHaveLength(1);
    expect(container.textContent).toBe("First.Second.");
  });
});
