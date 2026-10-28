# Nutzung und Datenschutz {#usage-and-privacy}

Nutzungszahlen sagen, wer welches Modell wie viel und wann genutzt hat. Im Betrieb sind das Daten über
Beschäftigte. Von Haus aus zeigt Fadenstack niemandem die Nutzungszahlen einer anderen Person: Jede Person sieht
ihre eigenen, Administratoren sehen Zahlen für Gruppen von mindestens fünf Personen. Diese Seite beschreibt, was
gespeichert wird, wer es sieht, wie lange es aufbewahrt wird und was jede Einstellung ändert, damit Sie mit Ihrem
Datenschutzbeauftragten und, wo es einen gibt, Ihrem Betriebsrat entscheiden können. Vorlagen dafür stehen unter
Unterlagen für Datenschutz und Betriebsrat.

!!! note "Keine Rechtsberatung"
    Diese Seiten beschreiben, was Fadenstack tut, und bieten Vorlagen an; sie sind keine Rechtsberatung. Ihre
    Organisation entscheidet, wozu und wie Nutzung gezählt wird, und ist damit Verantwortlicher im Sinne der
    DSGVO. Die Nutzungsdaten bleiben auf Ihrem Server: Nichts davon geht an Fadenstack, den Hersteller der
    Software. Entscheiden Sie die Punkte, die bei Ihnen liegen, mit Ihrem Datenschutzbeauftragten, Ihrer
    Rechtsberatung und, wo es einen gibt, Ihrem Betriebsrat.

## Datenschutzfreundlich voreingestellt {#private-by-default}

Nach der Installation, und solange Sie keine Einstellung ändern:

- **Niemand sieht die Nutzungszahlen einer anderen Person.** Die personenbezogene Auswertung ist aus. Die Seite
  **Nutzung** zeigt Teams, Agenten, Modelle und Provider nur dort, wo mindestens fünf Personen hinter einer Zahl
  stehen (siehe [Die Mindestgruppengröße](usage.md#the-minimum-group-size)).
- **Jede Person sieht ihre eigenen**, unter **Meine Nutzung**, und kann sie herunterladen.
- **Nichts stellt Personen in eine Rangfolge oder vergleicht sie**: keine Top-Benutzer, keine Bestenlisten,
  keine Punktwerte.
- **Personenbezogene Zahlen gibt es höchstens pro Tag, und sie werden 90 Tage aufbewahrt.** Das
  Anfrageprotokoll, aus dem sie stammen, ebenfalls 90 Tage. Keine Zahl enthält die Uhrzeit, zu der eine Person
  etwas genutzt hat, oder ihr Gerät.
- **Die Limits der Instanz kennen keine Person**: Sie zählen die Anfragen aller zusammen.
- **Wer die Zahlen einer einzelnen Person ansehen darf**, regelt eine Rolle, die bei der Installation niemand
  hat, auch kein Superuser.

## Die Einstellungen {#the-settings}

Öffnen Sie **Einstellungen** und wählen Sie den Reiter **Nutzung und Datenschutz**. Administratoren ändern die
Einstellungen; **Personenbezogene Auswertung** braucht außerdem den **Root-Modus**. Jede Änderung wird mit dem
Namen der Person, die sie vorgenommen hat, und der Zeit im Audit-Protokoll festgehalten. Eine Änderung gilt binnen
Minuten; nichts wird neu gestartet.

| Einstellung | Voreinstellung | Bereich | Was eine Änderung bedeutet |
|---|---|---|---|
| **Personenbezogene Auswertung** (`usage.person_reporting`) | Aus | Aus, ein | Ein: Personen mit der Rolle `usage_auditor` können die Zahlen einer Person oder eines Teams pro Person ansehen, jedes Mal mit Begründung (siehe [Die Zahlen einer Person ansehen](#looking-at-one-persons-figures)), und sehen auf den Kostenseiten, wer jede Anfrage gesendet hat. **Meine Nutzung** sagt das dann allen. Das ist eine Auswertung von Beschäftigtendaten pro Person: Sie unterliegt der Mitbestimmung des Betriebsrats (§ 87 Abs. 1 Nr. 6 BetrVG); vereinbaren Sie sie also, bevor Sie sie einschalten. Gehen Sie davon aus, dass eine Datenschutz-Folgenabschätzung nötig ist, und informieren Sie die Beschäftigten (die Fassung für die personenbezogene Auswertung unter Unterlagen). |
| **Mindestgruppengröße** (`usage.min_group_size`) | 5 | 5 bis 50 | Jede Zahl unter **Nutzung** umfasst mindestens so viele Personen. Erhöhen Sie sie, wenn Ihre Betriebsvereinbarung mehr verlangt; unter 5 geht es nicht. Je höher sie ist, desto mehr Teams, Agenten und Tage erscheinen nur in der Sammelzeile „weniger als …“. |
| **Granularität personenbezogener Zahlen** (`usage.person_granularity`) | Tag | Tag, Monat | Wie fein die personenbezogene Auswertung die Zahlen einer Person zeigt. Monat lässt weniger darauf schließen, wie jemand arbeitet. Feiner geht es nicht: Stündliche Zahlen einer Person gibt es nicht. |
| **Anfrageprotokoll** (`usage.request_log_retention_days`) | 90 Tage | 7 bis 365 Tage | Wie lange der Eintrag jeder Anfrage aufbewahrt wird: die Person, die Zeit auf die Sekunde, das Modell, die Tokens und bei Agenten das Gerät. Bewahren Sie ihn nur aus einem Grund länger auf, den Sie benennen können, etwa der Aufklärung von Sicherheitsvorfällen, und halten Sie den Grund schriftlich fest. |
| **Personenbezogene Zahlen** (`usage.person_retention_days`) | 90 Tage | 31 bis 400 Tage | Wie lange die täglichen Zahlen jeder Person aufbewahrt werden, für **Meine Nutzung** und die personenbezogene Auswertung. 90 Tage decken den laufenden Zeitraum, den davor und Zeit für die Klärung von Streitfragen ab; für eine längere Dauer halten Sie den Grund fest. |
| **Stündliche Zahlen** (`usage.hourly_retention_days`) | 90 Tage | 31 bis 180 Tage | Zahlen pro Stunde nach Team, Agent, Modell und Provider, für die Kapazitätsplanung. Sie enthalten keine Person. |
| **Tägliche Zahlen** (`usage.daily_retention_months`) | 25 Monate | 13 bis 60 Monate | Zahlen pro Tag nach Team, Agent, Modell und Provider, für den Vergleich mit dem Vorjahr und die Kosten. Sie enthalten keine Person. |
| **Zeitzone** (`usage.time_zone`) | Die Zeitzone des Servers oder UTC | Ein Zeitzonenname wie `Europe/Berlin` | Wo Tage und Monate beginnen und wann das Tageslimit neu beginnt. Ohne Einfluss auf den Datenschutz. |

??? info "Die Konfigurationsbeschreibung"
    Derselbe Reiter lädt eine **Konfigurationsbeschreibung** herunter: ein Dokument mit den Einstellungen, wie
    sie gerade sind, auf Deutsch, Englisch, Spanisch, Französisch oder Chinesisch. Fügen Sie sie der Anlage zur
    Betriebsvereinbarung und der Datenschutz-Folgenabschätzung bei, und laden Sie sie nach jeder Änderung neu
    herunter.

## Die Zahlen einer Person ansehen {#looking-at-one-persons-figures}

Die Zahlen einer einzelnen Person sieht nur, wer die Rolle `usage_auditor` hat, und nur, solange
**Personenbezogene Auswertung** eingeschaltet ist. Die Rolle ist eingebaut, bei der Installation hat sie niemand,
und ein Superuser hat sie nicht von selbst: Ein Administrator, der personenbezogene Zahlen ansehen soll, braucht
die Rolle ebenfalls.

1. Vereinbaren Sie mit Ihrem Datenschutzbeauftragten und Ihrem Betriebsrat, wer hineinsehen darf und aus welchen
   Gründen. Übliche Gründe: ein Streit über Kosten, ein Auskunftsersuchen der Person selbst, ein
   Sicherheitsvorfall.
2. Geben Sie diesen Personen die Rolle: **Zugriffskontrolle → Benutzer**, **Rollen bearbeiten** in ihrer Zeile,
   `usage_auditor` ankreuzen, **Speichern**.
3. Schalten Sie **Personenbezogene Auswertung** ein (siehe [Die Einstellungen](#the-settings)).

Ein Nutzungsprüfer findet die personenbezogene Auswertung dann unter **Observability → Nutzung**:

1. Wählen Sie eine Person oder ein Team und einen Zeitraum.
2. Geben Sie den Grund der Einsicht an: 10 bis 500 Zeichen.
3. Die Zahlen erscheinen pro Person und pro Tag (oder Monat), nach Namen sortiert, nie danach, wie viel jemand
   genutzt hat.

Jede Einsicht wird im Audit-Protokoll festgehalten (`usage.people.view`): wer hineingesehen hat, in wessen
Zahlen, für welchen Zeitraum und mit welchem Grund. Die Person sieht in ihrem Download Datum und Grund jeder
Einsicht in ihre Zahlen, aber nicht, wer hineingesehen hat; **Meine Nutzung** zeigt, wie viele es waren. Solange
die personenbezogene Auswertung eingeschaltet ist, sehen Nutzungsprüfer außerdem unter **Kosten-Observability →
Anfragen** und in deren CSV-Export, wer jede Anfrage gesendet hat; auch das wird so festgehalten.

??? info "Wer die Aufzeichnung prüft"
    Die eingebaute Rolle `auditor` liest das Audit-Protokoll und die Seite **Nutzung** und ändert nichts: eine
    passende Rolle für Ihren Datenschutzbeauftragten oder ein Mitglied des Betriebsrats. Unter **Logs → Audit**
    zeigt der Aktionsfilter `usage.` die Einsichten in personenbezogene Zahlen und die Änderungen an den Limits;
    die Änderungen an den Einstellungen stehen dort ebenfalls.

## Die eigene Ansicht und der Download {#your-own-view-and-the-download}

Jede Person, die sich anmeldet, hat **Meine Nutzung**, im Menü hinter ihrer Konto-Schaltfläche: ihre eigenen
Tokens und geschätzten Kosten nach Tag oder Monat, nach Modell, Agent und Chat-Projekt, und die Limits, die
gelten. Ist die personenbezogene Auswertung eingeschaltet, steht das dort, mit der Angabe, wer ihre Zahlen sehen
kann (die Nutzungsprüfer), wie fein und wie lange.

**Download** liefert ihre Daten als JSON oder CSV:

- ihre täglichen Zahlen;
- ihre Einträge, die noch im Anfrageprotokoll stehen: Zeit, Modell, Agent, Tokens, Kosten und Ergebnis, nie,
  was geschrieben wurde, und nie das Gerät;
- Datum und angegebenen Grund jeder Einsicht eines Nutzungsprüfers in ihre Zahlen.

Fragt jemand, was über seine Nutzung gespeichert ist (Art. 15 DSGVO), verweisen Sie ihn dorthin: Der Download
beantwortet das, zusammen mit Ihren Datenschutzhinweisen für Beschäftigte (Zwecke, Rechtsgrundlage, Empfänger,
Speicherdauer, Rechte). Der Download gehört der Person selbst: Ein Administrator kann ihn nicht für jemand
anderen herunterladen.

## Aufbewahrung und Löschung {#retention-and-erasure}

Fadenstack löscht regelmäßig und von selbst, was seine Aufbewahrungsdauer überschritten hat: Einträge des
Anfrageprotokolls, die älter sind als **Anfrageprotokoll**, personenbezogene Zahlen, die älter sind als
**Personenbezogene Zahlen**, stündliche und tägliche Zahlen, die älter sind als ihre Einstellung. Verkürzen Sie
eine Dauer, wird beim nächsten Lauf gelöscht, was nun zu alt ist.

| Ereignis | Was geschieht |
|---|---|
| Ein Konto wird gelöscht | Seine Einträge im Anfrageprotokoll und seine personenbezogenen Zahlen werden mit ihm gelöscht. Die Zahlen nach Team, Agent und Modell behalten seinen Anteil, ohne die Person. |
| Das Upgrade auf diese Version | Der erste Lauf fasst das vorhandene Anfrageprotokoll zu stündlichen und täglichen Zahlen zusammen und löscht dann Einträge, die älter sind als die Einstellung **Anfrageprotokoll** (90 Tage, wenn nicht geändert). |
| Die Minute oder der Tag eines Limits endet | Seine Zählung ist weg: Die Limits bewahren keinen Verlauf auf. |

??? info "Was nicht mit den Zahlen gelöscht wird"
    - **Sicherungen**, die vorher erstellt wurden, enthalten die Daten, bis sie ersetzt werden (siehe
      [Sicherungen](../operate/backups.md)).
    - **Das Audit-Protokoll** mit der Aufzeichnung jeder Einsicht in personenbezogene Zahlen löscht Fadenstack
      nie (siehe [Das Audit-Protokoll](users.md#the-audit-log)). Vereinbaren Sie mit Ihrem
      Datenschutzbeauftragten, wie lange Sie es brauchen.

## Die Datenfelder {#the-data-fields}

Was Fadenstack zur Nutzung speichert, wozu, wie fein, wie lange und wer es sieht. Die Liste beschreibt diese
Version; die Hilfe jeder Version hat ihre eigene Liste, und die Versionshinweise nennen Änderungen daran.

**Das Anfrageprotokoll** (`llm_gateway_request_log`): ein Eintrag pro Anfrage, die Quelle aller Zahlen.
Aufbewahrt 90 Tage (**Anfrageprotokoll**, `usage.request_log_retention_days`); gelöscht mit dem Konto.

| Felder | Wozu | Wer sie sieht |
|---|---|---|
| `user_id` | Die Person, die die Anfrage gesendet hat: ihre Konto-ID, nicht ihr Name oder ihre E-Mail-Adresse | Die Person, in ihrem Download. Administratoren, bei jedem Aufruf unter **LLM-Aufrufe**, um einen Fehler nachzuverfolgen. Auf den Kostenseiten nur Nutzungsprüfer, solange die personenbezogene Auswertung eingeschaltet ist |
| `group_id`, `project_id`, `agent_id` | Die Nutzungsgruppe (das Team) der Person, das Chat-Projekt, der Agent | Administratoren unter **LLM-Aufrufe**; in Summen unter **Nutzung**; die Person (Projekt, Agent) |
| `device_id`, `session_id`, `conversation_id`, `turn_id` | Verbinden die Anfrage eines Agenten mit seiner Anmeldung auf einem Gerät und mit seiner Unterhaltung | Administratoren unter **LLM-Aufrufe**. Nie in den Zahlen oder im Download |
| `request_id`, `trace_id`, `tenant_id`, `endpoint`, `stream` | Eine Anfrage finden und wie sie gestellt wurde | Administratoren |
| `model_alias`, `provider_instance_id` | Welches Modell geantwortet hat und wo es lief | Administratoren; in Summen; die Person (Modell) |
| `created_at` | Wann, auf die Sekunde | Administratoren; die Person |
| `prompt_tokens`, `cached_tokens`, `cache_write_tokens`, `completion_tokens`, `reasoning_tokens`, `total_tokens` | Die Tokens (siehe [Was gezählt wird](usage.md#what-is-counted)) | Administratoren; in Summen; die Person |
| `usage_source` | Woher die Zahl der Tokens kommt: `provider`, `counted` oder `estimated` | Administratoren |
| `estimated_input_cost`, `estimated_output_cost`, `estimated_total_cost`, `currency`, `price_catalog_id`, `cost_estimate_status` | Die geschätzten Kosten und der Preis, mit dem sie berechnet wurden | Administratoren; in Summen; die Person |
| `status_code`, `error_code`, `finish_reason`, `retry_count` | Das Ergebnis: Erfolg, Fehler oder das Limit, das abgelehnt hat | Administratoren; in Summen; die Person (Ergebnis) |
| `latency_ms`, `ttft_ms` | Wie lange es gedauert hat, und bis zum ersten Token | Administratoren |
| `skills_used`, `mcp_tool_call_count`, `mcp_tool_loop_iterations`, `rag_context` | Welche Skill-Versionen sie genutzt hat und wie viele Tool-Runden | Administratoren |
| Tool-Aufrufe (`llm_tool_call_log`): das Tool, sein Server, die Dauer, die Art des Fehlers und der Anfang der Antwort eines fehlgeschlagenen Tools (verschlüsselt) | Warum ein Tool fehlschlug | Administratoren unter **LLM-Aufrufe**; gelöscht mit der Anfrage |

**Die Zahlen**: das zusammengefasste Anfrageprotokoll. Jede Zeile enthält die Größen `requests`, `failed`,
`refused`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `cost` und `currency`
(siehe [Was gezählt wird](usage.md#what-is-counted)).

| Tabelle | Eine Zeile pro | Enthält außerdem | Aufbewahrt (Einstellung) | Wer sie sieht |
|---|---|---|---|---|
| `usage_hourly` | Stunde (UTC), Team, Agent, Modell, Provider und Währung | `people`: wie viele Personen hinter der Zeile stehen. Keine Person, kein Projekt, kein Gerät | 90 Tage (`usage.hourly_retention_days`) | Administratoren und Prüfer (`usage:read`) unter **Nutzung**, nur Stunden mit mindestens fünf Personen |
| `usage_daily` | Tag (Zeitzone der Nutzungsmessung), Team, Agent, Modell, Provider und Währung | `people`. Keine Person, kein Projekt, kein Gerät | 25 Monate (`usage.daily_retention_months`) | Administratoren und Prüfer unter **Nutzung**, nach der Mindestgruppengröße |
| `usage_daily_person` | Tag, Person, Team, Projekt, Agent, Modell und Währung | Kein Gerät, keine Uhrzeit | 90 Tage (`usage.person_retention_days`); gelöscht mit dem Konto | Die Person, unter **Meine Nutzung** und im Download. Nutzungsprüfer, solange die personenbezogene Auswertung eingeschaltet ist, nach Tag oder Monat (`usage.person_granularity`) |
| `usage_rollup_state` | Eine Zeile insgesamt | Wie weit die Zahlen zusammengefasst sind und wann zuletzt zusammengefasst und gelöscht wurde. Keine personenbezogenen Daten | Aufbewahrt | Niemand |

**Die Limits**: die eingestellten Werte (`rpm_limit`, `tpm_limit`, `tokens_per_day` der Richtlinie der Instanz)
und ihre Zählungen, eine pro Instanz, die nur für ihre Minute oder ihren Tag bestehen. Keine
personenbezogenen Daten.

**Die Aufzeichnung, wer hineingesehen und was sich geändert hat**, im Audit-Protokoll: jede Einsicht in
personenbezogene Zahlen (`usage.people.view`: wer, in wessen Zahlen, welcher Zeitraum, der Grund), jede Änderung
an den Limits (`usage.limits.update`) und an den Einstellungen zur Nutzung sowie das Lesen der Kostenseiten.
Aufbewahrt so lange wie das Audit-Protokoll (siehe oben). Sichtbar für Administratoren und Prüfer; die Person
sieht Datum und Grund der Einsichten in ihre eigenen Zahlen.

??? info "Andere Stellen der Konsole, die Personen nennen"
    Sie dienen dem Betrieb und der Sicherheit des Dienstes; sie gehören nicht zu den Nutzungszahlen, und die
    Einstellungen auf dieser Seite ändern sie nicht. Nennen Sie sie in Ihrer Vereinbarung ebenfalls.

    - **LLM-Aufrufe** zeigt bei jedem Aufruf die Konto-ID und findet Aufrufe danach (siehe
      [LLM-Aufrufe](llm-calls.md)).
    - Der Reiter **Aktivität** eines Agenten nennt, wer sich auf welchem Gerät beim Agenten angemeldet hat, und
      listet, was die Anwendungen gemeldet haben, etwa ausgeführte Tools und erteilte Freigaben. Seine Zahlen zu
      Anfragen und Tokens gelten pro Agent, auch für einen Agenten, den nur eine Person nutzt (siehe
      [Agenten und Add-ins](agents.md)).
    - Die Chat-Seiten zeigen, wer welche Chats, Projekte und Dateien hat und seit wann, aber nicht deren Inhalt
      (siehe [Ihre Daten und Datenschutz](../use/privacy.md)).
    - **Zugriffskontrolle → API-Tokens** zeigt die Verwendungen jedes Tokens: wann, von welcher Adresse, mit
      welcher Anwendung.
    - Das Audit-Protokoll nennt, wer was getan hat, mit E-Mail- und IP-Adressen (siehe
      [Das Audit-Protokoll](users.md#the-audit-log)).

## Wofür Nutzungsdaten da sind {#what-usage-data-is-for}

Die Nutzungszahlen und Limits von Fadenstack sind gebaut für:

- den Betrieb des Dienstes und die Planung seiner Kapazität;
- die Kenntnis der Kosten und ihre Zuordnung zu Teams, Projekten und Agenten;
- den Schutz vor ausuferndem Verbrauch, etwa durch ein Skript oder einen Agenten, der sich im Kreis dreht;
- die Anzeige der eigenen Nutzung für jede Person.

Sie sind nicht gebaut für, und dürfen nicht verwendet werden für:

- die Überwachung oder Bewertung der Leistung oder des Verhaltens einzelner Beschäftigter oder deren Vergleich;
- Beurteilungen, Vergütung, Beförderung, Auswahl, Abmahnungen oder Kündigungen;
- die Suche nach „Vielnutzern“ oder nach Personen, die wenig KI nutzen;
- die Auswertung der Nutzung einer Person durch ein KI-Modell.

!!! warning "Wer Nutzungsdaten zur Bewertung von Beschäftigten verwendet, ändert, was das System ist"
    Nutzung zu zählen und zusammenzufassen ist kein KI-System im Sinne der KI-Verordnung. Der Chat und die
    Agenten von Fadenstack sind es, und ihre Zweckbestimmung ist eine allgemeine Assistenz- und Agentenplattform,
    nicht die Bewertung von Beschäftigten. Ein KI-System, das dazu dient, Leistung und Verhalten von Beschäftigten
    zu überwachen oder zu bewerten, ist ein Hochrisiko-KI-System (Anhang III Nr. 4 lit. b KI-VO). Eine
    Organisation, die Fadenstack oder einen darauf gebauten Agenten nutzt, um Beschäftigte anhand von
    Nutzungsdaten zu bewerten, kann Anbieter eines Hochrisiko-KI-Systems werden (Art. 25 Abs. 1 lit. c KI-VO),
    mit den Pflichten eines Anbieters. In Deutschland braucht sie dafür außerdem den Betriebsrat (§ 87 Abs. 1
    Nr. 6 BetrVG) und geht über das hinaus, was eine Betriebsvereinbarung zu Kosten und Kapazität erlaubt.

## Ein Hinweis für Administratoren {#a-note-for-administrators}

Nutzungszahlen handeln von Ihren Kolleginnen und Kollegen. Bevor Sie damit arbeiten:

- Verwenden Sie sie für Kosten, Kapazität und die faire Verteilung der Modelle. Nie, um die Arbeit einer Person
  zu beurteilen.
- Bevor Sie die personenbezogene Auswertung einschalten, fragen Sie Ihren Datenschutzbeauftragten und, wo es
  einen gibt, Ihren Betriebsrat.
- Sehen Sie die Zahlen einer Person nur aus einem Grund an, den Ihre Organisation vereinbart hat, und schreiben
  Sie den Grund so, wie Sie ihn der Person selbst schreiben würden: Sie sieht ihn in ihrem Download.
- Kopieren Sie personenbezogene Zahlen nicht aus Fadenstack heraus, in Screenshots oder Tabellen: Dort entgehen
  sie der Aufbewahrungsdauer und der Aufzeichnung, wer hineingesehen hat.
- Fragt jemand, was über seine Nutzung gespeichert ist, verweisen Sie ihn auf **Meine Nutzung** und den Download.
- Sind Sie unsicher, ob eine Verwendung erlaubt ist, fragen Sie vorher Ihren Datenschutzbeauftragten.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| Die personenbezogene Auswertung sagt, sie sei ausgeschaltet | Sie ist aus, solange niemand sie eingeschaltet hat. Schalten Sie sie unter **Einstellungen → Nutzung und Datenschutz** mit dem **Root-Modus** ein, sobald sie vereinbart ist. |
| Die personenbezogene Auswertung sagt, Sie seien kein Nutzungsprüfer | Ihrem Konto fehlt die Rolle `usage_auditor`. Superuser zu sein genügt nicht: Ein Administrator kreuzt sie unter **Zugriffskontrolle → Benutzer**, **Rollen bearbeiten** an. |
| Die personenbezogene Auswertung verlangt einen Grund | Geben Sie in 10 bis 500 Zeichen an, warum Sie hineinsehen. Der Grund wird festgehalten, und die Person sieht ihn. |
| Das Speichern von **Personenbezogene Auswertung** verlangt den Root-Modus | Starten Sie den **Root-Modus** in der oberen Leiste und speichern Sie erneut. |
| Eine Einstellung wird als außerhalb des Bereichs abgelehnt | Die Bereiche stehen unter [Die Einstellungen](#the-settings): Die Mindestgruppengröße kann nicht unter 5 gehen, eine Aufbewahrungsdauer nicht unter ihren kleinsten Wert. |
| **Nutzung** zeigt fast nichts | Im Zeitraum haben wenige Personen die Instanz genutzt, oder die Mindestgruppengröße ist hoch: siehe [Die Mindestgruppengröße](usage.md#the-minimum-group-size). |
