import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { PricingEntry } from "~/api/observability";
import { findPrice } from "~/lib/modelPrice";
import { PriceField, formatPerMillion, parsePrice, perMillion, perThousand } from "./PriceField";

function Harness({ initial = null }: { initial?: number | null }) {
  const [value, setValue] = useState<number | null>(initial);
  return (
    <>
      <PriceField label="Input price" value={value} onChange={setValue} />
      <output data-testid="value">{value === null ? "null" : String(value)}</output>
    </>
  );
}

describe("PriceField", () => {
  it("takes a decimal price as typed", async () => {
    // Found on the pricing tab: "0.00015" came out as "000015", "2.5" as "05".
    render(<Harness />);
    const field = screen.getByLabelText(/input price/i);
    await userEvent.type(field, "0.15");
    expect(field).toHaveValue("0.15");
    expect(screen.getByTestId("value")).toHaveTextContent("0.15");
  });

  it("reads a comma as the decimal point and ignores letters", async () => {
    render(<Harness />);
    const field = screen.getByLabelText(/input price/i);
    await userEvent.type(field, "2,5x");
    expect(field).toHaveValue("2,5");
    expect(screen.getByTestId("value")).toHaveTextContent("2.5");
  });

  it("can be emptied, which means no price", async () => {
    render(<Harness initial={3} />);
    const field = screen.getByLabelText(/input price/i);
    await userEvent.clear(field);
    expect(field).toHaveValue("");
    expect(screen.getByTestId("value")).toHaveTextContent("null");
  });
});

describe("price units", () => {
  it("shows per 1M what is stored per 1K, without float dust", () => {
    expect(perMillion("0.00015000")).toBe(0.15);
    expect(perThousand(0.15)).toBe(0.00015);
    expect(perMillion(perThousand(0.1))).toBe(0.1);
    expect(parsePrice("")).toBeNull();
    expect(parsePrice("1.2.3")).toBeNull();
    expect(formatPerMillion(null)).toBe("—");
  });
});

function entry(provider: string, model: string): PricingEntry {
  return {
    id: `${provider}/${model}`, provider, model, input_price_per_1k: "0.0001", output_price_per_1k: "0.0004",
    currency: "USD", effective_from: "", active: true, source: "admin", notes: null, created_at: "", updated_at: "",
  };
}

describe("findPrice", () => {
  it("takes the provider's own entry, else the model's only one, as the gateway does", () => {
    const entries = [entry("openrouter", "deepseek/deepseek-v4.1-flash"), entry("openai", "gpt-4.1"), entry("azure", "gpt-4.1")];
    expect(findPrice(entries, "openrouter", "deepseek/deepseek-v4.1-flash")?.id).toBe("openrouter/deepseek/deepseek-v4.1-flash");
    expect(findPrice(entries, "my-proxy", "deepseek/deepseek-v4.1-flash")?.id).toBe("openrouter/deepseek/deepseek-v4.1-flash");
    expect(findPrice(entries, "openai", "gpt-4.1")?.id).toBe("openai/gpt-4.1");
    expect(findPrice(entries, "my-proxy", "gpt-4.1")).toBeNull();
  });
});
