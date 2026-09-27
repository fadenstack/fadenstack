import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearDashboardQueries, setDashboardQueryOwner, useDashboardQuery } from "./dashboardQuery";

function Reader({ id, queryKey, fetcher }: { id: string; queryKey: string; fetcher: () => Promise<string> }) {
  const q = useDashboardQuery(queryKey, fetcher, { refreshMs: 60_000 });
  return <div data-testid={id}>{q.loading ? "loading" : (q.data ?? `error:${q.error}`)}</div>;
}

afterEach(() => {
  clearDashboardQueries();
  setDashboardQueryOwner(null);
});

describe("useDashboardQuery", () => {
  it("shares one request between widgets reading the same key", async () => {
    const fetcher = vi.fn(() => Promise.resolve("facts"));
    render(
      <>
        <Reader id="a" queryKey="overview" fetcher={fetcher} />
        <Reader id="b" queryKey="overview" fetcher={fetcher} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("a").textContent).toBe("facts"));
    expect(screen.getByTestId("b").textContent).toBe("facts");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("lets a failing source break only the widgets that read it", async () => {
    render(
      <>
        <Reader id="ok" queryKey="health" fetcher={() => Promise.resolve("up")} />
        <Reader id="bad" queryKey="traffic" fetcher={() => Promise.reject(new Error("gateway DB down"))} />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("bad").textContent).toBe("error:gateway DB down"));
    expect(screen.getByTestId("ok").textContent).toBe("up");
  });

  it("forgets everything when the signed-in user changes", async () => {
    setDashboardQueryOwner("alice@example.com");
    const alice = vi.fn(() => Promise.resolve("alice's figures"));
    const { unmount } = render(<Reader id="x" queryKey="overview" fetcher={alice} />);
    await waitFor(() => expect(screen.getByTestId("x").textContent).toBe("alice's figures"));
    unmount();

    act(() => setDashboardQueryOwner("bob@example.com"));
    const bob = vi.fn(() => Promise.resolve("bob's figures"));
    render(<Reader id="y" queryKey="overview" fetcher={bob} />);
    expect(screen.getByTestId("y").textContent).toBe("loading"); // nothing of alice's shown
    await waitFor(() => expect(screen.getByTestId("y").textContent).toBe("bob's figures"));
    expect(bob).toHaveBeenCalledTimes(1);
  });
});
