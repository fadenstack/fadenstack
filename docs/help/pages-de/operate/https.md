# HTTPS und Vertrauen {#https-and-trust}

Was Fadenstack verschlüsselt, wie Browser, API-Clients und die GPU-Maschinen dem Server vertrauen lernen, und die
Befehle, mit denen Sie das Zertifikat verwalten. Eine neue Installation arbeitet von Anfang an mit HTTPS – mit
einem Zertifikat, das der Server für sich selbst erstellt, oder mit Ihrem eigenen.

## Was geschützt ist {#what-is-protected}

| Verbindung | Verschlüsselt | Hinweise |
|---|---|---|
| Browser zur Konsole und zu `/api` | Ja, sobald HTTPS an ist | Unverschlüsseltes HTTP leitet nur auf HTTPS um |
| API-Clients zum Gateway (`/v1`) | Ja, sobald HTTPS an ist | Eine Anfrage an den HTTP-Port wird umgeleitet, Methode und Inhalt bleiben erhalten |
| Agenten der GPU-Maschinen zum Server | Ja, sobald HTTPS an ist | Befehle, Registrierung, Downloads von Modellen und Runtime-Images, Übertragung von Logs. Ein Agent auf HTTPS fällt nie auf unverschlüsseltes HTTP zurück |
| Gateway zu den Modellen auf den Maschinen | Ja, immer | Gegenseitiges TLS (mutual TLS): Jede Maschine akzeptiert nur das Zertifikat des Gateways. Unabhängig von HTTPS auf dem Server |
| Zwischen den Maschinen eines Clusters (Ray) | Wenn pro Cluster eingeschaltet | Immer mit dem Token des Clusters authentifiziert; verschlüsselt, sobald sein Schalter **Verkehr zwischen den Maschinen** an ist |
| GPU-zu-GPU-Verkehr innerhalb eines Clusters | Nein | Er lässt sich nicht verschlüsseln. Beschränken Sie dieses Netzwerk auf die Maschinen des Clusters |
| Container auf dem Server untereinander | Nein | Das interne Netzwerk von Docker; es verlässt den Server nicht |

Jeder Befehl, den der Server an eine Maschine sendet, ist signiert, mit oder ohne HTTPS, und ein Agent lehnt einen
Befehl ohne gültige Signatur ab. Die Signatur verhindert, dass jemand anderes einer Maschine Befehle sendet; sie
verbirgt nichts.

!!! warning
    Lassen Sie HTTPS eingeschaltet. Ohne HTTPS gehen Anmeldungen, API-Schlüssel, Prompts und die Zugangsdaten der
    Maschinen im Klartext über das Netzwerk.

Auf dem Server sind nur die Ports 80 und 443 vom Netzwerk aus erreichbar. Grafana, Langfuse, Postgres, Redis,
RabbitMQ und die übrigen Datenspeicher lauschen nur auf der Loopback-Adresse des Servers. Siehe
[Netzwerk und Ports](network.md).

## Zwei Arten von Zertifikaten {#two-kinds-of-certificate}

**Ein auf dem Server erstelltes Zertifikat** (`generate`, die Voreinstellung). Das Tool erstellt eine
Zertifizierungsstelle für die Installation und signiert damit ein Zertifikat für die Namen und IPv4-Adressen des
Servers, dazu für Namen und Adressen, die Sie hinzufügen. Browsern und API-Clients muss einmal gesagt werden, dass sie dieser
Zertifizierungsstelle vertrauen. Die Maschinen lernen sie von selbst.

**Ihr eigenes Zertifikat** (`use`), von einem öffentlichen Aussteller oder von der Zertifizierungsstelle Ihres
Unternehmens. Ein öffentlicher Aussteller braucht nichts weiter. Bei einer Unternehmens-CA geben Sie auch deren
Stammzertifikat an, damit die Maschinen ihr vertrauen.

## HTTPS einschalten {#switch-https-on}

Bei einer neuen Installation hat `faden deploy` das bereits erledigt. Bei einer Installation, die unverschlüsseltes
HTTP verwendet:

```bash
faden tls generate --name ai.example.internal     # ein hier erstelltes Zertifikat
# oder
faden tls use fullchain.pem key.pem               # Ihr eigenes
```

Der Befehl prüft das Zertifikat, startet den Proxy neu (den einzigen Container, den er anfasst) und wartet, bis die
Konsole antwortet. Außerdem richtet er die tägliche Erneuerung ein. Verbundene Maschinen wechseln von selbst zu
HTTPS (siehe [Die Maschinen](#the-machines)).

### Einem auf dem Server erstellten Zertifikat vertrauen {#trusting-a-certificate-made-on-the-server}

Vertraut wird einer einzigen Datei: `~/fadenstack/tls/ca.crt`. Sie ist öffentlich; ihr privater Schlüssel bleibt in
`~/fadenstack/tls-ca/`, nur für Sie lesbar, und gelangt nie in einen Container.

- **Browser:** Importieren Sie `ca.crt` als vertrauenswürdige Zertifizierungsstelle, im Browser oder im
  Betriebssystem, auf den Computern, von denen aus die Konsole genutzt wird.
- **API-Clients:** `curl --cacert ca.crt https://ai.example.internal/v1/models`, oder `SSL_CERT_FILE=ca.crt` für
  Python-Programme.
- **Maschinen:** nichts zu tun.

Spätere Zertifikate (eine Erneuerung, eine neue Adresse) signiert dieselbe Zertifizierungsstelle, daher muss nichts
erneut als vertrauenswürdig eingerichtet werden. Die Zertifizierungsstelle selbst ist zehn Jahre gültig.

### Ihr eigenes Zertifikat {#your-own-certificate}

```bash
faden tls use fullchain.pem key.pem
faden tls use cert.pem key.pem --chain intermediates.pem
faden tls use cert.pem key.pem --ca company-root.pem
```

Bevor das Tool das Zertifikat installiert, prüft es, ob der Schlüssel zum Zertifikat gehört und keine Passphrase
hat und ob das Zertifikat heute gültig ist. Ein Zertifikat, das keinen der Namen und keine der Adressen des Servers
enthält, wird mit einer Warnung installiert, denn die Konsole kann auch unter einem Namen erreichbar sein, den nur
das DNS kennt.

Mit `--ca` erhalten die Maschinen das Stammzertifikat Ihres Unternehmens und vertrauen dem Zertifikat, ohne dass
jemand Dateien auf sie kopieren muss. Wenn die Maschinen einen neuen Aussteller lernen müssen, wartet das Tool vor
dem Umschalten etwa eine Minute, damit verbundene Maschinen ihn zuerst abrufen (`--no-wait` überspringt das).

Einen eingebauten ACME-Client gibt es nicht. Verwenden Sie certbot, acme.sh oder die Werkzeuge Ihres Unternehmens,
und geben Sie `faden tls use` die Dateien an, die dort an Ort und Stelle erneuert werden, z. B.
`/etc/letsencrypt/live/ai.example.internal/fullchain.pem` und `privkey.pem`.

## Die Befehle {#the-commands}

| Befehl | Was er tut |
|---|---|
| `faden tls status` | An oder aus, das Zertifikat, bis wann es gilt und wie es erneuert wird |
| `faden tls generate [--name NAME]` | Ein hier erstelltes Zertifikat; `--name` fügt einen Namen oder eine Adresse hinzu, mehrfach möglich |
| `faden tls use CERT KEY [--chain FILE] [--ca FILE]` | Ihr eigenes Zertifikat |
| `faden tls renew [--force]` | Erneuert, was fällig ist; ändert sonst nichts. `--force` erneuert sofort |
| `faden tls off` | Zurück zu unverschlüsseltem HTTP. Die Zertifizierungsstelle bleibt für das nächste Mal erhalten |
| `faden tls machine-ca [--replace]` | Die Zertifikate hinter der Verschlüsselung zwischen Gateway und Maschinen (siehe unten) |
| `faden ports [--http N] [--https N]` | Zeigt oder ändert die Ports, auf denen die Konsole, die API und `/v1` erreichbar sind |

`generate`, `use` und `off` nehmen `--no-restart`, um nur die Dateien zu schreiben; der Proxy übernimmt sie bei
seinem nächsten Start. Ein neues Zertifikat bei weiterhin eingeschaltetem HTTPS lädt den Proxy neu, statt ihn neu
zu starten, sodass offene Verbindungen, auch die der Maschinen, bestehen bleiben.

## Erneuerung und Warnungen {#renewal-and-warnings}

Sobald HTTPS eingeschaltet ist, läuft `faden tls renew` jeden Morgen (als systemd-Benutzer-Timer
`faden-tls-renew.timer` oder als Crontab-Zeile) und ändert nichts, solange nichts fällig ist:

- **Ein hier erstelltes Zertifikat** ist 825 Tage gültig. Es wird 30 Tage vor Ablauf neu ausgestellt, oder sobald
  der Server eine Adresse hat, die es nicht enthält – von derselben Zertifizierungsstelle.
- **Ihr eigenes Zertifikat** wird erneut aus den Dateien übernommen, die Sie `faden tls use` angegeben haben, sobald
  sie sich dort ändern. Erneuerte Dateien, die die Prüfungen nicht bestehen, werden nicht übernommen; das alte
  Zertifikat bleibt.
- **Die Zertifizierungsstelle** wird vor ihrem Ablauf ersetzt; die alte bleibt vertrauenswürdig, solange sie gültig
  ist.

Sie werden gewarnt, bevor ein Zertifikat abläuft: von `faden tls status`, von `faden doctor` und im Dashboard der
Konsole unter **Handlungsbedarf** („HTTPS-Zertifikat läuft in … Tagen ab“). Ein täglicher Lauf, der ein bald
ablaufendes Zertifikat nicht erneuern kann, endet mit einem Fehler, sodass `systemctl --user --failed` ihn anzeigt;
seine Ausgabe steht in `~/fadenstack/logs/tls-renew.log`.

## Die Maschinen {#the-machines}

**Sie folgen dem Server von selbst zu HTTPS.** Solange ein Agent verbunden ist, ruft er die Zertifizierungsstelle
des Servers ab, auch während HTTPS noch aus ist. Wird HTTPS eingeschaltet, wird ein Agent, der die HTTP-Adresse
verwendet, umgeleitet, prüft die HTTPS-Adresse gegen die Zertifizierungsstelle, die er hat, wechselt dorthin und
merkt sich die Adresse. Auf den Maschinen ist nichts zu tun; `faden-agent show` zeigt auf einer Maschine, wann sie
gewechselt hat.

**Zurück wechseln sie nie von selbst.** Unverschlüsseltes HTTP nach HTTPS wäre ein Downgrade, das jeder zwischen
Maschine und Server erzwingen könnte. Stellen Sie nach `faden tls off` jede Maschine wieder auf die HTTP-Adresse um
und starten Sie ihren Agent neu:

```bash
faden-agent configure --set BACKEND_URL=http://ai.example.internal
systemctl --user restart faden-agent     # oder: sudo systemctl restart faden-agent
```

**Maschinen, die Sie hinzufügen, während HTTPS an ist,** erhalten unter **Maschinen → Maschine hinzufügen** eine
längere Installationszeile. Sie ruft zuerst die Zertifizierungsstelle des Servers ab, prüft sie gegen einen
Fingerabdruck, der in der Zeile steht, und ruft erst dann das Installationsprogramm ab, geprüft gegen diese
Zertifizierungsstelle. Passt der Fingerabdruck nicht, bricht die Zeile ab, bevor irgendetwas ausgeführt wird. Die
Zeile ist nur so vertrauenswürdig wie die Konsolenseite, von der sie stammt: Öffnen Sie die Konsole, nachdem Sie der
Zertifizierungsstelle vertrauen, nicht indem Sie eine Zertifikatswarnung wegklicken. Bei einem Zertifikat eines
öffentlichen Ausstellers bleibt es bei der kurzen Zeile.

Ein Agent, der das Zertifikat des Servers nicht prüfen kann, bleibt, wo er ist, und schreibt den Grund in sein Log.

## Modellverkehr: das Gateway und die Maschinen {#model-traffic-the-gateway-and-the-machines}

Jeder Prompt und jede Antwort geht zwischen dem Gateway und den Maschinen über das Netzwerk. Diese Verbindung ist
mit gegenseitigem TLS verschlüsselt, ob HTTPS an ist oder nicht: Jede Maschine betreibt vor ihren Modellen einen
kleinen Proxy, der nur das Zertifikat des Gateways akzeptiert, und die Modelle selbst lauschen nur auf der
Loopback-Adresse der Maschine.

Darum müssen Sie sich nicht kümmern: Der Server erstellt, erneuert und wechselt die Zertifikate, und die Maschinen
holen ihre über ihre authentifizierte Verbindung ab.

![Die Karte Modellverkehr eines Clusters mit der Anzeige Verschlüsselt](../images/operate/cluster-model-traffic.png)

- Ein Cluster, der gestartet wurde, bevor es diesen Schutz gab, zeigt auf seiner Seite **Modellverkehr** mit
  „unverschlüsselt“, und das Dashboard führt ihn auf. **Modellverkehr verschlüsseln** und dann
  **Neu starten und verschlüsseln** verlegt seine Modelle hinter den Proxy; während sie neu laden, sind sie nicht
  verfügbar.
- `faden tls machine-ca` zeigt diese Zertifikate. Wenn Sie ein Leck vermuten, erstellt
  `faden tls machine-ca --replace` eine neue Zertifizierungsstelle und entzieht allen früheren das Vertrauen;
  Modellanfragen schlagen etwa eine Minute lang fehl, während das Gateway und die Maschinen neue Zertifikate
  übernehmen.
- Ein externer Provider, den ein Administrator direkt auf die Modelladresse einer Maschine gerichtet hat,
  funktioniert nicht mehr, sobald dieses Modell hinter dem Proxy liegt. Verwenden Sie stattdessen im Chat und in der
  API den eigenen Namen der Bereitstellung.

## Ray zwischen den Maschinen eines Clusters {#ray-between-a-clusters-machines}

In einem Cluster aus mehreren Maschinen läuft Ray über alle seine Maschinen: der Steuerungsverkehr, die Daten, die
bewegt werden, und die Aufrufe an eine Modellkopie auf einer anderen Maschine, Prompts eingeschlossen. Ray
authentifiziert diesen Verkehr immer mit dem Token des Clusters. Um ihn auch zu verschlüsseln:

1. Öffnen Sie **Cluster** und dann den Cluster.
2. Wählen Sie auf der Karte **Verkehr zwischen den Maschinen** die Option **Verschlüsseln**.
3. Lesen Sie die Warnung und wählen Sie **Cluster neu starten**.

Jede Maschine des Clusters startet ihren Container neu, und jedes Modell auf dem Cluster lädt neu. Die Karte zeigt
**Verschlüsselt**, sobald alle Maschinen damit laufen. Jeder Cluster hat seine eigene Zertifizierungsstelle; der
Schlüssel jeder Maschine bleibt auf der Maschine. Jede Maschine braucht einen aktuellen Agent; die Karte nennt die
Maschinen, deren Agent aktualisiert werden muss.

![Die Karte Verkehr zwischen den Maschinen eines Clusters mit der Anzeige Verschlüsselt](../images/operate/cluster-traffic-encrypted.png)

Die Verschlüsselung zwischen den Maschinen verlangsamt das Übertragen großer Datenmengen zwischen ihnen; das
Beantworten von Anfragen und der Chat werden nicht langsamer. Der GPU-zu-GPU-Verkehr gehört nicht zu Ray und lässt
sich nicht verschlüsseln: Beschränken Sie das Netzwerk des Clusters auf seine Maschinen, über ein Kabel oder ein
eigenes Netzwerk.

## Checkliste zur Härtung {#hardening-checklist}

1. Schalten Sie HTTPS ein (`faden tls generate` oder `faden tls use`), bevor Sie Maschinen hinzufügen.
2. Importieren Sie `ca.crt` in die Browser, von denen aus die Konsole genutzt wird, statt die Warnung zu bestätigen.
3. Wenn HTTPS dauerhaft bleibt, schalten Sie HTTP Strict Transport Security ein: Setzen Sie
   `FADEN_HSTS_MAX_AGE=31536000` in `~/fadenstack/.env` und übernehmen Sie es mit `faden up nginx`. Standardmäßig
   ist der Wert 0 (aus), damit sich HTTPS wieder abschalten lässt, ohne Browser auszusperren.
4. Lassen Sie Grafana und Langfuse auf der Loopback-Adresse des Servers (die Voreinstellung; setzen Sie weder
   `GRAFANA_BIND` noch `LANGFUSE_BIND`). Grafana ist in der Konsole unter `/grafana/` erreichbar, für angemeldete
   Benutzer, die es sehen dürfen. Langfuse erreichen Sie über einen SSH-Tunnel:
   `ssh -L 3002:localhost:3002 ai.example.internal`.
5. Halten Sie die Maschinen und das Netzwerk zwischen ihnen und dem Server privat. Schalten Sie
   **Verkehr zwischen den Maschinen** für jeden Cluster mit mehr als einer Maschine ein.
6. Halten Sie den Agent jeder Maschine aktuell (siehe [Upgrade](upgrading.md#update-the-machines-agents)).
7. Wählen Sie auf der Seite jedes Clusters **Modellverkehr verschlüsseln**, wo sie angibt, dass der Verkehr
   unverschlüsselt ist.
8. Behalten Sie die nächtliche Sicherung bei, und halten Sie Sicherungen privat: Sie enthalten die Schlüssel.
9. Behalten Sie die Liste **Handlungsbedarf** im Dashboard im Blick.

## Grenzen {#limits}

- **Kein Widerruf.** Die Zertifizierungsstelle des Servers veröffentlicht keine Sperrliste. Um sie zurückzuziehen,
  verschieben Sie `tls-ca/` an einen anderen Ort und führen `faden tls generate` erneut aus; danach muss jede
  Maschine wieder auf den Server ausgerichtet werden, wie nach `tls off`.
- **Erstes Vertrauen über unverschlüsseltes HTTP.** Eine Maschine, die beigetreten ist, während der Server
  unverschlüsseltes HTTP verwendete, hat seine Zertifizierungsstelle über unverschlüsseltes HTTP gelernt. Fügen Sie
  Maschinen erst hinzu, wenn HTTPS an ist, um das zu vermeiden.
- **Ein vLLM, das Fadenstack bereits laufend vorgefunden hat, hält seinen eigenen Port offen.** Das Gateway erreicht
  es über den Proxy, aber sein ursprünglicher Port bleibt im Netzwerk erreichbar. Sperren Sie ihn mit einer Firewall
  (siehe [Vorhandene vLLM-Server](existing-vllm.md)).
- **Einiger Verkehr rund um die Maschinen ist unverschlüsselt:** das Abrufen von Metriken der Maschinen durch den
  Server und das Runtime-Image, das zwischen Maschinen kopiert wird (gegen seinen Digest geprüft). Keines von beiden
  enthält Prompts.

## Selbst prüfen {#check-it-yourself}

```bash
faden tls status
faden doctor
curl -sI http://ai.example.internal/v1/models                    # eine Umleitung auf https
curl --cacert ca.crt https://ai.example.internal/api/health       # antwortet
openssl s_client -brief -connect ai.example.internal:443 -tls1_1  # abgelehnt: nur TLS 1.2 und 1.3
faden-agent show                                                 # auf einer Maschine: ihre Serveradresse und ihr Vertrauen
```
