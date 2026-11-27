/**
 * The install line: plain as before, and pinned to the install's own authority
 * when the backend serves a certificate it signed.
 */
import { describe, expect, it } from "vitest";

import { CA_FILE, installCommand } from "./installCommand";

const SHA = "ab".repeat(32);

describe("installCommand", () => {
  it("is the one-paste line it always was without an authority of its own", () => {
    expect(installCommand("http://192.0.2.61")).toBe(
      "curl -fsSLO http://192.0.2.61/api/install/faden-agent.sh && " +
        "sudo sh faden-agent.sh --join http://192.0.2.61",
    );
    // A public certificate needs nothing extra either.
    expect(installCommand("https://console.example.org", { caSha256: null })).not.toContain("--cacert");
  });

  it("checks the authority against the fingerprint before trusting anything from the backend", () => {
    const line = installCommand("https://192.0.2.61", { caSha256: SHA });
    const steps = line.split(" && ");
    expect(steps[0]).toBe(`curl -fsSLk -o ${CA_FILE} https://192.0.2.61/api/install/ca.crt`);
    expect(steps[1]).toBe(
      `if [ "$(uname)" = Darwin ]; then echo "${SHA}  ${CA_FILE}" | shasum -a 256 -c --quiet; ` +
        `else echo "${SHA}  ${CA_FILE}" | sha256sum -c --quiet; fi`,
    );
    // Only after the check: the installer, verified against it; the -k is for the authority alone.
    expect(steps[2]).toBe(`curl -fsSLO --cacert ${CA_FILE} https://192.0.2.61/api/install/faden-agent.sh`);
    expect(steps[3]).toBe(`sudo sh faden-agent.sh --join https://192.0.2.61 --ca-file ${CA_FILE}`);
  });

  it("checks with a tool each system has: shasum on a Mac, GNU sha256sum on Linux", () => {
    const check = installCommand("https://192.0.2.61", { caSha256: SHA }).split(" && ")[1];
    // Linux runs exactly the line it always ran.
    expect(check).toContain(`else echo "${SHA}  ${CA_FILE}" | sha256sum -c --quiet; fi`);
    // macOS's BSD sha256sum rejects -c from stdin and --quiet; shasum takes both.
    expect(check).toMatch(/^if \[ "\$\(uname\)" = Darwin \]; then echo "[0-9a-f]{64}  server-ca\.crt" \| shasum -a 256 -c --quiet;/);
  });

  it("carries a token the same way", () => {
    expect(installCommand("https://192.0.2.61", { caSha256: SHA, token: "tok" })).toMatch(
      /sudo sh faden-agent\.sh --backend https:\/\/192.0.2.61 --token tok --ca-file server-ca\.crt$/,
    );
    expect(installCommand("http://192.0.2.61", { token: "tok" })).toMatch(/--token tok$/);
  });

  it("never pins over plain HTTP, where the fingerprint could not protect anything", () => {
    expect(installCommand("http://192.0.2.61", { caSha256: SHA })).not.toContain("ca.crt");
  });
});
