# Usage and costs

Fadenstack counts every request that goes through its gateway: who sent it, which model answered and where it
ran, how many tokens it took, how long it took, and what it would cost at the prices you set. This page shows
where to see those figures, and how to make the cost estimates meaningful.

![Cost Observability on the Overview tab: Estimated Spend, Total Requests, Total Tokens, Avg Latency and Error Rate, with Cost Over Time, Request Throughput, Latency Percentiles and Spend by Model below](../images/administer/observability.png)

## What is counted

For each request, the request log keeps:

- the person, their **Usage group**, and the chat project or the agent it came from;
- the model, and where it ran: on your machines, in your network or at an external provider;
- the tokens: prompt, completion and total;
- the time to the first token and the whole time;
- the outcome: success or the error;
- the estimated cost, when the model has a price.

The text of the request and its answer is not part of it. Everything that goes through the gateway is
counted: the chat, the agents, and applications using API tokens.

## Where to see it

| Where | What it shows |
|---|---|
| [Dashboard](dashboard.md) | Today at a glance: **Requests today**, **Active users today**, **Kept in-house**, **Spend this month**, **Error rate (1 h)**; **AI traffic** (where requests were served) and **Usage** (requests per day, by where they were served). |
| **Observability → Cost Dashboard** | The cost and usage pages (below). |
| **Observability → LLM Calls** | Each request and call: why one failed, what an agent did for one question, where calls went (see [LLM calls](llm-calls.md)). |
| An agent's **Activity** tab | Requests, errors, tokens, users and conversations of that agent, counts only (see [Agents and add-ins](agents.md)). |
| A deployment's page | Requests, speed and failures of that model over the last minutes (see [Deployments](deployments.md)). |
| **Observability → Data Residency** | How much data went to your machines, your network and outside (see [Data residency](providers.md#data-residency)). |

## The cost and usage pages

![Cost Observability, opened from Observability → Cost Dashboard: the tabs Overview, Requests and Pricing, and the time ranges 7d to 90d with refresh and download at the top right](../images/administer/cost-dashboard-top.png)

Open **Observability → Cost Dashboard** (the page is titled **Cost Observability**) and pick a time range.

| Tab | What it shows |
|---|---|
| **Overview** | **Estimated Spend**, **Total Requests**, **Total Tokens**, **Avg Latency** and **Error Rate**, with **Cost Over Time**, **Request Throughput**, **Latency Percentiles**, **Spend by Model** and **Top Users**. |
| **Requests** | Every request, newest first. Filter by **Model** or **User ID**, and open one for its details: tokens, cost, timing, the tools and skills it used, and the steps it went through. |
| **Pricing** | The prices the estimates use (below). |

**Export CSV** downloads the requests of the range (up to 5,000 rows) for a spreadsheet.

## Set prices

Costs are estimates from prices you set per model. They do not change what a provider bills you.

![LLM Providers: a provider's row with its model, Set price in the Price $/1M column, its status and endpoint](../images/administer/providers-list-top.png)

1. Open **LLM → Providers** and select **Set price** in the model's row (in the **Price $/1M** column).

    ![The price dialog for a model: Input price and Output price in USD per million tokens, and Save](../images/administer/provider-set-price.png)

2. Enter what a million input tokens and a million output tokens cost.
3. Save. Requests are priced with it within a minute.

![The Pricing tab of the cost pages: each model's input and output price per million tokens, whether it is a default or set by an administrator, since when it applies, and Add Price](../images/administer/cost-pricing-list.png)

You can also set prices when you connect a provider, or under **Pricing** on the cost pages, where each
model's price history is kept.

??? info "Which price to set"
    Use the provider's list prices for a cloud model. For your own models, a price of your own (for example
    what the hardware costs you per million tokens) lets you compare and charge back internal use; leave it
    empty if you do not need that.

Requests to an external model without a price are still counted. **Spend this month** on the dashboard says
how many there were ("Requests without a price"), so you know the estimate is short.

## Usage per person and per group

![Assign Roles for one person: the roles to tick and the Usage group their usage is attributed to](../images/administer/users-edit-roles.png)

- **Per person**: **Top Users** on the cost pages, and the **User ID** filter under **Requests**.
- **Per group**: each request is recorded with the person's **Usage group**, the group set on their row under
  **Access Control → Users** (see [Users, groups and roles](users.md#groups)). A person with no usage group
  counts towards none. Reports of usage per group in the console are
  <span class="fs-planned">Planned</span>.

## Limits and budgets

Budgets and limits per person, group or agent are <span class="fs-planned">Planned</span>: they will cap how
much a person, a team or an agent may use.

## When it does not work

| You see | What to do |
|---|---|
| The figures are empty | No requests in the range, or the gateway cannot write its log. Send a message in the chat, then refresh. |
| Spend shows "None" or looks too low | The models used have no price. Set them as above. |
| A request is missing from the cost pages | Only requests that reach the gateway are counted: a model someone calls directly at its endpoint, bypassing Fadenstack, is not. |
