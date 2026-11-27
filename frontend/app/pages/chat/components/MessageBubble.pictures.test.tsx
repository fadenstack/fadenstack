import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import MessageBubble from "./MessageBubble";

describe("a message with a picture", () => {
  it("shows the picture as a thumbnail with a name for a screen reader", () => {
    render(
      <MessageBubble
        role="user"
        content="What is this?"
        pictures={[{ url: "blob:http://x/1", name: "cat.png" }]}
      />,
    );

    const img = screen.getByRole("img", { name: "Attached picture: cat.png" });
    expect(img).toHaveAttribute("src", "blob:http://x/1");
    expect(screen.getByText("What is this?")).toBeInTheDocument();
  });

  it("shows no thumbnails without pictures", () => {
    render(<MessageBubble role="user" content="hi" />);

    expect(screen.queryByTestId("message-pictures")).toBeNull();
  });
});
