/**
 * A collection is open to everyone, or only to chosen people and groups: the
 * form shows the picker only for the second, and an open collection sends no
 * grants (old ones would only confuse whoever restricts it again).
 */
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  accessPayloadOf,
  accessValueOf,
  CollectionAccessFields,
  type CollectionAccessValue,
} from "./CollectionAccessFields";

function Harness({ initial }: { initial: CollectionAccessValue }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <CollectionAccessFields value={value} onChange={setValue} />
      <output data-testid="payload">{JSON.stringify(accessPayloadOf(value))}</output>
    </>
  );
}

describe("who can read a collection", () => {
  it("reads a collection's grants as people with names", () => {
    const value = accessValueOf({
      access: "restricted",
      grants: [{ principal_type: "group", principal_id: "g1", label: "HR" }],
    });
    expect(value).toEqual({ access: "restricted", people: [{ type: "group", id: "g1", label: "HR" }] });
    expect(accessValueOf()).toEqual({ access: "everyone", people: [] });
  });

  it("shows the people only for a restricted collection, and sends none for an open one", async () => {
    render(
      <Harness
        initial={{ access: "restricted", people: [{ type: "group", id: "g1", label: "HR" }] }}
      />,
    );
    expect(screen.getByText("HR")).toBeInTheDocument();
    expect(JSON.parse(screen.getByTestId("payload").textContent ?? "")).toEqual({
      access: "restricted",
      grants: [{ principal_type: "group", principal_id: "g1" }],
    });

    await userEvent.click(screen.getByLabelText("Everyone who uses the chat"));

    expect(screen.queryByText("HR")).not.toBeInTheDocument();
    expect(JSON.parse(screen.getByTestId("payload").textContent ?? "")).toEqual({
      access: "everyone",
      grants: [],
    });
  });
});
