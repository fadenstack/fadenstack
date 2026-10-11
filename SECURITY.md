# Security policy

## Reporting a vulnerability

Please tell us privately first. Do not report a security problem in a public issue, pull request or discussion.

Report it in one of these ways:

- **On GitHub:** this repository's **Security** tab, **Report a vulnerability**. Only the maintainers see the
  report.
- **Through our contact form:** <https://emagin8.de/contact?subject=Fadenstack%20security>, with the subject
  "Fadenstack security".

Describe what you found and how to reproduce it:

- the release (`faden version` prints it) and the part concerned: the server, the `faden` command, the machine
  agent (`faden-agent`), a container image, an agent or an SDK;
- the steps, the requests or a small proof of concept;
- what an attacker gains, and what they need first (an account, a role, access to the network).

English and German are both fine.

## What happens next

We confirm that we received your report and keep you informed while we fix it. A fix ships in a release; we then
publish a security advisory on GitHub that describes the problem and names you, if you want to be named. Please
keep the details to yourself until that release is out.

## Please don't

- test against installations you do not own or are not allowed to test;
- access, change or delete data that isn't yours;
- disrupt a service (no denial-of-service testing).

## Supported versions

Security fixes go into the latest release. Keep your installation current with `faden upgrade`.

| Version | Security fixes |
| --- | --- |
| 0.4.x (latest) | Yes |
| older | No: upgrade first |

## Scope

This policy covers this repository and what is released from it: the server's services, the `faden` command,
the machine agent, the runtime helper and the container images published under `ghcr.io/fadenstack`. Report a
problem in the Fadenstack agents or SDKs the same way.

Our policy page on the website says the same: <https://fadenstack.com/security/#report>. The machine-readable
contact is <https://fadenstack.com/.well-known/security.txt>.
