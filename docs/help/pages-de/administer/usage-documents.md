# Unterlagen für Datenschutz und Betriebsrat {#documents-for-your-dpo-and-works-council}

Bevor Ihre Organisation die Nutzungszahlen von Fadenstack verwendet, brauchen ihr Datenschutzbeauftragter und,
wo es einen gibt, ihr Betriebsrat Unterlagen: Datenschutzhinweise für die Beschäftigten, einen Eintrag im
Verzeichnis von Verarbeitungstätigkeiten, eine Interessenabwägung, einen Baustein für die
Datenschutz-Folgenabschätzung, eine Anlage zur Betriebsvereinbarung, ein Informationsblatt, ein Verfahren für
Anfragen von Betroffenen und die Zweckbestimmung. Diese Seite enthält für jede eine Vorlage.

!!! note "Vorlagen, keine Rechtsberatung"
    Passen Sie jede Vorlage mit Ihrem Datenschutzbeauftragten und Ihrer Rechtsberatung an: Füllen Sie die
    [Platzhalter] aus, streichen Sie, was nicht zutrifft, und ergänzen Sie, was Ihre Organisation vereinbart hat.
    Ihre Organisation ist Verantwortlicher und Arbeitgeber und entscheidet. Die Vorlagen beschreiben Fadenstack in
    der Version, deren Hilfe Sie lesen; prüfen Sie bei jedem Upgrade die
    [Datenfelder](usage-privacy.md#the-data-fields) und die Versionshinweise auf Änderungen.

## Bevor Sie beginnen {#before-you-start}

1. Legen Sie die Konfiguration mit Ihrem Datenschutzbeauftragten und Ihrem Betriebsrat fest: personenbezogene
   Auswertung ein oder aus, Mindestgruppengröße, Granularität, Aufbewahrungsdauern und Limits (siehe
   [Nutzung und Datenschutz](usage-privacy.md#the-settings)).
2. Stellen Sie sie unter **Einstellungen → Nutzung und Datenschutz** ein.
3. Wählen Sie auf demselben Reiter unter **Konfigurationsbeschreibung** **Download**. Sie hält die Einstellungen fest,
   wie sie sind; fügen Sie sie der Anlage und der Datenschutz-Folgenabschätzung bei.

Die Liste aller gespeicherten Felder und was jede Einstellung bedeutet, steht unter
[Nutzung und Datenschutz](usage-privacy.md). Sie ist das Verzeichnis der Datenfelder, auf das sich die Unterlagen
unten beziehen.

| Unterlage | Für | Wann |
|---|---|---|
| [Datenschutzhinweise für Beschäftigte](#employee-notice) | Alle, die Fadenstack nutzen | Bevor Nutzung gezählt wird, und erneut, bevor die personenbezogene Auswertung eingeschaltet wird |
| [Eintrag im Verzeichnis von Verarbeitungstätigkeiten](#records-of-processing-entry) | Ihr Verzeichnis (Art. 30 DSGVO) | Einmal; bei jeder Änderung anpassen |
| [Interessenabwägung](#legitimate-interest-assessment) | Ihren Datenschutzbeauftragten | Vor der Nutzung und wenn sich Zwecke ändern |
| [Baustein für die Datenschutz-Folgenabschätzung](#dpia-input) | Ihren Datenschutzbeauftragten | Wenn die KI-Plattform eine DSFA erhält; immer, sobald die personenbezogene Auswertung eingeschaltet ist |
| [Anlage zur Betriebsvereinbarung](#works-agreement-annex) | Arbeitgeber und Betriebsrat | Vor der Nutzung und vor jeder Änderung |
| [Informationsblatt nach § 90 BetrVG](#information-sheet-for-the-works-council) | Die erste Sitzung des Betriebsrats dazu | Rechtzeitig, solange sich die Planung noch ändern lässt |
| [Verfahren für Auskunfts- und Löschersuchen](#access-request-and-erasure-procedure) | Wer Anfragen von Betroffenen beantwortet | Bereithalten |
| [Zweckbestimmung](#intended-purpose) | Administratoren, den Betriebsrat, Ihre Regeln zur KI-Nutzung | Mit der Betriebsanleitung |

## Datenschutzhinweise für Beschäftigte {#employee-notice}

Ein Baustein für Ihre bestehenden Datenschutzhinweise für Beschäftigte (Informationen nach Art. 13 DSGVO).
Verwenden Sie die erste Fassung, solange die personenbezogene Auswertung aus ist; ergänzen Sie die zweite, bevor
Sie sie einschalten.

### Nur zusammengefasste Zahlen {#aggregate-only}

> **Nutzungsmessung auf der KI-Plattform**
>
> [Unternehmen] betreibt die KI-Plattform Fadenstack auf eigenen Servern [, betreut durch [IT-Dienstleister]].
> Wenn Sie sie nutzen – im Chat, über einen Agenten (im Browser oder in Microsoft Office) oder über eine
> Anwendung –, wird jede Anfrage erfasst: Ihr Benutzerkonto, Ihr Team, das Chat-Projekt oder der Agent, das
> Modell, der Zeitpunkt, wie viele Tokens (Texteinheiten) hinein- und herausgingen, die geschätzten Kosten und ob
> die Anfrage erfolgreich war. Bei Agenten wird außerdem das Gerät erfasst, auf dem der Agent angemeldet ist.
> Nicht erfasst wird, was Sie geschrieben oder was das Modell geantwortet hat.
>
> **Zwecke.** Betrieb der Plattform und Planung ihrer Kapazität; Kenntnis ihrer Kosten und deren Zuordnung zu
> Teams, Projekten und Agenten; Schutz der Plattform vor ausuferndem Verbrauch durch Limits für die gesamte
> Plattform. Wir verwenden diese Daten nicht, um Ihre Leistung oder Ihr Verhalten zu überwachen oder zu bewerten,
> und nicht für Beurteilungen, Vergütung, Beförderungen, Auswahlentscheidungen, Abmahnungen oder Kündigungen.
>
> **Rechtsgrundlage.** Art. 6 Abs. 1 lit. f DSGVO. Unser berechtigtes Interesse liegt in der Kontrolle der
> Kosten, der Planung der Kapazität und der fairen Verteilung der Plattform [; außerdem die Betriebsvereinbarung
> [Titel] vom [Datum] (Art. 88 DSGVO, § 26 Abs. 4 BDSG)].
>
> **Wer was sieht.** Sie sehen Ihre eigene Nutzung auf der Plattform unter **Meine Nutzung** und können sie
> herunterladen. Die Administratoren [Funktionen] sehen Summen für Teams, Agenten und Modelle nur dort, wo
> mindestens [5] Personen hinter einer Zahl stehen. Die Einträge zu Anfragen, mit denen sie die Ursache eines
> Fehlers suchen, zeigen nicht, wer eine Anfrage gesendet hat. Niemand wertet Ihre persönliche Nutzung aus.
>
> **Speicherdauer.** Einträge zu Anfragen [90] Tage; Ihre täglichen Zahlen [90] Tage; Zahlen ohne Personenbezug
> [90] Tage pro Stunde und [25] Monate pro Tag. Wird Ihr Konto gelöscht, werden Ihre Einträge und täglichen Zahlen
> mit ihm gelöscht; Summen behalten Ihren Anteil ohne Ihren Namen.
>
> **Empfänger.** Niemand außerhalb von [Unternehmen] [außer [IT-Dienstleister] als Auftragsverarbeiter nach
> Art. 28 DSGVO]. Der Hersteller der Software erhält keine Nutzungsdaten.
>
> **Ihre Rechte.** Sie haben das Recht auf Auskunft (Art. 15 DSGVO; der Download unter **Meine Nutzung** zeigt
> Ihre Daten), auf Berichtigung, Löschung und Einschränkung der Verarbeitung (Art. 16 bis 18 DSGVO) sowie das
> Recht, aus Gründen, die sich aus Ihrer besonderen Situation ergeben, der Verarbeitung zu widersprechen
> (Art. 21 DSGVO). Sie können sich bei einer Aufsichtsbehörde beschweren (Art. 77 DSGVO) [zuständige Behörde].
> Verantwortlicher: [Kontakt]; unser Datenschutzbeauftragter: [Kontakt].

### Mit personenbezogener Auswertung {#person-level-reporting-on}

Ergänzen Sie die **Zwecke** und ersetzen Sie **Wer was sieht** durch:

> **Einsicht in persönliche Zahlen.** Zusätzlich dürfen [Anzahl] in [der Betriebsvereinbarung] benannte Personen
> [Funktionen] die täglichen [monatlichen] Zahlen einer einzelnen Person oder eines Teams pro Person ansehen, aber
> nur aus diesen Gründen: [Klärung eines Streits über Kosten; Beantwortung Ihrer eigenen Anfrage; Aufklärung eines
> Sicherheitsvorfalls]. Jede Einsicht braucht eine Begründung [und die vorherige Zustimmung von [Mitglied des
> Betriebsrats, Datenschutzbeauftragter]] und wird mit Datum, betroffener Person, Zeitraum und Grund festgehalten.
> **Meine Nutzung** zeigt Ihnen, wie oft Ihre Zahlen angesehen wurden; Ihr Download zeigt Datum und Grund jeder
> Einsicht. Die Aufzeichnung dieser Einsichten wird [Dauer] aufbewahrt.

## Eintrag im Verzeichnis von Verarbeitungstätigkeiten {#records-of-processing-entry}

Ein Eintrag für Ihr Verzeichnis von Verarbeitungstätigkeiten (Art. 30 Abs. 1 DSGVO).

| Angabe | Eintrag |
|---|---|
| Bezeichnung | Nutzungsmessung und Limits der KI-Plattform |
| Verantwortlicher, Vertreter, Datenschutzbeauftragter | [Unternehmen, Anschrift]; [Vertreter]; [Kontakt des Datenschutzbeauftragten] |
| Zwecke | Betrieb der KI-Plattform und Planung ihrer Kapazität; Kenntnis der Kosten und deren Zuordnung zu Teams, Projekten und Agenten; Schutz vor ausuferndem Verbrauch (Limits für die gesamte Plattform); Anzeige der eigenen Nutzung für jede Person [; Klärung von Streitfragen über Kosten durch Einsicht in persönliche Zahlen, siehe Betriebsvereinbarung] |
| Rechtsgrundlage | Art. 6 Abs. 1 lit. f DSGVO [; Betriebsvereinbarung [Titel] vom [Datum], Art. 88 DSGVO, § 26 Abs. 4 BDSG] |
| Kategorien betroffener Personen | Beschäftigte und andere Nutzer der KI-Plattform [Auszubildende, Fremdpersonal] |
| Kategorien personenbezogener Daten | Pro Anfrage: Konto-ID, Team, Chat-Projekt, Agent, Modell, Zeitpunkt, Tokens, geschätzte Kosten, Ergebnis; bei Agenten die Kennungen von Gerät und Unterhaltung. Pro Person und Tag: dieselben Zahlen zusammengefasst. Aufzeichnung der Einsichten in persönliche Zahlen und der Downloads der Daten einer Person: wer, in wessen Zahlen, Zeitraum, Grund. Keine Inhalte von Anfragen oder Antworten; keine besonderen Kategorien (Art. 9 DSGVO) |
| Kategorien von Empfängern | Administratoren [Funktionen]; [Nutzungsprüfer [Funktionen], wenn die personenbezogene Auswertung eingeschaltet ist]; [IT-Dienstleister als Auftragsverarbeiter nach Art. 28 DSGVO] |
| Übermittlung in Drittländer | Keine für die Nutzungsdaten |
| Löschfristen | Einträge zu Anfragen [90] Tage; tägliche Zahlen pro Person [90] Tage; Zahlen ohne Personenbezug [90] Tage stündlich, [25] Monate täglich; Löschung mit dem Konto; Audit-Protokoll [Dauer] |
| Technische und organisatorische Maßnahmen (Art. 32 DSGVO) | Rollenbasierter Zugriff; eine eigene Rolle für persönliche Zahlen, die niemand hat, solange sie nicht vergeben wird, und die Administratorrechte nicht einschließen; Mindestgruppengröße [5] für jede angezeigte Zahl mit Unterdrückung des Rests; persönliche Zahlen höchstens pro Tag, kein Gerät und keine Uhrzeit in den Zahlen; Begründung und Aufzeichnung jeder Einsicht; automatische Löschung; manipulationssicheres Audit-Protokoll; eigene Ansicht und Download für jede Person; [Verschlüsselung der Datenträger des Servers, Sicherungen, Zugang zum Server: nach Ihrem Betrieb]. Konfigurationsbeschreibung vom [Datum] beigefügt |

## Interessenabwägung {#legitimate-interest-assessment}

Eine Interessenabwägung nach Art. 6 Abs. 1 lit. f DSGVO in ihren drei Schritten.

**1. Berechtigtes Interesse.** [Unternehmen] muss wissen und steuern, was die KI-Plattform kostet ([externe
Modellanbieter, Hardware, Energie]), ihre Kapazität planen, sie fair auf die Teams verteilen und sie vor
ausuferndem Verbrauch schützen. [Ergänzen Sie eigene Interessen, etwa die Verrechnung auf Kostenstellen.]

**2. Erforderlichkeit.** Summen pro Team, Agent und Modell lassen sich nur aus dem Eintrag jeder Anfrage bilden.
Geprüfte und genutzte mildere Mittel:

- Zahlen über andere nur zusammengefasst, für Gruppen von mindestens [5] Personen (personenbezogene Auswertung
  aus);
- Limits für die gesamte Plattform, die keine personenbezogenen Daten brauchen;
- Zahlen pro Person höchstens pro Tag [pro Monat], ohne Uhrzeit und ohne Gerät;
- kurze Speicherdauer ([90] Tage für Einträge zu Anfragen und Zahlen pro Person).

[Wenn die personenbezogene Auswertung eingeschaltet ist: warum Summen für die genannten Gründe nicht genügen,
etwa ein Streit über Kosten, der sich nur mit den eigenen Zahlen der Person klären lässt.]

**3. Abwägung.** Die Interessen der Beschäftigten: nicht überwacht oder verglichen zu werden; keine Rückschlüsse
auf Arbeitszeiten; kein Druck auf Mitglieder kleiner Teams. Schutzmaßnahmen, die für die Verarbeitung sprechen:

- niemand sieht die Zahlen einer anderen Person, es sei denn, die personenbezogene Auswertung ist eingeschaltet;
  dann nur benannte Nutzungsprüfer, aus vereinbarten Gründen, jede Einsicht mit Begründung und aufgezeichnet;
- jede angezeigte Zahl umfasst mindestens [5] Personen, und die Unterdrückung verhindert, dass sich eine Zahl aus
  den anderen errechnen lässt;
- keine Rangfolgen, Vergleiche oder Punktwerte von Personen;
- jede Person sieht ihre eigenen Zahlen und kann sie herunterladen;
- die Beschäftigten sind informiert (Datenschutzhinweise), und der Betriebsrat hat zugestimmt
  [Betriebsvereinbarung];
- Nutzungsdaten werden nicht zur Bewertung von Beschäftigten verwendet (Zweckbestimmung, Betriebsvereinbarung).

Beschäftigte müssen vernünftigerweise damit rechnen, dass die Kosten eines Arbeitsmittels gemessen werden; sie
müssen nicht damit rechnen, dass ihre persönliche Nutzung ausgewertet wird, und sie wird es nicht.

**Ergebnis.** [Die Interessen von [Unternehmen] überwiegen / überwiegen nicht, unter diesen Schutzmaßnahmen.]
Geprüft von [Name] am [Datum], Datenschutzbeauftragter beteiligt am [Datum]. Erneute Prüfung am [Datum] oder wenn
sich Zwecke oder Konfiguration ändern.

## Baustein für die Datenschutz-Folgenabschätzung {#dpia-input}

Ein Baustein für die Datenschutz-Folgenabschätzung (DSFA, Art. 35 DSGVO) der KI-Plattform.

**Ist eine DSFA erforderlich?** Nr. 8 der DSFA-Muss-Liste der Datenschutzkonferenz (DSK) nennt die umfangreiche
Verarbeitung von Daten über das Verhalten von Beschäftigten, die zur Bewertung ihrer Arbeitstätigkeit so
eingesetzt werden können, dass sich Rechtsfolgen ergeben oder die Betroffenen in anderer Weise erheblich
beeinträchtigt werden. Für zusammengefasste Zahlen von Gruppen ab fünf Personen lässt sich vertreten, dass das
nicht zutrifft; mit eingeschalteter personenbezogener Auswertung gehen Sie davon aus, dass es zutrifft. Die
meisten Organisationen bewerten die KI-Plattform als Ganzes; fügen Sie dies als Baustein hinzu.

**Beschreibung.** Das Gateway der KI-Plattform erfasst jede Anfrage (siehe die Datenfelder). Alle paar Minuten
fasst es die Einträge zusammen:

```text
Anfrage (Chat, Agent, Anwendung)
  -> Gateway -> Anfrageprotokoll: ein Eintrag pro Anfrage, mit der Person        [90] Tage
       -> stündliche Zahlen: Team, Agent, Modell, Provider; keine Person          [90] Tage
       -> tägliche Zahlen: dieselben Schlüssel; keine Person                      [25] Monate
       -> tägliche Zahlen pro Person: Person, Team, Projekt, Agent, Modell        [90] Tage
            -> Seite „Nutzung“: Summen ab [5] Personen (Administratoren, Prüfer)
            -> Meine Nutzung: die eigenen Zahlen jeder Person und der Download
            -> personenbezogene Auswertung: Nutzungsprüfer, wenn eingeschaltet, mit Grund, aufgezeichnet
            -> Download der Daten einer Person durch einen Nutzungsprüfer (Auskunft): mit Grund, aufgezeichnet
Limits für die gesamte Plattform: eine Zählung pro Minute und pro Tag, keine Person, nicht aufbewahrt
```

**Notwendigkeit und Verhältnismäßigkeit.** Siehe die [Interessenabwägung](#legitimate-interest-assessment) und
die Konfigurationsbeschreibung vom [Datum].

**Risiken und Maßnahmen.**

| Risiko | Was geschehen könnte | Maßnahmen |
|---|---|---|
| Überwachungsdruck | Beschäftigte fühlen sich beobachtet und ändern, wie sie arbeiten | Personenbezogene Auswertung voreingestellt aus; keine Rangfolgen; eigene Ansicht; ausgeschlossene Verwendungen in der Betriebsvereinbarung |
| Re-Identifizierung | Die Summe eines kleinen Teams oder eines Agenten ist die einer Person | Mindestgruppengröße [5] für jede Zahl; kleine Gruppen zusammengefasst, mit Unterdrückung, sodass sich keine durch Abziehen errechnen lässt; Hinweis bei kleinen Instanzen |
| Zweckentfremdung | Zahlen gelangen in Beurteilungen oder Auswahlentscheidungen | Zweckbestimmung und ausgeschlossene Verwendungen; Betriebsvereinbarung; Grund und Aufzeichnung jeder Einsicht |
| Rückschluss auf Arbeitszeiten | Stündliche Verläufe zeigen, wann jemand arbeitet | Keine Zahlen pro Person nach Stunden; [Granularität Monat] |
| Unbefugter Zugriff oder Kopien | Persönliche Zahlen verlassen das System | Eigene Rolle, die niemand hat, solange sie nicht vergeben wird; Aufzeichnung jeder Einsicht; Regel gegen Kopien (Hinweis für Administratoren) |
| Zu lange Speicherung | Alte Daten dienen neuen Zwecken | Automatische Löschung nach [90] Tagen / [25] Monaten; Löschung mit dem Konto |
| Fehlende Transparenz | Beschäftigte wissen nicht, was gezählt wird | Datenschutzhinweise; **Meine Nutzung** zeigt die Einstellung und die Zahl der Einsichten; Download |

**Maßnahmen nach Gewährleistungszielen** (Standard-Datenschutzmodell):

| Gewährleistungsziel | Maßnahme |
|---|---|
| Datenminimierung | Keine Inhalte; Zahlen pro Person höchstens pro Tag; kein Gerät und keine Uhrzeit in den Zahlen; voreingestellt nur zusammengefasste Zahlen |
| Verfügbarkeit | Sicherungen des Servers [nach Ihrem Betrieb] |
| Integrität | Manipulationssicheres Audit-Protokoll; Zahlen werden aus dem Anfrageprotokoll neu berechnet |
| Vertraulichkeit | Rollenbasierter Zugriff; eigene Rolle für Nutzungsprüfer; Mindestgruppengröße |
| Nichtverkettung | Nutzungszahlen getrennt von Chat-Inhalten; Konto-IDs statt Namen in den Einträgen |
| Transparenz | Datenschutzhinweise; eigene Ansicht; Aufzeichnung der Einsichten für die Person sichtbar; Verzeichnis der Datenfelder; Konfigurationsbeschreibung |
| Intervenierbarkeit | Download für Auskunftsersuchen; Löschung mit dem Konto; der Betriebsrat liest das Audit-Protokoll [mit der Rolle `auditor`] |

**Standpunkt der Betroffenen** (Art. 35 Abs. 9 DSGVO): [Der Betriebsrat wurde am [Datum] beteiligt; seine
Stellungnahme].

## Anlage zur Betriebsvereinbarung {#works-agreement-annex}

Eine Anlage zu Ihrer Betriebsvereinbarung über die KI-Plattform: **Anlage „Nutzungsmessung und Budgets“**. Sie
passt zu einem Entwurf von Arbeitnehmerseite wie zu einem von Arbeitgeberseite: Diese unterscheiden sich in der
gewählten Konfiguration, nicht in dem, was die Anlage regeln muss.

> **Anlage [Nr.] zur Betriebsvereinbarung [Titel] vom [Datum]: Nutzungsmessung und Budgets**
>
> **1. Gegenstand.** Diese Anlage regelt die Nutzungsmessung und die Limits der KI-Plattform Fadenstack, Version
> [Version], wie in ihrer Hilfe beschrieben (Nutzung und Limits; Nutzung und Datenschutz). Das Verzeichnis der
> Datenfelder dieser Version und die Konfigurationsbeschreibung vom [Datum] sind beigefügt.
>
> **2. Zwecke.** Nutzungsdaten dürfen nur verarbeitet werden, um die Plattform zu betreiben und ihre Kapazität zu
> planen, ihre Kosten zu kennen und Teams, Projekten und Agenten zuzuordnen, sie vor ausuferndem Verbrauch zu
> schützen und jeder Person ihre eigene Nutzung anzuzeigen [; sowie zur Klärung von Streitfragen über Kosten nach
> Nr. 6].
>
> **3. Ausgeschlossene Verwendungen.** Nutzungsdaten dürfen nicht verwendet werden, um Leistung oder Verhalten von
> Beschäftigten zu überwachen oder zu bewerten, um Beschäftigte zu vergleichen, oder als Grundlage für
> Beurteilungen, Vergütung, Beförderungen, Auswahlentscheidungen, Abmahnungen, Kündigungen oder andere personelle
> Maßnahmen. Personelle Maßnahmen, die auf Daten beruhen, die unter Verstoß gegen diese Anlage verarbeitet wurden,
> [sind unwirksam / unterbleiben]. Nutzungsdaten dürfen nicht durch ein KI-Modell personenbezogen ausgewertet
> werden.
>
> **4. Daten.** Verarbeitet werden nur die Felder des beigefügten Verzeichnisses. Eine Version, die Felder oder
> Auswertungen hinzufügt oder eine Voreinstellung ändert, wird erst eingeführt, nachdem der Betriebsrat
> unterrichtet wurde (§ 90 BetrVG) und diese Anlage [angepasst / bestätigt] wurde.
>
> **5. Konfiguration.**
>
> | Einstellung | Vereinbart |
> |---|---|
> | Personenbezogene Auswertung | [Aus / Ein, nach Nr. 6] |
> | Mindestgruppengröße | [5 oder höher] |
> | Granularität persönlicher Zahlen | [Tag / Monat] |
> | Einträge zu Anfragen | [90] Tage |
> | Tägliche Zahlen pro Person | [90] Tage |
> | Stündliche Zahlen, tägliche Zahlen | [90] Tage, [25] Monate |
> | Limits für die gesamte Plattform | Anfragen pro Minute [ ], Tokens pro Minute [ ], Tokens pro Tag [ ] |
> | Budgets | [Keine / wie in [Anlage] geregelt] |
>
> Änderungen der Konfiguration bedürfen der [Zustimmung] des Betriebsrats.
>
> **6. Persönliche Zahlen** (nur bei eingeschalteter personenbezogener Auswertung). Die Rolle `usage_auditor`
> haben nur [Namen oder Funktionen]. Sie dürfen persönliche Zahlen nur aus [Gründe] ansehen, jedes Mal mit
> Begründung [und nach Zustimmung von [Mitglied des Betriebsrats / Datenschutzbeauftragter]]. Zur Beantwortung
> eines Auskunftsersuchens dürfen sie die Daten einer Person herunterladen, mit dem Ersuchen als Grund. Die
> betroffene
> Person [wird binnen [Frist] informiert / sieht Datum und Grund in ihrem Download].
>
> **7. Rollen.** Administratoren: [Funktionen]. Lesen des Audit-Protokolls und der zusammengefassten Zahlen (Rolle
> `auditor`): [Datenschutzbeauftragter, Mitglied des Betriebsrats]. Niemand sonst hat die Rolle `usage_auditor`.
>
> **8. Limits und Benachrichtigungen.** Lehnt ein Limit eine Anfrage ab, erfährt das nur die Person, die sie
> gestellt hat, mit der Art des Limits und der Stelle, an die sie sich wenden kann. [Budgets und ihre
> Benachrichtigungen: [keine / wie in [Anlage] geregelt].] Wer mehr braucht, wendet sich an [Kontakt].
>
> **9. Kontrolle.** Der Betriebsrat kann die Aufzeichnungen der Einsichten in persönliche Zahlen und der Änderungen
> der Konfiguration [jederzeit / vierteljährlich] durch [Mitglied] mit der Rolle `auditor` einsehen. Das
> Audit-Protokoll wird [Dauer] aufbewahrt.
>
> **10. Neue Versionen.** Der Arbeitgeber unterrichtet den Betriebsrat vor jedem Upgrade, das die
> Versionshinweise als datenschutzrelevant kennzeichnen, mit dem neuen Verzeichnis der Datenfelder und der neuen
> Konfigurationsbeschreibung.

## Informationsblatt für den Betriebsrat {#information-sheet-for-the-works-council}

Unterrichtung nach § 90 Abs. 1 Nr. 3 BetrVG über die Planung von Arbeitsverfahren und Arbeitsabläufen
einschließlich des Einsatzes von Künstlicher Intelligenz, für die erste Sitzung des Betriebsrats zur
Nutzungsmessung. Übergeben Sie es so rechtzeitig, dass Vorschläge und Bedenken des Betriebsrats bei der Planung
noch berücksichtigt werden können (§ 90 Abs. 2 BetrVG).

> **Nutzungsmessung auf der KI-Plattform: Information für den Betriebsrat**
>
> **Was geplant ist.** [Unternehmen] [betreibt / plant den Betrieb] der KI-Plattform Fadenstack [Version] auf
> eigenen Servern. [Geplante Änderung: Einführung der Nutzungsmessung / Einschalten der personenbezogenen
> Auswertung / neue Limits.] [Geplanter Zeitpunkt.]
>
> **Was gezählt wird.** Jede Anfrage an ein KI-Modell: wer sie gesendet hat (Benutzerkonto), sein Team, das
> Chat-Projekt oder der Agent, das Modell, der Zeitpunkt, die Menge an Text hinein und heraus (Tokens), die
> geschätzten Kosten, Erfolg oder Fehler. Bei Agenten außerdem das Gerät, auf dem sie angemeldet sind. Nicht
> gezählt: was geschrieben oder geantwortet wurde.
>
> **Was daraus wird.** Alle paar Minuten werden die Einträge zusammengefasst: pro Stunde und pro Tag für Teams,
> Agenten und Modelle, ohne Personen; pro Tag für jede Person.
>
> **Wer was sieht.** Alle Beschäftigten sehen ihre eigenen Zahlen. Administratoren sehen Summen nur dort, wo
> mindestens [5] Personen hinter einer Zahl stehen; die Einträge zu Anfragen, mit denen sie Fehler
> nachverfolgen, zeigen nicht, wer eine Anfrage gesendet hat. Niemand sieht Rangfolgen oder Vergleiche von
> Personen. [Geplant: [Anzahl] benannte Personen dürfen
> persönliche Zahlen aus [Gründe] ansehen, jedes Mal mit Begründung, aufgezeichnet.]
>
> **Limits.** [Keine / Anfragen pro Minute [ ], Tokens pro Minute [ ], Tokens pro Tag [ ]] für die gesamte
> Plattform. Sie zählen alle zusammen und speichern nichts über Einzelne. Wird eines erreicht, erfährt das nur die
> Person, deren Anfrage abgelehnt wird.
>
> **Wie lange.** Einträge zu Anfragen [90] Tage; tägliche Zahlen pro Person [90] Tage; Summen [25] Monate.
>
> **Was ausgeschlossen ist.** Überwachung oder Bewertung von Beschäftigten; Beurteilungen, Vergütung,
> Auswahlentscheidungen, Abmahnungen oder Kündigungen auf Grundlage von Nutzungsdaten.
>
> **Mitbestimmung.** Die Plattform erfasst Daten über die Nutzung durch Einzelne; sie unterliegt daher § 87
> Abs. 1 Nr. 6 BetrVG. [Unternehmen] schlägt die beigefügte Anlage vor. [Sachverständiger nach § 80 Abs. 3
> BetrVG: [Vereinbarung].]
>
> **Beigefügt.** Verzeichnis der Datenfelder; Konfigurationsbeschreibung vom [Datum]; Entwurf der Anlage;
> Baustein für die DSFA.
>
> **Ansprechperson.** [Name, Funktion, Kontakt.]

## Verfahren für Auskunfts- und Löschersuchen {#access-request-and-erasure-procedure}

So beantworten Sie Anfragen zu Nutzungsdaten. Die gesetzliche Frist beträgt einen Monat (Art. 12 Abs. 3 DSGVO).

**Auskunft (Art. 15 DSGVO)**

1. Erfassen Sie das Ersuchen und prüfen Sie, wer anfragt, wie bei jedem Ersuchen von Betroffenen.
2. Bitten Sie die Person, **Meine Nutzung** zu öffnen und ihre Daten herunterzuladen (JSON oder CSV), oder führen
   Sie sie durch. Der Download enthält ihre täglichen Zahlen, ihre noch aufbewahrten Einträge zu Anfragen (Zeit,
   Modell, Agent, Tokens, Kosten, Ergebnis) sowie Datum und Grund jeder Einsicht in ihre Zahlen.
3. Ergänzen Sie die Informationen nach Art. 15 Abs. 1 lit. a bis h DSGVO: Zwecke, Kategorien, Empfänger,
   Speicherdauer, Rechte, Herkunft. Die Datenschutzhinweise enthalten sie.
4. Für jemanden, der sich nicht anmelden kann, lädt ein Nutzungsprüfer dieselben Daten in der
   personenbezogenen Auswertung unter **Nutzung** herunter und gibt das Ersuchen als Grund an. Der Download wird
   aufgezeichnet.

**Löschung (Art. 17 DSGVO)**

- Nutzungsdaten werden am Ende ihrer Speicherdauer von selbst gelöscht und mit dem Konto gelöscht, wenn das
  Konto gelöscht wird.
- Fadenstack kann die Nutzungsdaten einer Person nicht löschen, solange ihr Konto besteht. [Ihr Verfahren, etwa:
  die Speicherdauer für alle verkürzen oder das Konto löschen.]
- Was bleibt: Summen nach Team, Agent und Modell ohne die Person; Sicherungen, bis sie ersetzt werden; die
  Aufzeichnungen des Audit-Protokolls über Einsichten in die Zahlen der Person, aufbewahrt [Dauer].

**Widerspruch (Art. 21 DSGVO)**

- Bei ausgeschalteter personenbezogener Auswertung wertet niemand persönliche Zahlen aus; prüfen Sie, ob die
  übrige Verarbeitung (Summen, Einträge zu Anfragen für die Fehlersuche) mit Blick auf die besondere Situation der
  Person gerechtfertigt ist.
- Bei eingeschalteter: Fadenstack hat keinen Schalter, der eine einzelne Person ausnimmt. [Ihre Maßnahme, etwa:
  Die Nutzungsprüfer sehen die Zahlen dieser Person nicht an; festgehalten in [Verzeichnis].]

**Dokumentieren** Sie jedes Ersuchen, die Antwort und das Datum in [Ihrem Verzeichnis der Betroffenenanfragen].

## Zweckbestimmung {#intended-purpose}

Die Zweckbestimmung der Nutzungsmessung und der Limits, wie Fadenstack sie in seiner Hilfe angibt (siehe
[Nutzung und Datenschutz](usage-privacy.md#what-usage-data-is-for)). Übernehmen Sie sie in Ihre Regeln zur
KI-Nutzung und Ihre Anweisungen für Administratoren.

> **Zweckbestimmung.** Die Nutzungszahlen und Limits von Fadenstack dienen dem Betrieb der KI-Plattform und der
> Planung ihrer Kapazität, der Kenntnis ihrer Kosten und deren Zuordnung zu Teams, Projekten und Agenten, dem
> Schutz vor ausuferndem Verbrauch und der Anzeige der eigenen Nutzung für jede Person.
>
> **Ausgeschlossene Verwendungen.** Sie dienen nicht, und dürfen nicht verwendet werden, zur Überwachung oder
> Bewertung der Leistung oder des Verhaltens einzelner Beschäftigter oder zu deren Vergleich; für Beurteilungen,
> Vergütung, Beförderungen, Auswahlentscheidungen, Abmahnungen oder Kündigungen; zur Suche nach „Vielnutzern“
> oder nach Personen, die wenig KI nutzen; oder zur Auswertung der Nutzung einer Person durch ein KI-Modell.
>
> **Warum das wichtig ist.** Ein KI-System, das dazu dient, Beschäftigte zu überwachen oder zu bewerten, ist nach
> der KI-Verordnung ein Hochrisiko-KI-System (Anhang III Nr. 4 lit. b KI-VO). Eine Organisation, die Fadenstack
> oder einen darauf gebauten Agenten so verwendet, kann Anbieter eines Hochrisiko-KI-Systems werden (Art. 25
> Abs. 1 lit. c KI-VO), mit den Pflichten eines Anbieters.
>
> [Unsere Organisation übernimmt diese Zweckbestimmung. Fragen an [Kontakt].]
