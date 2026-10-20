# Preview-Releases

Fadenstack 0.5.0 ist ein **Preview**-Release. Es hat eine normale Versionsnummer und ist trotzdem eine Preview:
Das sagen die Versionshinweise, die Download-Seite, `faden deploy`, `faden upgrade` und **Über** in der Konsole.

## Was Preview bedeutet

- Es ist für Evaluierung und Tests gedacht. Teile davon können unvollständig sein oder sich im nächsten Release
  ändern.
- Die Kennzeichnung von Inhalten (das Modul Herkunftsnachweis, EU AI Act Art. 50(2)) gehört dazu und ist von
  Anfang an eingeschaltet. Sie unterstützt die Kennzeichnung KI-erzeugter Inhalte; sie macht Ihre Nutzung von
  Fadenstack nicht von selbst konform, und ihre Kennzeichen halten noch nicht auf jedem Weg (siehe
  [Kennzeichnung von Inhalten](../administer/content-marking.md)).
- Verlassen Sie sich bei rechtlich bedeutsamen Entscheidungen nicht allein darauf.
- Das Modul Herkunftsnachweis ist kostenlose proprietäre Software unter einer eigenen Lizenz; der Rest von
  Fadenstack steht unter Apache-2.0 (siehe [Komponenten Dritter](third-party.md)).

Dass es eine Preview ist, ändert nichts an Ihren eigenen Pflichten nach dem AI Act oder dem Datenschutzrecht.

## Beim Installieren oder Aktualisieren

`faden deploy` und `faden upgrade` zeigen diesen Hinweis, bevor sie etwas ändern, und fragen, ob es weitergehen
soll. Mit `-y` geht es ohne Frage weiter, und das wird so gesagt. Wer welches Release wann angenommen hat, steht in
`faden.yaml` unter `preview_accepted`:

```yaml
preview_accepted:
  0.5.0: {by: alex, at: "2026-10-21T09:12:00+00:00", how: prompt}
```

Lehnen Sie ab, wird nichts geändert.
