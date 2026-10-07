# Vorhandene vLLM-Server {#existing-vllm-servers}

Auf vielen GPU-Maschinen läuft vLLM schon, bevor Fadenstack dazukommt: Container, die von Hand oder von einem anderen
Tool gestartet wurden. Fadenstack findet sie auf seinen Maschinen und lässt Sie jeden laufenden davon unter einem
Namen hinter das Gateway stellen, ohne den Container anzufassen.

## Bevor Sie beginnen {#before-you-start}

- Die Maschine, auf der der vLLM-Container läuft, ist zu Fadenstack hinzugefügt (siehe [GPU-Maschinen](machines.md))
  und hat einen aktuellen Agent.
- Der Container veröffentlicht seinen Port auf der Maschine (z. B. `-p 8102:8000`).

## Was gefunden wird {#what-is-found}

Jede Maschine meldet etwa einmal pro Minute die vLLM-Container, die Fadenstack nicht gestartet hat, laufende wie
gestoppte. Ihre Seite listet sie unter **vLLM auf dieser Maschine** auf:

| Spalte | Was sie zeigt |
|---|---|
| **Container** | Name und Image des Containers und das Tool, das ihn gestartet hat, aus seinen Labels: „Gestartet von …“ oder ein Docker-Compose-Projekt |
| **Modell** | Das Modell, wofür es dient (**Chat**, **Embeddings** oder **Bewertung (Rerank)**), und der Name, unter dem es antwortet |
| **Zustand** | Läuft oder gestoppt |
| **Port** | Der Port, unter dem er auf der Maschine veröffentlicht ist |
| **Routing** | Ob er über Fadenstack geroutet wird und unter welchem Namen |

![vLLM auf dieser Maschine, auf der Seite einer Maschine: ein gestoppter vLLM-Container, bei dem „Über Fadenstack routen“ deaktiviert ist, weil er nicht läuft](../images/operate/machine-vllm-found.png)

Wofür das Modell dient, ergibt sich aus den vLLM-Flags des Containers. Sagen die Flags nichts dazu, wird es aus dem
Namen des Modells geschätzt und als „aus dem Modellnamen geschätzt“ gekennzeichnet.

Die Suche ist rein lesend. Der Agent liest die Containerliste und die Konfiguration jedes Containers, sonst nichts.
Zwei Dinge verlassen die Maschine nie: die Umgebungsvariablen des Containers, in denen meist Tokens stehen, und
geheime Werte auf seiner Befehlszeile (`--api-key` und jedes Flag, dessen Name key, token, secret oder password
enthält, werden als `***` gemeldet).

Container, die Fadenstack selbst gestartet hat, sind hier nicht aufgeführt; sie stehen auf den Seiten
**Deployments** und **Provider**.

## Einen Container über Fadenstack routen {#route-one-through-fadenstack}

1. Öffnen Sie **Maschinen**, wählen Sie die Maschine und suchen Sie den Container unter **vLLM auf dieser Maschine**.
2. Wählen Sie **Über Fadenstack routen**.
3. Geben Sie unter **Name für Clients** einen Namen ein. Vorgeschlagen wird der Name, unter dem der Container
   antwortet.
4. Wählen Sie im Dialog **Über Fadenstack routen**.

Anfragen für diesen Namen gehen nun an den Container, so wie er läuft. Er erscheint auf der Seite **Provider**, mit
einem Link zur Seite der Maschine, auf der Sie ihn verwalten. Der Chat bietet ihn an, wenn es ein Chat-Modell ist;
ein Embeddings-Modell antwortet unter `/v1/embeddings`, ein Bewertungsmodell unter `/v1/rerank`. Eine Anfrage der
falschen Art wird abgelehnt, mit einer Meldung, die sagt, wohin sie gehört. Ein gerouteter Container zählt als
externer Provider, und Core nimmt davon höchstens drei auf (siehe [Externe Provider](../administer/providers.md)).

Auf der Maschine ändert sich nichts: weder der Container noch seine Einstellungen oder seine Neustart-Richtlinie. Wer
ihn gestartet hat, startet und stoppt ihn weiterhin.

**Routing beenden** nimmt den Namen wieder weg; das Gateway antwortet dann, dass es das Modell nicht gibt. Der
Container läuft weiter.

### Wenn der Container stoppt {#when-the-container-stops}

Ein gerouteter Container, der stoppt, wird innerhalb von etwa einer Minute aus dem Routing genommen („Nicht geroutet,
solange der Container … ist“) und wieder aufgenommen, sobald er wieder läuft. Anfragen für seinen Namen schlagen in
der Zwischenzeit fehl.

## Was nicht geroutet werden kann {#what-cannot-be-routed}

**Über Fadenstack routen** ist deaktiviert, mit dem Grund darunter, für einen Container, bei dem Folgendes zutrifft:

| Angezeigter Grund | Was zu tun ist |
|---|---|
| It is not running. | Starten Sie ihn so, wie er normalerweise gestartet wird |
| Its port is not published on the machine, so nothing outside the machine can reach it. | Veröffentlichen Sie seinen Port, wenn er gestartet wird |
| It asks for an API key, which routing a found container does not handle yet. | Starten Sie ihn ohne `--api-key`, oder stellen Sie das Modell stattdessen auf einem Cluster bereit |
| No answer from … | Fadenstack hat ihn nach seinen Modellen gefragt und keine Antwort bekommen: Er lädt noch, oder eine Firewall ist im Weg |

## Sicherheit {#security}

Das Gateway erreicht einen gerouteten Container über gegenseitiges TLS, über denselben Proxy auf der Maschine, der
auch die Modelle schützt, die Fadenstack ausführt, und zwar auf dem Port des Containers plus 20000 (aus 8102 wird
28102). Erlauben Sie dem Server, diesen Port zu erreichen, und auch den eigenen Port des Containers: Dort fragt der
Server den Container, welche Modelle er bereitstellt, bevor er ihn routet und wenn die Konsole ihn auflistet.

!!! warning
    Der eigene Port des Containers bleibt offen: Fadenstack hat den Container nicht gestartet und schließt ihn nicht.
    vLLM betreibt diesen Port oft ganz ohne Authentifizierung. Schützen Sie ihn mit einer Firewall, sodass nur die
    Maschine selbst und der Fadenstack-Server ihn erreichen, oder stellen Sie das Modell stattdessen auf einem
    Cluster bereit.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**„Diese Maschine hat ihre Container nicht gemeldet.“** Ihr Agent ist zu alt, um nach vLLM zu suchen. Führen Sie die
Installationszeile erneut auf ihr aus, um ihn zu aktualisieren.

**„Keine vLLM-Container gefunden.“** Die Maschine meldet etwa einmal pro Minute, was sie ausführt; warten Sie eine
Minute. Nur Container, in denen vLLM läuft, werden aufgeführt.
