# Wissen {#knowledge}

Ein Modell weiß, was es im Training gelernt hat, nicht aber, was in den Dokumenten Ihrer Organisation steht. Diese Seite zeigt die zwei Wege, ihm Ihre Dokumente zu geben: eine Datei an einen Chat anhängen oder es die Wissensdatenbank Ihrer Organisation durchsuchen lassen. Sie sagt, was das Modell in beiden Fällen erhält, und welche Grenzen gelten.

## Zwei Wege {#two-ways-in}

| | Dateien, die Sie anhängen | Die Wissensdatenbank |
|---|---|---|
| Wer Dokumente hinzufügt | Sie, in einem Chat | Ihr Administrator |
| Wer sie nutzen kann | Nur Sie, in diesem Chat | Alle, für die die Sammlung freigegeben ist: alle in Ihrer Organisation oder nur die Personen und Gruppen, die Ihr Administrator ausgewählt hat |
| Was das Modell erhält | Den Text der Datei oder das Bild | Passagen, die zur Frage passen, mit dem Namen des Dokuments |

## Eine Datei anhängen {#attach-a-file}

![Das Nachrichtenfeld: die Büroklammer neben Schraubenschlüssel und Puzzleteil, rechts die Auswahl „Modell“ und die Senden-Schaltfläche](../images/use/chat-composer.png)

1. Wählen Sie im Chat die Büroklammer neben dem Nachrichtenfeld.
2. Wählen Sie eine oder mehrere Dateien aus. Jede erscheint als Chip über dem Feld; das × entfernt sie.
3. Geben Sie Ihre Frage ein und senden Sie sie.

Das Modell erhält den Text der Datei zusammen mit Ihrer Frage. Die Datei bleibt beim Chat: Sie können in späteren Nachrichten weiter dazu fragen, ohne sie erneut anzuhängen.

??? info "Welche Dateien Sie anhängen können, und die Grenzen"
    Diese Dateitypen können Sie anhängen, sofern Ihr Administrator die Liste nicht geändert hat:

    | Art | Dateitypen |
    |---|---|
    | Dokumente | PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`) |
    | Text | `.txt`, `.md`, `.csv`, `.json`, `.html` |
    | Bilder | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` |

    Sofern Ihr Administrator keine anderen festgelegt hat: Eine Datei darf bis zu 10 MB groß sein, ein Chat darf bis zu 20 Dateien haben, und alle Ihre angehängten Dateien zusammen dürfen bis zu 50 MB belegen. Von einem langen Dokument werden die ersten 50 Seiten gelesen.

??? info "Bilder, Scans und lange Dateien"
    | | Gut zu wissen |
    |---|---|
    | **Bilder** | Sie helfen nur bei einem Modell, das Bilder lesen kann. Fragen Sie Ihren Administrator, welche Modelle das können. Ein Bild wird nur an ein Modell gesendet, das Bilder lesen kann: Bei einem Modell, das das nicht kann, weist sein Chip vor dem Senden darauf hin, und die Antwort sagt, dass das Bild nicht gesendet wurde. |
    | **Scans** (Dokumente, deren Seiten Bilder sind) | Sie enthalten möglicherweise keinen Text, den der Server lesen kann. Das Modell erhält die Datei dann nicht. Wenn eine Antwort Ihre Datei zu ignorieren scheint, kann das der Grund sein. |
    | **Lange Dateien** | Sie passen möglicherweise nicht vollständig in das, was das Modell auf einmal lesen kann. Das Modell durchsucht die Datei dann nach den Teilen, die es braucht, oder liest ihren Anfang. Im Tool-Modus **Aus** bekommt es keins von beiden. |

    Nach dem Senden listet der Chat die Dateien, die Sie angehängt haben, nicht auf.

## Fragen zu den Dokumenten Ihrer Organisation stellen {#ask-about-your-organisations-documents}

Ihr Administrator kann eine Wissensdatenbank mit Dokumenten für alle befüllen: Handbücher, Richtlinien, Produkthandbücher. Sie selbst fügen nichts hinzu.

Wenn Ihre Frage von diesen Dokumenten abhängen kann, durchsucht das Modell die Wissensdatenbank, liest die gefundenen Passagen und antwortet auf ihrer Grundlage. Es nennt das Dokument, aus dem eine Passage stammt. Damit es sicher nachsieht, sagen Sie es ausdrücklich, z. B. „Durchsuche unsere Dokumente: Wie viele Urlaubstage werden ins nächste Jahr übertragen?“

??? info "Was Sie erwarten können"
    - Die Suche findet Passagen, keine ganzen Dokumente. Das Modell kann rund um eine Passage weiterlesen, wenn es mehr vom Dokument braucht.
    - Die Antwort gibt wieder, wie das Modell diese Passagen versteht. Prüfen Sie wichtige Antworten anhand des Dokuments, das es nennt.
    - Nur Modelle, die Tools verwenden können, durchsuchen die Wissensdatenbank. Wenn ein Modell nie ein Dokument nennt, versuchen Sie ein anderes.
    - Mit dem Tool-Modus **Aus** (siehe [Tools im Chat](tools.md)) kann das Modell nicht suchen.
    - Die Suche schaut nicht in Ihren Chats, in den angehängten Dateien anderer Personen oder im Internet nach.

!!! note "Wer was sieht"
    Ihr Administrator legt fest, wer welche Sammlung der Wissensdatenbank lesen darf: alle oder nur bestimmte Personen und Gruppen. Ihre Suchen finden nur die Dokumente, die Sie lesen dürfen. Ihre eigenen Dateien gehören stattdessen in Ihren Chat.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **File type '…' is not allowed.** | Ihre Organisation nimmt diese Art von Datei nicht an. Speichern Sie sie als PDF oder Text, oder fragen Sie Ihren Administrator. |
| **File exceeds maximum size of … MB.** | Verkleinern Sie die Datei, oder hängen Sie nur den Teil an, den Sie brauchen. |
| **Maximum of … attachments per session reached.** | Beginnen Sie für weitere Dateien einen neuen Chat. |
| **Total attachment storage would exceed … MB.** | Ihre angehängten Dateien füllen den Ihnen zustehenden Speicherplatz. Das Löschen eines Chats gibt den Platz seiner Dateien frei, ebenso das Entfernen nicht mehr benötigter Dateien aus einem Chat. |
| Das Modell sagt, es habe nichts gefunden | Die Wissensdatenbank enthält möglicherweise kein Dokument zu dem Thema. Fragen Sie Ihren Administrator, was sie enthält. |
