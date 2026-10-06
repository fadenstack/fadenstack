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

`faden deploy` prüft Docker, schreibt die Installation nach `~/fadenstack` (oder in das Verzeichnis, das Sie
angeben: `faden deploy /opt/faden`), erzeugt eine `.env` mit einem zufälligen Passwort für jeden Dienst, bemisst
die Dienste für den Host, lädt die Images genau des Release herunter, zu dem das Werkzeug gehört, startet alles
und legt den ersten Administrator an.

### Was der Befehl fragt {#what-it-asks}

| Frage | Was Sie antworten |
|---|---|
| **HTTPS** | `generate` (Standard): ein auf diesem Server erstelltes Zertifikat für seine Namen und Adressen. `own`: Ihr Zertifikat und Ihr Schlüssel als PEM-Dateien. `off`: einfaches HTTP. |
| Other names or addresses | Bei `generate`: Namen, unter denen die Konsole erreicht wird und die der Server selbst nicht kennt, z. B. `ai.example.internal`. Durch Kommas getrennt, leer für keine. |
| A port to use instead | Nur wenn Port 80 oder 443 von etwas anderem belegt ist. |
| Admin email | Die E-Mail-Adresse, mit der sich der erste Administrator anmeldet. |
| Admin password | Leer lassen, um ein Passwort erzeugen zu lassen. |
| Save to file? | Speichert die Zugangsdaten in `~/fadenstack/.bootstrap-credentials` (nur für Sie lesbar). |
| Back up every night? | Ja (Standard) führt `faden backup` um 03:00 Uhr aus und behält die letzten 7. Siehe [Sicherungen](backups.md). |

Die Zugangsdaten werden nur einmal angezeigt. Bewahren Sie sie in einem Passwort-Manager auf.

### Ohne Rückfragen {#without-questions}

`faden deploy -y` fragt nichts: HTTPS mit einem hier erstellten Zertifikat, der Administrator `admin@localhost`
mit einem erzeugten Passwort, gespeichert in `~/fadenstack/.bootstrap-credentials`, und die nächtliche Sicherung
eingeschaltet. Bewahren Sie die Zugangsdaten an einem sicheren Ort auf und löschen Sie dann diese Datei.

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

Die Konsole öffnet sich mit dem **Dashboard** und der Liste **Erste Schritte**: eine Maschine hinzufügen, einen
Cluster erstellen, ein Modell bereitstellen, es im Chat ausprobieren, Personen hinzufügen. Jeder Schritt hakt sich
selbst ab, sobald er erledigt ist.

![Die Liste „Erste Schritte“ auf dem Dashboard, hier mit vier von fünf Schritten abgehakt](../images/operate/getting-started.png)

Als Nächstes: [die GPU-Maschinen hinzufügen](machines.md).

## Wo was liegt {#where-things-are}

| Pfad | Was es ist |
|---|---|
| `~/fadenstack/` | Die Installation: Compose-Datei, `.env`, Konfiguration der Dienste |
| `~/fadenstack/.env` | Alle Passwörter, Schlüssel und Einstellungen. Halten Sie die Datei vertraulich und bewahren Sie eine Kopie bei Ihren Sicherungen auf |
| `~/fadenstack/tls/`, `~/fadenstack/tls-ca/` | Das ausgelieferte Zertifikat und die eigene Zertifizierungsstelle des Servers mit ihrem Schlüssel |
| `~/fadenstack/backups/` | Sicherungen |
| `~/fadenstack/logs/` | Logs der geplanten Jobs |
| `~/.config/faden/faden.yaml` | Die Einstellungen des Werkzeugs selbst: wo die Installation liegt, welche Module eingeschaltet sind |

Die Daten liegen in Docker-Volumes namens `fadenstack_*`, z. B. `fadenstack_pg_data`.

Einstellungen, die Sie ändern, gehören in die `.env`. Die anderen Dateien der Installation, z. B.
`nginx/nginx.conf`, werden beim nächsten Upgrade ersetzt.

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
| `faden admin reset-password --email alex@example.com` | Ein neues Passwort für einen Benutzer setzen, der sich nicht anmelden kann |
| `faden --help` | Alle Befehle; `faden <command> --help` für die Optionen eines Befehls |

## Fadenstack entfernen {#removing-fadenstack}

`faden down` stoppt und entfernt die Container und behält die Daten-Volumes, sodass `faden up` alles zurückbringt.

!!! warning
    `faden down --volumes` löscht auch die Daten-Volumes: die Datenbank, alle Chats, den Modellspeicher. Das lässt
    sich nicht rückgängig machen. Legen Sie vorher eine Sicherung an und kopieren Sie sie vom Server weg.

## Wenn es nicht funktioniert {#when-it-does-not-work}

**`Docker is not available` oder `Docker daemon is not running`.** Installieren Sie Docker Engine mit dem
Compose-Plugin, starten Sie es und stellen Sie sicher, dass Ihr Benutzer in der Gruppe `docker` ist (melden Sie
sich nach dem Hinzufügen ab und wieder an).

**`HTTP port 80 is in use by something else.`** Ein anderes Programm belegt den Port. Geben Sie ihn frei oder
führen Sie `faden deploy --http-port 8080 --https-port 8443` aus. `faden doctor` listet alle Ports auf, die die
Installation nach außen öffnet, und wer sie gerade belegt.

**`Backend did not become healthy in time — skipping admin setup.`** Die Dienste sind gestartet, aber die
Konsolen-API hat nicht rechtzeitig geantwortet. Prüfen Sie `faden status` und `faden logs faden-backend`, beheben
Sie, was dort zu sehen ist, und führen Sie dann `faden deploy` erneut aus: Es behält die `.env` und legt den
Administrator an.

**`Some images failed to pull.`** Der Server konnte eine Registry nicht erreichen. Prüfen Sie seinen
Internetzugang und Proxy und führen Sie dann `faden deploy` erneut aus.

**Der Browser lehnt das Zertifikat ab.** Die Zertifizierungsstelle des Servers ist auf diesem Computer noch nicht
als vertrauenswürdig eingetragen. Importieren Sie `~/fadenstack/tls/ca.crt`, oder verwenden Sie ein Zertifikat
Ihres eigenen Ausstellers (siehe [HTTPS und Vertrauen](https.md)).

**Die Konsole meldet „Dieser Server verwendet noch den Standard-Hauptschlüssel für Einstellungen“.** Der
Schlüssel, der gespeicherte Geheimnisse verschlüsselt (unter anderem das Hugging-Face-Token), ist noch der
Standardwert. `faden deploy` schreibt einen zufälligen in die `.env`; einer Installation, die auf andere Weise
gestartet wurde, kann er fehlen. Setzen Sie `FADEN_BACKEND_SETTINGS_MASTER_KEY` in `~/fadenstack/.env` auf einen
langen Zufallswert (z. B. die Ausgabe von `openssl rand -base64 48`) und führen Sie dann `faden up` aus.
Geheimnisse, die unter dem alten Schlüssel gespeichert wurden, müssen Sie erneut eingeben.
