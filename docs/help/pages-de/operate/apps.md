# Fadenstack-Apps {#fadenstack-apps}

Fadenstack hat eigene Apps: den Desktop-Agenten, die Office-Add-ins, die Nextcloud-App und die Chrome-Erweiterung. Der Server sagt den Administratoren, welche Versionen davon im Einsatz sind, wann eine neuere erschienen ist oder eine alte ersetzt werden muss, und kann sie allen auf der Seite **Apps** anbieten.

## Die App-Liste {#the-app-list}

Einmal am Tag liest der Server eine zweite öffentliche, signierte Datei neben der Liste der Releases: `https://fadenstack.com/updates/v1/apps.json` mit der Signatur `apps.json.sig`. Sie wird zusammen mit der Liste der Releases gelesen (siehe [Wie Sie von einem neuen Release erfahren](upgrading.md#how-you-hear-about-a-new-release)) und auf dieselbe Weise: Die Datei wird wie jede andere abgerufen, über diesen Server und seine Apps wird nichts gesendet, und alles wird auf dem Server verglichen.

- **Mit einem Spiegel** (**Einstellungen**, **Updates**, **Release list address**) wird die App-Liste aus demselben Ordner gelesen wie die gespiegelte `index.json`.
- **Der Schalter** **Check for new releases** (`updates.check_enabled`) gilt für beide Listen.
- **Ohne Internetzugang** siehe [Ein Server ohne Internetzugang](#a-server-without-internet-access).

## Apps im Einsatz {#apps-in-use}

Jede App meldet dem Server beim Start ihren Namen und ihre Version. Der Server zählt die Versionen, die er in den letzten 30 Tagen gesehen hat. Namen von Personen werden nicht angezeigt.

## Der Bereich Apps auf Updates {#the-apps-section-on-updates}

Öffnen Sie in der Konsole **Updates** und sehen Sie unter **Apps** nach. Für jede App zeigt er:

- das neueste Release;
- die hier im Einsatz befindlichen Versionen, ältere sind markiert;
- das Ende des Supports der App;
- ob ein Release ein neueres Fadenstack braucht, als dieser Server ausführt;
- die Versionshinweise und Download-Links mit ihren SHA-256-Prüfsummen;
- den Schalter **Allen anbieten** (siehe [Eine App allen anbieten](#offer-an-app-to-people));
- die [Verbindungsdateien](#connection-files).

## Hinweise {#notices}

Die App-Liste fügt **Handlungsbedarf** im Dashboard Einträge hinzu. Jeder lässt sich wie die anderen stummschalten, und wer ein Release stummschaltet, schaltet das nächste nicht mit stumm.

| Eintrag | Wann | Angezeigt |
|---|---|---|
| Ein Sicherheitsrelease einer App | Eine ältere Version der App ist hier im Einsatz | Als Warnung; als kritisch bei einem kritischen Release |
| Eine App-Version nach dem Ende ihres Supports | Diese Version ist hier im Einsatz | Immer, solange es die App-Liste gibt |
| Ein neues Release einer App | Die App ist hier im Einsatz | Nur mit **News about Fadenstack apps** |
| Eine neue App | Sie ist in den letzten 30 Tagen zum ersten Mal erschienen, und niemand hier nutzt sie oder bekommt sie angeboten | Nur mit **News about Fadenstack apps** |

**News about Fadenstack apps** (`updates.app_news`) steht unter **Einstellungen**, **Updates**. Der Schalter ist standardmäßig an. Die beiden Sicherheitseinträge hängen nicht davon ab.

### E-Mail {#e-mail}

Wenn der Server E-Mails senden kann, bekommen die Administratoren eine Nachricht für jedes Release einer App, das Sicherheitsprobleme einer im Einsatz befindlichen Version behebt. Nie für Neuigkeiten.

## Eine App allen anbieten {#offer-an-app-to-people}

Schalten Sie bei einer App **Allen anbieten** ein (gespeichert in der Einstellung `updates.apps_offered`). Alle, die angemeldet sind, sehen sie dann auf ihrer Seite **Apps**: den Download, die neueste Version, das Ende des Supports und wie sie mit diesem Server verbunden wird, samt der Adresse des Servers. Siehe [Apps](../use/apps.md). Sonst erreicht nichts aus der App-Liste Personen, die keine Administratoren sind.

## Downloads sind Links {#downloads-are-links}

Ein Download ist ein Link dorthin, wo die App veröffentlicht ist: ihr Release auf GitHub unter `github.com/fadenstack/`, der Chrome Web Store oder der Nextcloud-App-Store. Der Server lädt nichts herunter. Ein Klick auf einen Link sendet die Adresse (IP) der Person an diese Seite, wie jeder Link. Die Links senden keinen Referrer, sodass diese Seite die Adresse dieses Servers nicht erfährt.

## Verbindungsdateien {#connection-files}

Unter **Apps** auf **Updates** laden Sie Dateien herunter, die eine App auf diesen Server ausrichten. Keine davon enthält ein Passwort oder Token: Jede Person meldet sich weiterhin mit ihrem eigenen Konto an.

| App | Datei | Was Sie damit tun |
|---|---|---|
| Office-Add-ins | `policy.json` | Legen Sie sie auf jedem Computer unter `%PROGRAMDATA%\Fadenstack\Office\policy.json` ab, z. B. per Gruppenrichtlinie oder Intune. Sie enthält die Adresse dieses Servers und die vier Agenten (Excel, Word, PowerPoint, Outlook). |
| Nextcloud-App | `connect-nextcloud.sh` | Führen Sie ihre `occ`-Befehle auf dem Nextcloud-Server als Benutzer des Webservers aus. Sie aktivieren die App `fadenstack_agent` und setzen `server_url`, `agent` und `integration_enabled`. |
| Chrome-Erweiterung | `fadenstack-chrome-policy.json` | Eine Chrome-Richtlinie: Sie installiert die Erweiterung aus dem Chrome Web Store und setzt die verwalteten Einstellungen `serverUrl` und `agent` der Erweiterung. Unter Linux gehört die Datei nach `/etc/opt/chrome/policies/managed/`. Unter Windows und macOS setzen Sie dieselben Werte per Gruppenrichtlinie, Intune oder Konfigurationsprofil. |
| Desktop-Agent | keine | Die Person gibt beim ersten Start die Adresse des Servers ein und meldet sich an. |

??? info "Wenn dieser Server eine eigene Zertifizierungsstelle nutzt"
    Laden Sie für die Office-Add-ins zusätzlich `/install/ca.crt` vom Server herunter und legen Sie es auf jedem Computer unter `%PROGRAMDATA%\Fadenstack\Office\fadenstack-ca.crt` ab.

## Ein Server ohne Internetzugang {#a-server-without-internet-access}

Laden Sie `apps.json` und `apps.json.sig` von `https://fadenstack.com/updates/v1/` auf einem anderen Rechner herunter und laden Sie sie unter **Updates** bei **Kein Internet auf diesem Server?** hoch, wie bei der Liste der Releases (siehe [Ein Server ohne Internetzugang](upgrading.md#a-server-without-internet-access)). Oder kopieren Sie sie unverändert zusammen mit der Liste der Releases auf Ihren eigenen Webserver und tragen Sie dessen Adresse als **Release list address** ein.
