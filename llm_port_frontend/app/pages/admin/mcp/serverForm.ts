/** What the register and edit dialogs send for an MCP server. */
import type {
  MCPServerDetail,
  MCPTransportType,
  PIIMode,
  UpdateMCPServerPayload,
} from "~/api/mcp";

import { toSecretPatch, type SecretEdits } from "./SecretPairsEditor";

/** Reached over HTTP, so it takes a URL and headers; stdio takes a command and env. */
export function isHttp(transport: MCPTransportType): boolean {
  return transport === "sse" || transport === "streamable_http";
}

/** A command line as the service stores it: one list, split on spaces. */
export function splitCommand(line: string): string[] {
  return line.split(/\s+/).filter(Boolean);
}

/** The command line a server runs, arguments included. */
export function commandLine(server: Pick<MCPServerDetail, "command" | "args">): string {
  return [...(server.command ?? []), ...(server.args ?? [])].join(" ");
}

export interface EditForm {
  url: string;
  command: string;
  timeoutSec: number;
  piiMode: PIIMode;
  headers: SecretEdits;
  env: SecretEdits;
}

/**
 * Only what changed: every field sent makes the service reconnect, and a
 * header or variable left out keeps its stored value.
 */
export function serverPatch(server: MCPServerDetail, form: EditForm): UpdateMCPServerPayload {
  const patch: UpdateMCPServerPayload = {};
  if (isHttp(server.transport)) {
    const url = form.url.trim();
    if (url && url !== (server.url ?? "")) patch.url = url;
    const headers = toSecretPatch(form.headers);
    if (Object.keys(headers).length) patch.headers = headers;
  } else {
    const command = splitCommand(form.command);
    if (command.length && command.join(" ") !== commandLine(server)) {
      // The whole line goes in `command`; `args` is emptied so nothing repeats.
      patch.command = command;
      patch.args = [];
    }
    const env = toSecretPatch(form.env);
    if (Object.keys(env).length) patch.env = env;
  }
  if (form.timeoutSec !== server.timeout_sec) patch.timeout_sec = form.timeoutSec;
  if (form.piiMode !== server.pii_mode) patch.pii_mode = form.piiMode;
  return patch;
}
