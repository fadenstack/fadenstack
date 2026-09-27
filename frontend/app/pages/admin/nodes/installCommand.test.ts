/**
 * The install line: plain as before, and pinned to the install's own authority
 * when the backend serves a certificate it signed.
 */
import { describe, expect, it } from "vitest";

import { CA_FILE, installCommand } from "./installCommand";

const SHA = "ab".repeat(32);

describe("installCommand", () => {
  it("is the one-paste line it always was without an authority of its own", () => {
    expect(installCommand("http://10.250.10.61")).toBe(
      "curl -fsSLO http://10.250.10.61/api/install/faden-agent.sh && " +
        "sudo sh faden-agent.sh --join http://10.250.10.61",
    );
    // A public certificate needs nothing extra either.
    expect(installCommand("https://console.example.org", { caSha256: null })).not.toContain("--cacert");
  });

  it("checks the authority against the fingerprint before trusting anything from the backend", () => {
    const line = installCommand("https://10.250.10.61", { caSha256: SHA });
    const steps = line.split(" && ");
    expect(steps[0]).toBe(`curl -fsSLk -o ${CA_FILE} https://10.250.10.61/api/install/ca.crt`);
    expect(steps[1]).toBe(`echo "${SHA}  ${CA_FILE}" | sha256sum -c --quiet`);
    // Only after the check: the installer, verified against it; the -k is for the authority alone.
    expect(steps[2]).toBe(`curl -fsSLO --cacert ${CA_FILE} https://10.250.10.61/api/install/faden-agent.sh`);
    expect(steps[3]).toBe(`sudo sh faden-agent.sh --join https://10.250.10.61 --ca-file ${CA_FILE}`);
  });

  it("carries a token the same way", () => {
    expect(installCommand("https://10.250.10.61", { caSha256: SHA, token: "tok" })).toMatch(
      /sudo sh faden-agent\.sh --backend https:\/\/10\.250\.10\.61 --token tok --ca-file server-ca\.crt$/,
    );
    expect(installCommand("http://10.250.10.61", { token: "tok" })).toMatch(/--token tok$/);
  });

  it("never pins over plain HTTP, where the fingerprint could not protect anything", () => {
    expect(installCommand("http://10.250.10.61", { caSha256: SHA })).not.toContain("ca.crt");
  });
});
