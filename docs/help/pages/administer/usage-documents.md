# Documents for your DPO and works council

Before your organisation uses Fadenstack's usage figures, its data protection officer (DPO) and, where there is
one, its works council need documents: a notice for employees, an entry in the records of processing, a
balancing of interests, input for the data protection impact assessment, an annex to the works agreement, an
information sheet, a procedure for requests, and the intended purpose. This page has a template for each.

!!! note "Templates, not legal advice"
    Adapt each template with your DPO and legal counsel: fill in the [placeholders], delete what does not apply,
    add what your organisation has agreed. Your organisation is the controller and the employer, and decides.
    The templates describe Fadenstack as of the release whose Help you read; when you upgrade, check the
    [data fields](usage-privacy.md#the-data-fields) and the release notes for changes.

## Before you start

1. Decide the configuration with your DPO and works council: person-level reporting on or off, the minimum
   group size, the granularity, the retention periods and the limits (see
   [Usage and privacy](usage-privacy.md#the-settings)).
2. Set it under **Settings → Usage and privacy**.
3. Download the **configuration statement** from the same tab. It records the settings as they are; attach it to
   the annex and to the DPIA.

The list of every stored field, and what each setting means, is on [Usage and privacy](usage-privacy.md). It is
the data-field inventory the documents below refer to.

| Document | For | When |
|---|---|---|
| [Employee notice](#employee-notice) | Everyone who uses Fadenstack | Before usage is counted, and again before person-level reporting is switched on |
| [Records-of-processing entry](#records-of-processing-entry) | Your records of processing (Art. 30 GDPR) | Once; update it on every change |
| [Legitimate-interest assessment](#legitimate-interest-assessment) | Your DPO | Before use, and when purposes change |
| [DPIA input](#dpia-input) | Your DPO | When the AI platform gets a DPIA; always once person-level reporting is on |
| [Works-agreement annex](#works-agreement-annex) | Employer and works council | Before use, and before each change |
| [§ 90 information sheet](#information-sheet-for-the-works-council) | The works council's first meeting on it | In good time, while the plans can still change |
| [Access-request and erasure procedure](#access-request-and-erasure-procedure) | Whoever answers data-subject requests | Keep it ready |
| [Intended purpose](#intended-purpose) | Administrators, the works council, your rules for using AI | With the instructions for use |

## Employee notice

A module for your existing privacy notice for employees (information under Art. 13 GDPR). Use the first version
while person-level reporting is off; add the second before you switch it on.

### Aggregate only

> **Usage measurement on the AI platform**
>
> [Company] runs the AI platform Fadenstack on its own servers [operated by [IT service provider]]. When you use
> it, in the chat, through an agent (in the browser or in Microsoft Office) or through an application, it records
> each request: your user account, your team, the chat project or the agent, the model, the time, how many tokens
> (units of text) went in and out, the estimated cost, and whether the request succeeded. For agents it also
> records the device the agent is signed in on. It does not record what you wrote or what the model answered.
>
> **Purposes.** Running the platform and planning its capacity; knowing its costs and allocating them to teams,
> projects and agents; protecting it against runaway use with limits for the whole platform. We do not use these
> data to monitor or evaluate your performance or behaviour, nor for appraisals, pay, promotion, selection,
> warnings or dismissals.
>
> **Legal basis.** Art. 6(1)(f) GDPR: our legitimate interest in controlling costs, planning capacity and sharing
> the platform fairly [, and the works agreement [title] of [date] (Art. 88 GDPR, § 26(4) BDSG)].
>
> **Who sees what.** You see your own usage under **My usage** on the platform, and can download it. The
> administrators [positions] see totals for teams, agents and models only where at least [5] people are behind a
> figure. The request records they use to find the cause of a failure do not show who sent a request. Nobody
> evaluates your individual usage.
>
> **How long.** Request records [90] days; your daily figures [90] days; figures without persons [90] days by the
> hour and [25] months by the day. When your account is deleted, your records and daily figures are erased with
> it; totals keep your share without your name.
>
> **Recipients.** Nobody outside [Company] [except [IT service provider], bound by a contract under Art. 28
> GDPR]. The maker of the software receives no usage data.
>
> **Your rights.** You may ask for access (Art. 15 GDPR; the download under **My usage** shows your data),
> rectification, erasure and restriction (Art. 16 to 18), and object to the processing on grounds relating to
> your particular situation (Art. 21). You may lodge a complaint with a supervisory authority [authority]. Contact:
> [controller's contact]; our data protection officer: [DPO's contact].

### Person-level reporting on

Add to **Purposes** and replace **Who sees what** with:

> **Looking at individual figures.** In addition, [number] persons named in [the works agreement] [positions]
> may look at the daily [monthly] figures of an individual, or of a team per person, but only for these reasons:
> [settling a dispute about costs; answering your own request; investigating a security incident]. Each look needs
> a stated reason [and the prior approval of [works council member, DPO]], and is recorded with the date, whose
> figures, the period and the reason. **My usage** shows you how often your figures were looked at; your download
> shows the date and reason of each look. The record of these looks is kept for [period].

## Records-of-processing entry

An entry for your records of processing activities (Art. 30(1) GDPR).

| Item | Entry |
|---|---|
| Name | Usage measurement and limits of the AI platform |
| Controller, representative, DPO | [Company, address]; [representative]; [DPO's contact] |
| Purposes | Running the AI platform and planning its capacity; knowing costs and allocating them to teams, projects and agents; protection against runaway use (limits for the whole platform); showing each user their own use [; settling disputes about costs by looking at individual figures, see the works agreement] |
| Legal basis | Art. 6(1)(f) GDPR [; works agreement [title] of [date], Art. 88 GDPR, § 26(4) BDSG] |
| Data subjects | Employees and other users of the AI platform [trainees, contractors] |
| Personal data | Per request: account id, team, chat project, agent, model, time, tokens, estimated cost, outcome; for agents the device and conversation ids. Per person and day: the same figures added up. Records of looks at individual figures: who looked, whose figures, period, reason. No content of requests or answers; no special categories (Art. 9 GDPR) |
| Recipients | Administrators [positions]; [usage auditors [positions], if person-level reporting is on]; [IT service provider under Art. 28 GDPR] |
| Transfers to third countries | None for the usage data |
| Erasure periods | Request records [90] days; daily figures per person [90] days; figures without persons [90] days hourly, [25] months daily; erased with the account; audit log [period] |
| Technical and organisational measures (Art. 32 GDPR) | Role-based access; a separate role for individual figures, held by nobody unless assigned, not given by administrator rights; minimum group size [5] for every figure shown, with suppression of the remainder; individual figures by day at most, no device and no time of day in figures; a stated reason and a record for every look; automatic deletion; tamper-evident audit log; own view and download for every user; [encryption of the server's storage, backups, access to the server: per your operation]. Configuration statement of [date] attached |

## Legitimate-interest assessment

A balancing of interests under Art. 6(1)(f) GDPR, in its three steps.

**1. Legitimate interest.** [Company] has to know and control what the AI platform costs ([external model
providers, hardware, energy]), plan its capacity, share it fairly among teams and protect it against runaway use.
[Add your own, for example charging costs to cost centres.]

**2. Necessity.** Totals per team, agent and model can only be formed from the record of each request. Milder means
considered and used:

- figures for others only in aggregate, for groups of at least [5] people (person-level reporting off);
- limits for the whole platform, which need no personal data;
- figures per person by day at most [by month], with no time of day and no device;
- short retention ([90] days for request records and figures per person).

[If person-level reporting is on: why aggregates do not suffice for the reasons named, for example a dispute
about costs that can only be settled with a person's own figures.]

**3. Balancing.** The interests of employees: not to be monitored or compared; no conclusions about working time;
no pressure on members of small teams. Safeguards that weigh in favour of the processing:

- nobody sees another person's figures unless person-level reporting is on, and then only named usage auditors,
  for agreed reasons, each look with a stated reason and recorded;
- every figure shown covers at least [5] people, and suppression prevents working one out from the others;
- no rankings, comparisons or scores of people;
- every person sees their own figures and can download them;
- employees are informed (employee notice), and the works council has agreed [works agreement];
- usage data is not used to evaluate employees (intended purpose, works agreement).

Employees can reasonably expect a work tool's costs to be measured; they need not expect their individual use to
be evaluated, and it is not.

**Result.** [The interests of [Company] prevail / do not prevail, given these safeguards.] Assessed by [name] on
[date], DPO consulted on [date]. Review on [date] or when purposes or configuration change.

## DPIA input

A module for the data protection impact assessment (DPIA, Art. 35 GDPR) of the AI platform.

**Is one needed?** The German list of processing operations that always need a DPIA (DSK, no. 8) names extensive
processing of employees' behaviour data that can be used to evaluate their work with legal or similarly
significant effects. Aggregate figures for groups of at least five people arguably do not meet it; with
person-level reporting on, assume they do. Most organisations assess the AI platform as a whole; add this as a
module.

**Description.** The gateway of the AI platform records each request (see the data fields). Every few minutes it
adds the records up:

```text
request (chat, agent, application)
  -> gateway -> request log: one record per request, with the person          [90] days
       -> hourly figures: team, agent, model, provider; no person              [90] days
       -> daily figures: the same keys; no person                              [25] months
       -> daily figures per person: person, team, project, agent, model        [90] days
            -> Usage page: totals of at least [5] people (administrators, auditors)
            -> My usage: each person's own figures and download
            -> person-level view: usage auditors, if switched on, reason, recorded
limits for the whole platform: one count per minute and per day, no person, not kept
```

**Necessity and proportionality.** See the [legitimate-interest assessment](#legitimate-interest-assessment) and
the configuration statement of [date].

**Risks and measures.**

| Risk | What could happen | Measures |
|---|---|---|
| Pressure from monitoring | Employees feel watched and change how they work | Person-level reporting off by default; no rankings; own view; excluded uses in the works agreement |
| Re-identification | A small team's or an agent's total is one person's | Minimum group size [5] on every figure; small groups merged, with suppression so none can be worked out by subtraction; small-instance warning |
| Function creep | Figures end up in appraisals or selection | Intended purpose and excluded uses; works agreement; reason and record for every look |
| Working-time inference | Hourly series show when someone works | No figures per person by the hour; [month granularity] |
| Unauthorised access or copies | Individual figures leave the system | Separate role assigned to nobody by default; record of every look; rule against copies (admin note) |
| Keeping too long | Old data serves new purposes | Automatic deletion after [90] days / [25] months; erasure with the account |
| Lack of transparency | Employees do not know what is counted | Employee notice; **My usage** shows the setting and the number of looks; download |

**Measures by protection goal** (Standard Data Protection Model):

| Goal | Measure |
|---|---|
| Data minimisation | No content; figures per person by day at most; no device or time of day in figures; aggregate-only by default |
| Availability | Backups of the server [per your operation] |
| Integrity | Tamper-evident audit log; figures recomputed from the request log |
| Confidentiality | Role-based access; separate usage auditor role; minimum group size |
| Unlinkability | Usage figures kept apart from chat content; account ids instead of names in the records |
| Transparency | Employee notice; own view; record of looks shown to the person; data-field list; configuration statement |
| Intervenability | Download for access requests; erasure with the account; works council reads the audit log [with the `auditor` role] |

**Views of those affected** (Art. 35(9) GDPR): [the works council was consulted on [date]; its comments].

## Works-agreement annex

An annex to your works agreement on the AI platform: **Anlage „Nutzungsmessung und Budgets“** (usage
measurement and budgets). It fits a union-side draft as well as an employer-side one: they differ in the
configuration they choose, not in what the annex must settle.

> **Annex [number] to the works agreement [title] of [date]: usage measurement and budgets**
>
> **1. Subject.** This annex governs the usage measurement and limits of the AI platform Fadenstack, release
> [version], as described in its Help (Usage and limits; Usage and privacy). The data-field list of that release
> and the configuration statement of [date] are attached.
>
> **2. Purposes.** Usage data may be processed only to run the platform and plan its capacity, to know its costs
> and allocate them to teams, projects and agents, to protect it against runaway use, and to show each user their
> own use [; and to settle disputes about costs under section 6].
>
> **3. Excluded uses.** Usage data must not be used to monitor or evaluate the performance or behaviour of
> employees, to compare employees, or as a basis for appraisals, pay, promotion, selection, warnings, dismissals or
> other personnel measures. Personnel measures based on data processed in breach of this annex [are void / will not
> be taken]. Usage data must not be analysed by an AI model with regard to individuals.
>
> **4. Data.** Only the fields in the attached list are processed. A release that adds fields or reports, or
> changes a default, is introduced only after the works council has been informed (§ 90 BetrVG) and this annex has
> been [amended / confirmed].
>
> **5. Configuration.**
>
> | Setting | Agreed |
> |---|---|
> | Person-level reporting | [Off / On, under section 6] |
> | Minimum group size | [5, or higher] |
> | Granularity of individual figures | [Day / Month] |
> | Request records | [90] days |
> | Daily figures per person | [90] days |
> | Hourly figures, daily figures | [90] days, [25] months |
> | Limits for the whole platform | Requests per minute [ ], tokens per minute [ ], tokens per day [ ] |
> | Budgets | [None / as set out in [annex]] |
>
> Changes to the configuration need the works council's [consent].
>
> **6. Individual figures** (only if person-level reporting is on). The role `usage_auditor` is held only by
> [names or positions]. They may look at individual figures only for [reasons], each time with a stated reason
> [and after approval by [works council member / DPO]]. The person concerned [is informed within [period] / can see
> the date and reason in their download].
>
> **7. Roles.** Administrators: [positions]. Reading the audit log and aggregate figures (role `auditor`):
> [DPO, member of the works council]. Nobody else holds the role `usage_auditor`.
>
> **8. Limits and alerts.** When a limit refuses a request, only the person who made it is told, with the kind of
> limit and whom to ask. [Budgets and their alerts: [none / as set out in [annex]].] Anyone who needs more turns to
> [contact].
>
> **9. Checks.** The works council may read the records of looks at individual figures and of configuration
> changes [at any time / quarterly] through [member] with the role `auditor`. The audit log is kept for [period].
>
> **10. Releases.** The employer informs the works council before each upgrade that the release notes mark as
> relevant to data protection, with the new data-field list and configuration statement.

## Information sheet for the works council

Information under § 90(1) no. 3 BetrVG, about the planned use of AI in work procedures, for the works council's
first meeting on usage measurement. Hand it over in good time, so that the works council's suggestions and concerns
can still be taken into account (§ 90(2) BetrVG).

> **Usage measurement on the AI platform: information for the works council**
>
> **What is planned.** [Company] [runs / plans to run] the AI platform Fadenstack [release] on its own servers.
> [Planned change: introduction of the usage measurement / switching on person-level reporting / new limits.]
> [Date planned.]
>
> **What is counted.** Each request to an AI model: who sent it (user account), their team, the chat project or
> agent, the model, the time, the amount of text in and out (tokens), the estimated cost, success or failure. For
> agents also the device they are signed in on. Not counted: what was written or answered.
>
> **What becomes of it.** Every few minutes the records are added up: per hour and per day for teams, agents and
> models, without persons; per day for each person.
>
> **Who sees what.** Every employee sees their own figures. Administrators see totals only where at least [5]
> people are behind a figure; the request records they use to trace failures do not show who sent a request.
> Nobody sees rankings or comparisons of people. [Planned: [number] named persons may look at individual figures for [reasons], each time
> with a reason, recorded.]
>
> **Limits.** [None / requests per minute [ ], tokens per minute [ ], tokens per day [ ]] for the whole platform.
> They count everyone together and store nothing about individuals. When one is reached, only the person whose
> request is refused is told.
>
> **How long.** Request records [90] days; individual daily figures [90] days; totals [25] months.
>
> **What is excluded.** Monitoring or evaluating employees; appraisals, pay, selection, warnings or dismissals based
> on usage data.
>
> **Co-determination.** The platform records data about individual use, so it is subject to § 87(1) no. 6
> BetrVG. [Company] proposes the attached annex. [Expert under § 80(3) BetrVG: [arrangement].]
>
> **Enclosed.** Data-field list; configuration statement of [date]; draft annex; DPIA module.
>
> **Contact.** [Name, position, contact.]

## Access-request and erasure procedure

How to answer requests about usage data. The legal deadline is one month (Art. 12(3) GDPR).

**Access (Art. 15 GDPR)**

1. Record the request and check who is asking, as for any data-subject request.
2. Ask the person to open **My usage** and download their data (JSON or CSV), or guide them through it. It holds
   their daily figures, their request records still kept (time, model, agent, tokens, cost, outcome), and the date
   and reason of every look at their figures.
3. Add the information of Art. 15(1)(a) to (h) GDPR: purposes, categories, recipients, periods, rights, source.
   The employee notice has it.
4. Fadenstack has no download of another person's usage. For someone who cannot sign in: [your procedure].

**Erasure (Art. 17 GDPR)**

- Usage data is deleted by itself at the end of its period, and erased with the account when the account is deleted.
- Fadenstack has no way to erase one person's usage data while their account stays. [Your procedure, for example:
  shorten the periods for everyone, or delete the account.]
- What remains: totals by team, agent and model, without the person; backups until they are replaced; the audit
  log's records of looks at the person's figures, kept for [period].

**Objection (Art. 21 GDPR)**

- With person-level reporting off, nobody evaluates individual figures; check whether the remaining processing
  (totals, request records for troubleshooting) is justified for the person's situation.
- With it on: Fadenstack has no switch that leaves one person out. [Your measure, for example: the usage auditors
  do not look at this person's figures; record that in [register].]

**Record** each request, the answer and the date in [your register of data-subject requests].

## Intended purpose

The intended purpose of the usage measurement and limits, as Fadenstack states it in its Help (see
[Usage and privacy](usage-privacy.md#what-usage-data-is-for)). Put it into your rules for using AI and your
instructions for administrators.

> **Intended purpose.** Fadenstack's usage figures and limits are meant for running the AI platform and planning
> its capacity, knowing its costs and allocating them to teams, projects and agents, protecting it against runaway
> use, and showing each user their own use.
>
> **Excluded uses.** They are not meant for, and must not be used for, monitoring or evaluating the performance or
> behaviour of individual workers or comparing them; appraisals, pay, promotion, selection, warnings or dismissals;
> finding "heavy users" or people who use AI little; or having an AI model analyse a person's usage.
>
> **Why it matters.** An AI system used to monitor or evaluate workers is high-risk under the EU AI Act (Annex III
> point 4(b)). An organisation that uses Fadenstack, or an agent built on it, that way may become the provider of
> a high-risk AI system (Art. 25(1)(c) AI Act), with a provider's duties.
>
> [Our organisation adopts this purpose. Questions to [contact].]
