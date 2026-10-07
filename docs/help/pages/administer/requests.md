# What happens to a request

Every message someone sends, from the chat, an agent or an application using the API, goes through the same
gateway: the one OpenAI-compatible address of your Fadenstack server. This page explains in plain terms what
Fadenstack does with it, so that you can tell your colleagues, your data protection officer or your works
council what applies.

## Six things happen to every request

### 1. Fadenstack checks who is asking

A request comes with the person's sign-in, an API token they created, or an agent's sign-in on their device.
A request without one, or with one that was revoked or has expired, is refused. A request through an agent is
also refused when the person is not granted that agent, or the agent is retired.

Set up in: [Users, groups and roles](users.md) and [Agents and add-ins](agents.md).

### 2. The request gets what the model needs

Along with the new message go the chat's earlier messages, the instructions of the chat's project, the
[skills](skills.md) that apply (the ones the person picked, and the ones set to come in on their own), the
text of attached files, and the tools the chat may use. An agent's request brings what the application sends:
the page or document the agent reads, and the application's tools. Only skills the person can see reach their
requests.

### 3. Your rules are applied

- **Personal data.** If your [policy](policies.md#personal-data) covers the model that will answer, personal
  data is masked before the request leaves Fadenstack. When the filter cannot run, the policy decides whether
  the request is refused.
- **Tools.** The [tool rules](policies.md#tool-rules), the tools' classes and the chat's tool mode decide
  which tools are offered to the model at all, and which of them may run without asking.

Content policies that refuse a prompt or an answer by what it says are
<span class="fs-planned">Planned</span>.

### 4. The model you chose answers

The request names a model: the name picked in the chat, set in the agent, or given by the application. That
name is either one of your [deployments](deployments.md), running on your own machines, or a
[remote provider](providers.md). If several deployments offer the same name, one of them that is healthy
answers. A model that does not answer gets no requests until it answers again.

Where it runs decides where the request goes: [Data residency](providers.md#data-residency) shows it for every
model.

### 5. Tools run, under your rules

When the model asks for a tool, Fadenstack runs it if the rules allow, asks the person first where they say
so, and gives the result back to the model. Results pass through the same personal data rules. Tools that
belong to an application, such as filling a form in the browser, run in that application on the person's
computer, with the same approvals. The answer then streams back to the person, with masked values put back
where the policy allows it.

### 6. Fadenstack records what happened, not what was said

| Record | What it holds |
|---|---|
| The request log | Who asked, which model and where it ran, how many tokens, how long it took, the estimated cost and the outcome, and which version of which skill was used. Not the text. See [Usage and costs](usage.md). |
| The chat | The conversation itself, kept with the person's chat on the server and encrypted there. Agents keep their conversations on the person's computer. |
| The audit log | Who changed what in the console. |
| Governance decisions | What a rule refused or let through after asking. Never the refused text. |
| The privacy log | How much personal data was found, by kind. Never the text. |

## What people can rely on

- A private skill never reaches anyone else's request.
- Tools run only within the mode the person chose and the rules you set. No mode runs a destructive or
  unclassified tool without asking.
- An agent answers only the people it is granted to, with the model its published version sets. When its
  version lists the application's tools, any other tool is held back.
- The record of what a rule refused never holds the refused text.

For people who use Fadenstack, the same is explained in [Your data and privacy](../use/privacy.md).
