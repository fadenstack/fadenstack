/**
 * The line an operator pastes on a machine to install the agent.
 *
 * One paste, not two. The installer is still downloaded to disk before it
 * runs -- `&&` only chains the download to the run -- so it can be read first.
 *
 * A backend on HTTPS with a certificate from the install's own authority
 * (`faden tls generate`) is one no machine trusts yet. The line then fetches
 * that authority first and checks it against the fingerprint the console shows
 * (`caSha256`, from /api/install/address) before trusting anything from the
 * backend: the installer is fetched verified against it, and handed it with
 * `--ca-file` so the agent keeps it.
 */
export const CA_FILE = "server-ca.crt";

export function installCommand(
  origin: string,
  { caSha256, token }: { caSha256?: string | null; token?: string } = {},
): string {
  const how = token ? `--backend ${origin} --token ${token}` : `--join ${origin}`;
  if (caSha256 && origin.startsWith("https://")) {
    return (
      `curl -fsSLk -o ${CA_FILE} ${origin}/api/install/ca.crt && ` +
      `echo "${caSha256}  ${CA_FILE}" | sha256sum -c --quiet && ` +
      `curl -fsSLO --cacert ${CA_FILE} ${origin}/api/install/faden-agent.sh && ` +
      `sudo sh faden-agent.sh ${how} --ca-file ${CA_FILE}`
    );
  }
  return `curl -fsSLO ${origin}/api/install/faden-agent.sh && sudo sh faden-agent.sh ${how}`;
}
