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
 *
 * The check runs `shasum` on a Mac and `sha256sum` elsewhere: macOS ships a BSD
 * `sha256sum` that rejects both `-c` from stdin and `--quiet`, while Linux keeps
 * the GNU line it always had. Written as `if`, not `$TOOL`, because zsh -- the
 * Mac's shell -- does not split an unquoted variable into words.
 */
export const CA_FILE = "server-ca.crt";

function caCheck(caSha256: string): string {
  const line = `echo "${caSha256}  ${CA_FILE}"`;
  return (
    `if [ "$(uname)" = Darwin ]; then ${line} | shasum -a 256 -c --quiet; ` +
    `else ${line} | sha256sum -c --quiet; fi`
  );
}

export function installCommand(
  origin: string,
  { caSha256, token }: { caSha256?: string | null; token?: string } = {},
): string {
  const how = token ? `--backend ${origin} --token ${token}` : `--join ${origin}`;
  if (caSha256 && origin.startsWith("https://")) {
    return (
      `curl -fsSLk -o ${CA_FILE} ${origin}/api/install/ca.crt && ` +
      `${caCheck(caSha256)} && ` +
      `curl -fsSLO --cacert ${CA_FILE} ${origin}/api/install/faden-agent.sh && ` +
      `sudo sh faden-agent.sh ${how} --ca-file ${CA_FILE}`
    );
  }
  return `curl -fsSLO ${origin}/api/install/faden-agent.sh && sudo sh faden-agent.sh ${how}`;
}
