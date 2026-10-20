# Preview releases

Fadenstack 0.5.0 is a **Preview** release. It has a normal version number, and it is a Preview all the same:
the release notes, the downloads page, `faden deploy`, `faden upgrade` and the console's **About** say so.

## What Preview means

- It is for evaluation and testing. Parts of it may be incomplete or change in the next release.
- Content marking (the provenance module, EU AI Act Art. 50(2)) is part of it and on by default. It supports
  marking AI-generated content; it does not by itself make your use of Fadenstack compliant, and its marks do not
  yet hold on every path (see [Content marking](../administer/content-marking.md)).
- Do not rely on it as the only control for legally significant decisions.
- The provenance module is free proprietary software under its own licence; the rest of Fadenstack is
  Apache-2.0 (see [Third-party components](third-party.md)).

Being a Preview changes none of your own duties under the AI Act or data protection law.

## When you deploy or upgrade

`faden deploy` and `faden upgrade` show this notice before they change anything and ask whether to go on. With
`-y` they go on without asking and say so. Who accepted which release, and when, is kept in `faden.yaml` under
`preview_accepted`:

```yaml
preview_accepted:
  0.5.0: {by: alex, at: "2026-10-21T09:12:00+00:00", how: prompt}
```

Answer no, and nothing is changed.
