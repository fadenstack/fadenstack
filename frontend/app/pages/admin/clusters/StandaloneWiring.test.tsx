import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StandaloneWiring } from "./StandaloneWiring";

describe("StandaloneWiring", () => {
  it("draws gateway, Mac, engine and model in one row", () => {
    render(<StandaloneWiring host="mac-1.local" models={["Qwen3 0.6B"]} />);
    const row = screen.getByTestId("standalone-wiring");
    for (const text of ["Gateway", "mac-1.local", "vLLM-Metal", "Qwen3 0.6B"]) expect(row).toHaveTextContent(text);
  });

  it("says so when no model runs yet", () => {
    render(<StandaloneWiring host="mac-1.local" models={[]} />);
    expect(screen.getByTestId("standalone-wiring")).toHaveTextContent("No model yet");
  });
});
