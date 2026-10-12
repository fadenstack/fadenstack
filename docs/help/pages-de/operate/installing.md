# Den Server installieren {#installing-the-server}

Sie installieren Fadenstack auf einem Linux-Server mit dessen Kommandozeilenwerkzeug `faden`. Ein einziger Befehl
richtet die Container, HTTPS, den ersten Administrator und eine nächtliche Sicherung ein; danach melden Sie sich
im Browser an der Konsole an.

## Bevor Sie beginnen {#before-you-start}

- Ein Linux-Server, der die [Systemanforderungen](requirements.md) erfüllt: x86_64, Docker Engine 24 oder neuer
  mit Compose v2, Python 3.12 oder neuer mit `pipx` oder `uv`.
- Ihr Benutzer kann Docker ohne `sudo` ausführen (er ist in der Gruppe `docker`).
- Die Ports 80 und 443 sind frei, oder Sie wissen, welche anderen Ports Sie verwenden.
- Der Server erreicht das Internet, um seine Images herunterzuladen.
- Optional: ein DNS-Name für den Server, z. B. `ai.example.internal`, und ein Zertifikat dafür, wenn Sie ein
  eigenes verwenden wollen (siehe [HTTPS und Vertrauen](https.md)).

Führen Sie nach der Installation des Werkzeugs `faden doctor` aus, um zuerst den Host zu prüfen.

## Installieren {#install}

1. Installieren Sie das Kommandozeilenwerkzeug:

    ```bash
    pipx install fadenstack        # oder: uv tool install fadenstack
    ```

2. Führen Sie die Installation aus:

    ```bash
    faden deploy
    ```

3. Beantworten Sie die Fragen (siehe unten). Zum Schluss gibt der Befehl die Adressen aus, unter denen die
   Konsole erreichbar ist, und bei einem Zertifikat, das er selbst erstellt hat, die Datei, der Sie vertrauen
   müssen.

??? info "Was `faden deploy` tut"
    `faden deploy` prüft Docker, schreibt die Installation nach `~/fadenstack` (oder in das Verzeichnis, das Sie
    angeben: `faden deploy /opt/faden`), erzeugt eine `.env` mit zufälligen Passwörtern und Schlüsseln, bemisst die
    Dienste für den Host, lädt die Images genau des Release herunter, zu dem das Werkzeug gehört, startet alles und
    legt den ersten Administrator an. Grafana, RabbitMQ
    und Langfuse behalten je ein eigenes, zufälliges Passwort in der `.env`: Keines davon ist das Konsolen-Passwort
    des Administrators. Der erste Benutzer von Langfuse ist die E-Mail-Adresse des Administrators mit
    `LANGFUSE_INIT_USER_PASSWORD`; eine vor diesem Release angelegte Installation hat an diesen drei Stellen das
    Konsolen-Passwort, mit dem sie eingerichtet wurde, und die `.env` enthält, womit jeder Dienst läuft.

### Was der Befehl fragt {#what-it-asks}

| Frage | Was Sie antworten |
|---|---|
| **HTTPS** | `generate` (Standard): ein auf diesem Server erstelltes Zertifikat für seine Namen und Adressen. `own`: Ihr Zertifikat und Ihr Schlüssel als PEM-Dateien. `off`: einfaches HTTP. |
| Other names or addresses | Bei `generate`: Namen, unter denen die Konsole erreicht wird und die der Server selbst nicht kennt, z. B. `ai.example.internal`. Durch Kommas getrennt, leer für keine. |
| A port to use instead | Nur wenn Port 80 oder 443 von etwas anderem belegt ist. |
| Check for new releases? | Ja (Standard): Einmal am Tag lesen der Server und `faden` die öffentliche Liste der Releases und melden den Administratoren, wenn eines erschienen ist. Über den Server wird nichts gesendet. Siehe [Upgrade](upgrading.md#how-you-hear-about-a-new-release). |
| Admin email | Die E-Mail-Adresse, mit der sich der erste Administrator anmeldet. |
| Admin password | Mindestens 8 Zeichen, und nicht die E-Mail-Adresse. Leer lassen, um ein Passwort erzeugen zu lassen. |
| Save to file? | Speichert die Zugangsdaten in `~/fadenstack/.bootstrap-credentials` (nur für Sie lesbar). |
| Back up every night? | Ja (Standard) führt `faden backup` um 03:00 Uhr aus und behält die letzten 7. Siehe [Sicherungen](backups.md). |

Die Zugangsdaten werden nur einmal angezeigt. Bewahren Sie sie in einem Passwort-Manager auf.

### Ohne Rückfragen {#without-questions}

`faden deploy -y` fragt nichts: HTTPS mit einem hier erstellten Zertifikat, der Administrator `admin@localhost`
mit einem erzeugten Passwort, gespeichert in `~/fadenstack/.bootstrap-credentials`, die nächtliche Sicherung und
die Update-Prüfung eingeschaltet. Bewahren Sie die Zugangsdaten an einem sicheren Ort auf und löschen Sie dann diese Datei.

Die Auswahl lässt sich auch vorab angeben:

```bash
faden deploy --tls generate --tls-name ai.example.internal
faden deploy --tls own --tls-cert fullchain.pem --tls-key key.pem
faden deploy --tls off
faden deploy --http-port 8080 --https-port 8443
faden deploy --runtime-images x86_64
```

`--runtime-images` legt fest, welche Runtime-Images der Server für die Maschinen vorhält: `all` (Standard),
`none` oder `x86_64`, `aarch64` (NVIDIA DGX Spark), durch Kommas getrennt. Die Auswahl bleibt für Upgrades
gespeichert.

### Die Runtime-Images {#the-runtime-images}

Zum Schluss lädt `faden deploy` die Runtime-Images herunter: die Container, in denen die GPU-Maschinen Modelle
ausführen. Die Maschinen laden sie von diesem Server, nicht aus dem Internet, deshalb hält der Server eines pro
Maschinenart vor. Das ist der größte Download der Installation. Schlägt er fehl, laufen die Dienste bereits;
wiederholen Sie ihn mit:

```bash
faden runtime-images            # holt, was dieses Release braucht
faden runtime-images --check    # was der Server vorhält und was fehlt
```

## Zum ersten Mal anmelden {#sign-in-for-the-first-time}

1. Öffnen Sie die Adresse, die `faden deploy` ausgegeben hat, z. B. `https://ai.example.internal`.
2. Bei einem auf dem Server erstellten Zertifikat warnt der Browser, dass er den Aussteller nicht kennt.
   Importieren Sie `~/fadenstack/tls/ca.crt` in den Browser (oder in das Betriebssystem) als vertrauenswürdige
   Zertifizierungsstelle und laden Sie die Seite neu. Siehe
   [HTTPS und Vertrauen](https.md#trusting-a-certificate-made-on-the-server).
3. Geben Sie auf der Seite **Bei Fadenstack anmelden** die **E-Mail** und das **Passwort** des Administrators ein
   und wählen Sie **Anmelden**.

![Die Liste „Erste Schritte“ auf dem Dashboard, hier mit vier von fünf Schritten abgehakt](../images/operate/getting-started.png)

Die Konsole öffnet sich mit dem **Dashboard** und der Liste **Erste Schritte**: eine Maschine hinzufügen, einen
Cluster erstellen, ein Modell bereitstellen, es im Chat ausprobieren, Personen hinzufügen. Jeder Schritt hakt sich
selbst ab, sobald er erledigt ist.

Als Nächstes: [die GPU-Maschinen hinzufügen](machines.md).

## Wo was liegt {#where-things-are}

| Pfad | Was es ist |
|---|---|
| `~/fadenstack/` | Die Installation: Compose-Datei, `.env`, Konfiguration der Dienste |
| `~/fadenstack/.env` | Alle Passwörter, Schlüssel und Einstellungen, nur für Sie lesbar. Halten Sie die Datei vertraulich und bewahren Sie eine Kopie bei Ihren Sicherungen auf |
| `~/fadenstack/tls/`, `~/fadenstack/tls-ca/` | Das ausgelieferte Zertifikat und die eigene Zertifizierungsstelle des Servers mit ihrem Schlüssel |
| `~/fadenstack/backups/` | Sicherungen |
| `~/fadenstack/logs/` | Das Log der täglichen Zertifikatserneuerung (die nächtliche Sicherung schreibt nach `~/fadenstack/backups/backup.log`) |
| `~/.config/faden/faden.yaml` | Die Einstellungen des Werkzeugs selbst: wo die Installation liegt, welche Module eingeschaltet sind |

Die Daten liegen in Docker-Volumes namens `fadenstack_*`, z. B. `fadenstack_pg_data`.

### Wo die Firmendaten liegen {#where-the-company-data-is-kept}

| Daten | Wo | In einer Sicherung |
|---|---|---|
| Datenbanken (Benutzer, Chats, Einstellungen) | `fadenstack_pg_data` | Als Dumps |
| An Chats angehängte Dateien | `fadenstack_faden_chat_files` | Mit `--include-volumes` |
| In die Wissensdatenbank hochgeladene Dateien | `fadenstack_faden_rag_lite` | Mit `--include-volumes` |
| Modelle | `fadenstack_faden_models` | Nein |

Vor Release 0.4.4 hatten Chat-Anhänge kein Volume: Sie lagen im Gateway-Container und gingen verloren, wenn ein
Upgrade oder eine Wiederherstellung ihn ersetzte. `faden upgrade` kopiert die Dateien, die der alte Container
enthält, in das Volume, bevor es den Container ersetzt; beim ersten Upgrade geht also nichts verloren.

Um die Dateien Ihrer Firma in einem eigenen Verzeichnis zu halten, z. B. auf Speicher, den Ihre Firma verwaltet,
tragen Sie es in der `.env` ein:

```bash
FADEN_DATA_DIR=/srv/fadenstack
```

oder geben Sie es bei der Installation an: `faden deploy --data-dir /srv/fadenstack`. Verwenden Sie einen absoluten
Pfad in POSIX-Form (unter Windows `/c/data`). Leer, der Standard, behält die Docker-Volumes.

Was es abdeckt, steht fest und wächst in einem späteren Release nicht:

| Ordner | Enthält |
|---|---|
| `/srv/fadenstack/chat-files` | Die an Chats angehängten Dateien |
| `/srv/fadenstack/knowledge` | Die in die Wissensdatenbank hochgeladenen Dateien |

Nie abgedeckt sind die Datenbanken (`fadenstack_pg_data`), die Modelle (`fadenstack_faden_models`) und die
Beobachtungsdaten (Logs, Metriken, Traces, Grafana). Sie bleiben in Docker-Volumes und bekommen eigene Einstellungen,
falls sie je umziehen.

**Das Einschalten kopiert Ihre Dateien.** Findet `faden deploy`, `faden upgrade`, `faden rebuild` oder `faden up`
`FADEN_DATA_DIR` gesetzt und ein benanntes Volume, das noch Dateien enthält, die dem Ordner fehlen, kopiert es sie in
den Ordner, bevor es einen Container neu erstellt, und vergleicht dann jede Datei per SHA-256-Hash. Nichts wird
überschrieben: Eine Datei, die an beiden Orten mit unterschiedlichem Inhalt existiert, stoppt den Befehl und wird
genannt; es wurde nichts ersetzt. Scheitert eine Kopie oder ein Vergleich, stoppt der Befehl ebenso, und die alten
Dienste laufen weiter. Führen Sie ihn nach der Behebung der Ursache erneut aus. Das benannte Volume bleibt unverändert;
sobald Sie sicher sind, dass der Ordner alles enthält, können Sie es mit `docker volume rm` entfernen. Ein erneuter
Lauf kopiert nichts, was schon da ist.

**Das Ausschalten kopiert die Dateien nicht zurück.** Der Ordner behält sie, und die Container nutzen wieder die
(alten oder leeren) benannten Volumes. Um zurückzugehen, kopieren Sie die Dateien zuvor selbst in die Volumes.

### Docker-Logs {#docker-logs}

Das Docker-Log jedes Containers rotiert: höchstens 5 Dateien zu je 50 MB pro Container, die älteste wird verworfen.
Ohne Grenze füllen die Logs die Festplatte. Ändern Sie die Grenze in der `.env` oder mit
`faden deploy --log-max-size 100m --log-max-file 10`:

```bash
FADEN_LOG_MAX_SIZE=50m
FADEN_LOG_MAX_FILE=5
```

Führen Sie danach `faden upgrade` oder `faden rebuild` aus, damit die Container damit neu erstellt werden. Die Logs,
die Sie in Grafana lesen, liegen in Loki und werden nicht wegrotiert: Alloy liest das Log jedes Containers über
Docker und liest über eine Rotation hinweg weiter. Wählen Sie die Grenzen großzügig, denn Docker behält nur, was
hineinpasst, und Alloy muss laufen, um eine Zeile zu sammeln.

### Redis-Speicher {#redis-memory}

Redis hält kurzlebige Daten: die laufenden Ereignisse von Chat-Antworten, Zähler für Ratenbegrenzungen, die
Anwesenheit von Geräten und, bei eingeschaltetem Tracing, die Job-Warteschlangen von Langfuse. Es ist auf 1 GB
begrenzt, damit ein Fehler nicht den Speicher des Servers füllt. An der Grenze verwirft Redis keine Daten, sondern
lehnt neue Schreibvorgänge ab: Das Gateway arbeitet dann ohne die gemeinsame Kopie der Ereignisse einer Antwort weiter
(eine Seite, die mitten in einer Antwort zurückkommt, findet sie vielleicht nicht), und eine Anfrage mit gesetzter
Ratenbegrenzung kann fehlschlagen, bis wieder Speicher frei ist. Ändern Sie die Grenze in der `.env` und führen Sie
danach `faden upgrade` oder `faden rebuild` aus:

```bash
FADEN_REDIS_MAXMEMORY=1gb
```

Einstellungen, die Sie ändern, gehören in die `.env`. Die anderen Dateien der Installation, z. B.
`nginx/nginx.conf`, werden beim nächsten Upgrade ersetzt.

## Geheimnisse in der .env {#secrets-in-env}

`faden deploy` schreibt jedes Passwort, jeden Schlüssel und jedes Token, das die Dienste brauchen, in
`~/fadenstack/.env`, jeweils zufällig und nur für diese Installation; `faden upgrade` ergänzt die, die ein neues
Release braucht. Die Compose-Datei hat für keines davon einen Ersatzwert: Fehlt eines, bricht `docker compose` ab
und nennt es („required variable ... is missing a value“).

Starten Sie stattdessen von Hand mit `docker compose`, setzen Sie sie selbst, jedes auf einen eigenen langen
Zufallswert (`openssl rand -base64 48`):

| Variable | Was sie schützt |
|---|---|
| `POSTGRES_PASSWORD` | Die Datenbank-Anmeldung aller Dienste |
| `FADEN_BACKEND_SETTINGS_MASTER_KEY` | Die Geheimnisse, die die Konsole speichert |
| `FADEN_BACKEND_CREDENTIAL_PEPPER` | Die Zugangsdaten der Maschinen. Ein eigener Wert, den `faden secrets rotate` nie ändert, sodass die Maschinen verbunden bleiben |
| `FADEN_MCP_ENCRYPTION_KEY` | Die Zugangsdaten der MCP-Server |
| `FADEN_GATEWAY_MCP_SERVICE_TOKEN`, `FADEN_BACKEND_MCP_SERVICE_TOKEN`, `FADEN_MCP_SERVICE_TOKEN` | Aufrufe des MCP-Dienstes. Ein Wert für alle drei |
| `FADEN_GATEWAY_SKILLS_SERVICE_TOKEN`, `FADEN_BACKEND_SKILLS_SERVICE_TOKEN`, `FADEN_SKILLS_SERVICE_TOKEN` | Aufrufe des Skills-Dienstes. Ein Wert für alle drei |
| `FADEN_GATEWAY_PII_SERVICE_TOKEN`, `FADEN_BACKEND_PII_SERVICE_TOKEN`, `FADEN_MCP_PII_SERVICE_TOKEN`, `FADEN_PII_SERVICE_TOKEN` | Aufrufe des PII-Dienstes, der jeden Aufruf ohne ihn ablehnt (nur sein Health-Check ist offen). Ein Wert für alle vier |
| `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` (64 Hex-Zeichen: `openssl rand -hex 32`), `LANGFUSE_NEXTAUTH_SECRET` | Die API-Schlüssel, gespeicherten Schlüssel und Anmeldungen von Langfuse |
| `REDIS_AUTH`, `MINIO_ROOT_PASSWORD`, `CLICKHOUSE_PASSWORD`, `GRAFANA_ADMIN_PASSWORD` | Die Speicher und Grafana. Grafana liest sein Passwort beim ersten Start; eine spätere Änderung in der `.env` ändert es in Grafana nicht |
| `LANGFUSE_INIT_USER_PASSWORD` | Das Passwort des ersten Benutzers von Langfuse (die E-Mail-Adresse des Administrators). Langfuse liest es nur einmal, wenn es den Benutzer anlegt: Ändern Sie es danach in Langfuse und tragen Sie das neue in die `.env` ein |
| `RABBITMQ_ADMIN_PASS`, `RABBITMQ_BACKEND_PASS`, `RABBITMQ_API_PASS`, `RABBITMQ_PII_PASS` | Den Message-Broker. Seine Benutzer stammen aus `rabbitmq/definitions.json`, die `faden deploy` daraus schreibt |

Außerhalb der Entwicklung starten das Backend, das Gateway sowie der MCP- und der Skills-Dienst nicht mit den
Entwicklungswerten, die mit dem Quellcode veröffentlicht sind, und sagen, was zu setzen ist.

### Eine von Hand gestartete Installation vor Release 0.4.2 {#an-install-started-by-hand-before-release-042}

Bis dahin griff die Compose-Datei auf feste Werte zurück, wenn die `.env` keinen hatte: für jede Installation
dieselben, veröffentlicht mit dem Quellcode. Die meisten können Sie einfach auf neue Zufallswerte setzen. Diese
hier versiegeln gespeicherte Daten, die nur mit dem Wert lesbar sind, unter dem sie gespeichert wurden: Setzen Sie
sie auf den alten Wert.

| Variable | Der alte Wert |
|---|---|
| `POSTGRES_PASSWORD` | `postgres`, oder ein neuer Wert, den Sie auch in Postgres setzen: `docker exec -i faden-postgres psql -U postgres -c "ALTER USER postgres PASSWORD '<neuer Wert>'"` |
| `FADEN_BACKEND_SETTINGS_MASTER_KEY` | `dev-settings-master-key-change-me` |
| `FADEN_BACKEND_CREDENTIAL_PEPPER` | Der Wert von `FADEN_BACKEND_SETTINGS_MASTER_KEY`, damit die Zugangsdaten der Maschinen gültig bleiben (`faden upgrade` erledigt das selbst) |
| `FADEN_MCP_ENCRYPTION_KEY` | `dev-mcp-encryption-key-change-me-32b`, wenn MCP eingeschaltet war; sonst ein neuer Zufallswert |
| `LANGFUSE_SALT` | `mysalt` |
| `LANGFUSE_ENCRYPTION_KEY` | 64 Nullen |

Mit dem alten Hauptschlüssel für Einstellungen oder dem alten MCP-Schlüssel setzen Sie zusätzlich
`FADEN_ALLOW_PUBLISHED_KEYS=true`. Backend, Gateway und MCP starten dann damit und warnen bei jedem Start; ohne
diese Einstellung verweigern sie den Start. `faden upgrade` tut das von selbst für eine Installation, deren `.env`
einer dieser Schlüssel fehlt.

Um einen solchen Schlüssel zu ersetzen, führen Sie `faden secrets rotate` aus (siehe [Schlüssel erneuern](rotating-keys.md)):
Es verschlüsselt, was unter dem alten Schlüssel gespeichert ist, mit einem neuen neu, sodass nichts neu eingegeben
werden muss, und die Maschinen bleiben verbunden.

## Optionale Module {#optional-modules}

Drei Module lassen sich zu den Kerndiensten hinzufügen: `pii` (findet und maskiert personenbezogene Daten), `mcp`
(Tool-Server für Chats und Agenten) und `skills` (gespeicherte Anweisungen). Sie brauchen mehr Arbeitsspeicher
(siehe [Systemanforderungen](requirements.md)).

```bash
faden module list
faden module enable pii mcp skills
faden up
```

Mit `faden module disable pii` und anschließend `faden down` und `faden up` entfernen Sie ein Modul wieder.

## Befehle für den Alltag {#everyday-commands}

| Befehl | Was er tut |
|---|---|
| `faden status` | Alle Dienste: ob sie laufen und – wo es eine Prüfung gibt – ob sie gesund sind |
| `faden logs -f faden-backend` | Das Log eines Dienstes mitverfolgen (ohne Namen: alle Dienste) |
| `faden up` / `faden down` | Die Dienste starten oder stoppen; die Daten bleiben erhalten |
| `faden doctor` | Host, Ports und Zertifikat prüfen |
| `faden config show` | Wo die Installation liegt und wie das Werkzeug eingerichtet ist |
| `faden admin reset-password --email alex@example.com` | Ein neues Passwort für einen Benutzer setzen, der sich nicht anmelden kann. Es ändert nur die Anmeldung an der Konsole, nicht Grafana, Langfuse oder RabbitMQ |
| `faden --help` | Alle Befehle; `faden <command> --help` für die Optionen eines Befehls |

## Fadenstack entfernen {#removing-fadenstack}

`faden down` stoppt und entfernt die Container und behält die Daten-Volumes, sodass `faden up` alles zurückbringt.

!!! warning
    `faden down --volumes` löscht auch die Daten-Volumes: die Datenbank, alle Chats, den Modellspeicher. Das lässt
    sich nicht rückgängig machen. Legen Sie vorher eine Sicherung an und kopieren Sie sie vom Server weg.

## Wenn es nicht funktioniert {#when-it-does-not-work}

| Sie sehen | Was Sie tun |
|---|---|
| **`Docker is not available`** oder **`Docker daemon is not running`** | Installieren Sie Docker Engine mit dem Compose-Plugin, starten Sie es und stellen Sie sicher, dass Ihr Benutzer in der Gruppe `docker` ist (melden Sie sich nach dem Hinzufügen ab und wieder an). |
| **`HTTP port 80 is in use by something else.`** | Ein anderes Programm belegt den Port. Geben Sie ihn frei oder führen Sie `faden deploy --http-port 8080 --https-port 8443` aus. `faden doctor` listet alle Ports auf, die die Installation nach außen öffnet, und ob etwas anderes sie gerade belegt. |
| **`Backend did not become healthy in time — skipping admin setup.`** | Die Dienste sind gestartet, aber die Konsolen-API hat nicht rechtzeitig geantwortet. Prüfen Sie `faden status` und `faden logs faden-backend`, beheben Sie, was dort zu sehen ist, und führen Sie dann `faden deploy` erneut aus: Es behält die `.env` und legt den Administrator an. |
| **`The backend refused to create the administrator`** | Das Backend legt den ersten Administrator nur für den an, der das Bootstrap-Token der Installation mitschickt: `FADEN_BACKEND_BOOTSTRAP_TOKEN` in `~/fadenstack/.env`. `faden deploy` schreibt eines, wenn die `.env` keines hat. Einer Installation, die auf andere Weise gestartet wurde, kann es fehlen, und ein Backend, das vor dem Eintrag gestartet wurde, kennt es noch nicht: Führen Sie `faden up` und danach erneut `faden deploy` aus. |
| **`Some images failed to pull.`** | Der Server konnte eine Registry nicht erreichen. Prüfen Sie seinen Internetzugang und Proxy und führen Sie dann `faden deploy` erneut aus. |
| Der Browser lehnt das Zertifikat ab | Die Zertifizierungsstelle des Servers ist auf diesem Computer noch nicht als vertrauenswürdig eingetragen. Importieren Sie `~/fadenstack/tls/ca.crt`, oder verwenden Sie ein Zertifikat Ihres eigenen Ausstellers (siehe [HTTPS und Vertrauen](https.md)). |
| **`required variable ... is missing a value`** von `docker compose` | Der `.env` fehlt ein Passwort, Schlüssel oder Token, das die Compose-Datei verlangt. `faden upgrade` ergänzt sie; für eine von Hand gestartete Installation siehe [Geheimnisse in der .env](#secrets-in-env). |
| Ein Dienst bricht beim Start ab mit **`... is the development default published with Fadenstack's source code`** | Backend, Gateway, MCP oder der Skills-Dienst hat einen veröffentlichten Entwicklungswert gefunden. Die Meldung sagt, was zu setzen ist; [Geheimnisse in der .env](#secrets-in-env) erklärt die Einzelheiten, auch für eine Installation, die Daten darunter gespeichert hat. |
| Die Konsole meldet **„Dieser Server verwendet noch den Standard-Hauptschlüssel für Einstellungen“** | Die Installation läuft mit dem veröffentlichten Entwicklungsschlüssel, behalten mit `FADEN_ALLOW_PUBLISHED_KEYS=true`, weil ihre gespeicherten Geheimnisse damit versiegelt sind. Was ein Wechsel bedeutet, steht unter [Geheimnisse in der .env](#an-install-started-by-hand-before-release-042). |
