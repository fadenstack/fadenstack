/**
 * Platform health on an install with nothing wrong (a test server, 0.6.0 and 0.7.0rc1, 9-10 November 2026):
 * "Modules 2 / 3 healthy" with PII on (Chat & Sessions, switched on but without a probe of its own, never
 * counted as healthy), and "61of 250 connections" (the number and the text were apart by a margin only).
 */
import { render } from "@testing-library/react";
import { ThemeProvider } from "@mui/material/styles";
import i18n from "i18next";
import { afterEach, describe, expect, it, vi } from "vitest";

import de from "../../../../../backend/i18n/de/common.json";
import es from "../../../../../backend/i18n/es/common.json";
import fr from "../../../../../backend/i18n/fr/common.json";
import zh from "../../../../../backend/i18n/zh/common.json";
import { getAppTheme } from "~/theme";

const state = vi.hoisted(() => ({
  services: [] as Array<Record<string, unknown>>,
}));

vi.mock("../queries", () => ({
  useOverview: () => ({
    data: { system_status: "ok", system_badge: "green", postgres_connections: 61, postgres_max_connections: 250 },
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  useDependencyHealth: () => ({
    data: { items: [{ name: "postgres", status: "up" }, { name: "rabbitmq", status: "up" }, { name: "grafana", status: "up" }] },
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
  useKpis: () => ({ data: { items: [] }, loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock("~/lib/ServicesContext", () => ({
  useServices: () => ({ services: state.services, loading: false }),
}));

import PlatformHealthWidget from "./PlatformHealthWidget";

const chat = { name: "chat", enabled: true, status: "configured", module_type: "plugin" };
const pii = { name: "pii", enabled: true, status: "healthy", module_type: "container" };
const provenance = { name: "provenance", enabled: true, status: "healthy", module_type: "container" };
const mcp = { name: "mcp", enabled: false, status: "disabled", module_type: "container" };

function view(): string {
  const { container } = render(
    <ThemeProvider theme={getAppTheme("light")}>
      <PlatformHealthWidget size="M" layout={{} as never} onLayoutChange={() => {}} refreshToken={0} />
    </ThemeProvider>,
  );
  return container.textContent ?? "";
}

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("PlatformHealthWidget", () => {
  it("counts a module switched on without a probe of its own as healthy", () => {
    state.services = [chat, pii, provenance, mcp];
    expect(view()).toContain("3 / 3 healthy");
  });

  it("still counts a module whose probe failed as not healthy", () => {
    state.services = [chat, { ...pii, status: "unhealthy" }, provenance, mcp];
    expect(view()).toContain("2 / 3 healthy");
  });

  it("writes a space between the connections and their limit, in every language", async () => {
    state.services = [chat];
    expect(view()).toContain("61 of 250 connections");
    const expected: Array<[string, object, string]> = [
      ["de", de, "61 von 250 Verbindungen"],
      ["es", es, "61 de 250 conexiones"],
      ["fr", fr, "61 sur 250 connexions"],
      ["zh", zh, "61 / 250 个连接"],
    ];
    for (const [lang, bundle, text] of expected) {
      i18n.addResourceBundle(lang, "common", bundle, true, true);
      await i18n.changeLanguage(lang);
      expect(view()).toContain(text);
    }
  });
});
