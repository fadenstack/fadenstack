# The admin dashboard

**Dashboard**, the first entry of the admin console, shows the whole platform
on one page: how much traffic there was and where it was served, what is
broken, how busy the GPU machines are, and the health of the server
LLM.Port runs on. Only superusers see it; everyone else lands in the chat.

The screenshots were taken on the development install on 2026-09-26.

---

## What it shows

![The admin dashboard](images/dashboard/01-overview.png)

The dashboard is a set of widgets. Each loads and refreshes on its own, so a
slow or failing one does not hold up the rest: it shows "This widget could
not load" with a **Retry**, and the others carry on.

| Widget | What it shows | Links to |
|---|---|---|
| Key metrics | Up to six figures you choose (below), each with its trend | |
| AI traffic | Where requests were served: a ring with the in-house share in its centre, the busiest destinations, request count, P50, P95 and errors. **Today**, **24 h**, **7 d** or **30 d** | Observability traffic |
| Needs attention | Current problems, worst first: machines offline or silent, failed clusters and deployments, runtimes in error, providers not answering, hot GPUs, machines waiting to join, failed model downloads and document ingestion, a spike of gateway errors, dependencies down, low disk, restarting containers, MCP servers with problems. **Show all** when there are more than fit | |
| Local AI capacity | Clusters healthy, machines online, GPUs and free GPUs, and each machine's CPU, GPU and VRAM. Colour by **Health**, **GPU** or **VRAM** | Machines |
| Usage | Requests per day for the last **7** or **30 days**, stacked by where they were served | Observability |
| Integrations | LLM providers by where they run, MCP servers healthy, skills active | Each module's page |
| Platform health | Overall state, dependencies (Postgres, RabbitMQ, Grafana), modules, gateway errors in the last hour, Postgres connections in use | |
| Server resources | CPU, RAM and swap, disk and the NVIDIA GPU of the server itself | |
| Top containers | The containers using the most CPU and memory (hidden at first) | Containers |
| Grafana | The Grafana overview dashboard (hidden at first) | Grafana |

**Where requests were served** has four classes, used in AI traffic, Usage and
Integrations: **Our machines** (your GPU machines and this server),
**Private network**, **External** (a cloud provider) and **Unknown** (where
it cannot be told, such as a provider that has since been removed). "Kept
in-house" is our machines plus the private network. A request refused before
it was routed to a provider is counted apart ("Rejected before routing").

**Machine health** is what a machine reports, never how busy it is: a GPU at
100 % on a healthy machine is healthy. A machine silent for three heartbeats
is "Not reporting". The states differ in shape as well as colour, so they
can be told apart without colour. VRAM reads as "allocated": vLLM reserves
about 90 % of it by design. With more machines the widget draws a small cell
per machine, then a summary per group; select a group to list its machines.

**Time** follows your browser's time zone. "Today" is compared with yesterday
up to the same time, and a month with the previous month up to the same day.

### The header

- **Critical issues: 6** (or **Warnings**, or **System healthy**) counts what
  Needs attention lists.
- The ring beside it shows when the figures were last updated. They refresh
  every 30 seconds while the tab is open, and pause in a hidden tab. Click
  the ring to refresh at once. It turns amber, then red, when a refresh is
  late or failed; the figures shown are then the previous ones.

### Key metrics

The sliders icon on the Key metrics widget picks which figures it shows, up
to six:

| Metric | Figure | Compared with |
|---|---|---|
| Requests today | Requests and tokens | Yesterday at this time |
| Active users today | Users today, and this month | Yesterday at this time |
| Kept in-house | Share of requests served on your machines or private network | Yesterday at this time |
| Spend this month | Estimated from model prices, per currency; requests to external models without a price are counted | Last month up to the same day |
| Needs attention | Open problems, critical and warning | |
| Error rate (1 h) | Gateway errors, and the P95 latency | The hour before |
| GPUs in use | GPUs at 10 % utilization or more, machines online | |

A trend's colour says whether the change is good or bad, not whether it went
up: more errors is red, a larger in-house share is green.

---

## Arranging it

**Customize** puts every widget in a frame with its controls:

![Customize: move, size and hide each widget](images/dashboard/02-customize.png)

- Drag a widget by its handle, or move it with the arrows.
- Pick its size where it has a choice: **S** (small), **M** (medium), **L**
  (large) or **XL** (full width).
- The eye hides or shows it. Rows close up around a hidden widget; nothing is
  left empty.
- **Reset layout** returns to the default. **Done** leaves Customize.

The layout is saved on the server for each user, so it follows you to another
browser or device. If you change it on two devices at once, the second save
says "Your layout was changed on another device": **Keep mine** overwrites
the other, **OK** takes it.

The default layout depends on the setup profile picked on first sign-in:
**Private** hides Integrations, **Enterprise** hides Server resources. Every
widget can still be shown. Integrations leaves out a module that is switched
off.

---

## Light theme

The sun and moon in the top bar switch between the dark and light theme, for
the whole console and the chat:

![The dashboard in the light theme](images/dashboard/03-light-theme.png)

---

## On a phone

Below 600 px the widgets stack in one column, and Customize (the sliders icon
next to the pill) opens a full-screen list of the widgets to reorder, resize
and hide.

<p>
  <img src="images/dashboard/06-phone.png" alt="The dashboard on a phone" width="300">
  <img src="images/dashboard/07-phone-menu-hand.png" alt="The menu, with the side of the menu button" width="300">
</p>

The sidebar gives way to a round **menu button** in a bottom corner, where
your thumb reaches it. It opens the navigation as a panel from the same side;
the logo in the top bar opens it too. **Menu button: Left / Right** at the
bottom of the panel moves the button and the panel to the other hand. The
browser remembers the choice, and the chat uses the same one.

---

## Other pages in the same style

Pages that show figures are built from the dashboard's parts, so they read
the same way.

**Privacy (PII) → Dashboard** shows what the PII filter found in requests, as
counts only: scans, how many found personal data, the entities found and the
detection rate, the daily volume, and the entity types, operations and
sources that account for them.

![The PII dashboard](images/dashboard/04-pii-dashboard.png)

**Containers** opens with four tiles (running, not running, unhealthy,
restarting). Each is a filter for the list below it; see
[Containers](containers.md#the-list).

![The containers page with its state tiles](images/dashboard/05-containers-tiles.png)

The figures on a provider, a deployment, a machine and the chat admin pages,
and the Observability pages, use the same tiles and charts.
