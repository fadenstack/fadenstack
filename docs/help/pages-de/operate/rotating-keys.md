# Schlüssel erneuern {#rotating-the-keys}

`faden secrets rotate` ersetzt die Schlüssel, die die gespeicherten Daten Ihres Servers schützen, durch neue
Zufallsschlüssel und verschlüsselt alles neu, was unter den alten gespeichert war. Nichts muss neu eingegeben
werden: Die gespeicherten Tokens, die API-Schlüssel der Provider, die Zugangsdaten der MCP-Server und die Chats
bleiben, wie sie sind, und **die GPU-Maschinen bleiben verbunden**.

## Wann Sie die Schlüssel erneuern sollten {#when-to-rotate}

- **Die Installation läuft mit einem veröffentlichten Schlüssel.** Das Installationsprogramm schreibt zufällige
  Schlüssel. Eine Installation, die vor Version 0.4.2 von Hand gestartet wurde, läuft vielleicht noch mit den
  Entwicklungsschlüsseln, die mit dem Quellcode veröffentlicht sind, mit `FADEN_ALLOW_PUBLISHED_KEYS=true` in der
  `.env` und einer Warnung bei jedem Start eines Dienstes. Wer den Quellcode liest, kennt diese Schlüssel.
- **Die `.env` ist womöglich abgeflossen:** eine Kopie in einem Ticket, eine Sicherung, die das Haus verlassen hat.
- **Ein Administrator, der die `.env` lesen konnte, hat das Unternehmen verlassen.**

`faden secrets status` zeigt, welche Schlüssel veröffentlichte Werte sind und ob eine Erneuerung unvollständig
geblieben ist:

```bash
faden secrets status
```

Es listet die Variablen und ihren Zustand auf, nie die Schlüssel selbst.

## Was erneuert wird {#what-is-rotated}

| Schlüssel in der `.env` | Was er schützt |
|---|---|
| `FADEN_BACKEND_SETTINGS_MASTER_KEY` | Die Geheimnisse, die die Konsole speichert (das Hugging-Face-Token, der TLS-Schlüssel, Geheimnisse der Wissensdatenbank, die Tokens der Ray-Cluster), die API-Schlüssel externer Provider und die privaten Schlüssel der Zertifizierungsstelle für die Maschinen |
| `FADEN_MCP_ENCRYPTION_KEY` | Die Header und die Umgebung der MCP-Server: Bearer-Tokens und API-Schlüssel. Nur, wo das MCP-Modul eingeschaltet ist |
| `FADEN_PROVENANCE_SIGNING_KEY`, `FADEN_PROVENANCE_HASH_KEYS` | Was erzeugte Inhalte als KI-generiert kennzeichnet: Der Schlüssel, der die Kennzeichnungen signiert, bekommt einen neuen Schlüssel und ein neues Zertifikat von der Zertifizierungsstelle der Installation; die Hash-Schlüssel bekommen eine neue Version, und jede ältere bleibt, damit früher gekennzeichnete Inhalte weiter geprüft werden können. Nur, wo das Modul Provenance eingeschaltet ist |
| `FADEN_GATEWAY_ENCRYPTION_KEY` | Die Chats: Nachrichten, Titel, Gedächtnis und Anhangsnotizen. Nach dem Erneuern dieses Schlüssels werden unverschlüsselt gespeicherte Chats mit dem neuen versiegelt (siehe unten) |

Nicht erneuert werden:

| Nicht erneuert | Warum |
|---|---|
| `FADEN_BACKEND_CREDENTIAL_PEPPER` | Er hält die Zugangsdaten der Maschinen gültig. Ein Zugangsdatum liegt als Hash vor, und ein Hash lässt sich ohne das Geheimnis der Maschine nicht neu bilden; eine Änderung würde jede Maschine trennen. `faden upgrade` trägt ihn in die `.env` ein, mit dem Wert, den der Hauptschlüssel bis dahin hatte |
| `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` | Langfuse verschlüsselt damit seine eigenen Daten und kann sie nicht neu verschlüsseln. `faden secrets status` meldet trotzdem, wenn es veröffentlichte Werte sind |
| Passwörter und Tokens, die nichts verschlüsseln (`POSTGRES_PASSWORD`, die Dienst-Tokens) | Nichts Gespeichertes hängt von ihnen ab. Ändern Sie sie in der `.env` und starten Sie neu; `faden deploy` zeigt, welche zusammengehören |

## Unverschlüsselt gespeicherte Chats {#chats-stored-in-plain-text}

Installationen vor dem 2. Oktober 2026 haben ihre Chats unverschlüsselt gespeichert, ebenso eine Installation ohne
Chat-Schlüssel. `faden upgrade` schreibt einen Chat-Schlüssel, wo keiner gesetzt ist, und versiegelt diese Chats
danach, während die Plattform weiterläuft. Sie können es jederzeit selbst ausführen; es beendet, was ein
abgebrochener Lauf übrig ließ:

```bash
faden secrets seal
```

Es meldet, wie viele Werte es versiegelt hat. Ein Wert, der versiegelt aussieht, sich aber mit keinem Schlüssel
der Installation öffnen lässt, wird gezählt und bleibt, wie er ist.

## Erneuern {#rotate}

```bash
faden secrets rotate
```

Es listet die Schlüssel auf, die es erneuern wird, und fragt nach (`-y` überspringt die Rückfrage).

| Option | Was sie bewirkt |
|---|---|
| `--only settings`, `--only mcp`, `--only chat` | Nur diesen Schlüssel erneuern. Die Option lässt sich für zwei wiederholen. Standard: jeder Schlüssel, den die Installation verwendet |
| `--resume` | Eine abgebrochene Erneuerung zu Ende führen. Den Befehl erneut auszuführen bewirkt dasselbe |
| `--skip-unreadable` | Gespeicherte Werte, die weder der neue noch der alte Schlüssel öffnet, unverändert lassen, statt anzuhalten |
| `-y` | Nicht nachfragen |

Was geschieht, in dieser Reihenfolge:

1. **Eine Sicherung**, mit der aktuellen `.env` darin. Ohne Sicherung wird nichts geändert. Die Dienste müssen
   vorher gesund sein; ist einer es nicht, nennt der Befehl ihn und hält an.
2. **Die neuen Schlüssel kommen in die `.env`**, jeweils neben den alten, der als `..._PREVIOUS` bleibt. Ab jetzt
   öffnen die Dienste Daten, die unter einem der beiden Schlüssel gespeichert sind.
3. **Die Dienste, die die Schlüssel verwenden, starten neu**, hinter der Wartungsseite.
4. **Jeder gespeicherte Wert wird mit dem neuen Schlüssel neu verschlüsselt**, in einem Schritt je Datenbank, und
   geprüft: Ein Wert, den der neue Schlüssel nicht allein öffnet, wird nie gespeichert. Der Befehl gibt aus,
   wie viele Werte er je Datenbank umgestellt hat, nie einen Wert.
5. **Erst dann verschwinden die alten Schlüssel aus der `.env`**, und mit ihnen `FADEN_ALLOW_PUBLISHED_KEYS`, wenn
   kein veröffentlichter Schlüssel mehr übrig ist. Die Dienste starten noch einmal neu, und die Wartungsseite
   verschwindet.

Rechnen Sie für die Neustarts mit einigen Minuten. Die Chats sind der große Teil: Je mehr Chats, desto länger
dauert Schritt 4.

!!! note
    Solange der Befehl läuft, sehen die Leute die Wartungsseite. Eine Chat-Antwort, die beim Neustart der Dienste
    gerade geschrieben wird, kann abbrechen; die Person fragt dann erneut.

## Wenn es anhält {#if-it-stops}

Eine Erneuerung kann anhalten: Der Server verliert den Strom, ein Dienst kommt nicht zurück oder ein Wert lässt
sich nicht lesen. Die neuen Schlüssel stehen dann schon in der `.env` neben den alten, die Dienste öffnen also
alles, was sie zuvor geöffnet haben. Lesen Sie die Meldung, beheben Sie, was sie nennt, und führen Sie den Befehl
erneut aus:

```bash
faden secrets rotate --resume
```

Er macht dort weiter, wo er angehalten hat, mit denselben neuen Schlüsseln, und überspringt Werte, die schon fertig
sind. `faden secrets status` zeigt `rotation unfinished`, bis es so weit ist.

| Sie sehen | Was zu tun ist |
|---|---|
| `These services are not healthy` | Nichts wurde geändert. Beheben Sie das (`faden status`, `faden logs`) und führen Sie den Befehl erneut aus |
| `The backup failed` | Nichts wurde geändert. Schaffen Sie Speicherplatz oder beheben Sie den genannten Fehler, dann den Befehl erneut ausführen |
| `open with neither the new nor the previous key` | Ein gespeicherter Wert war schon vor der Erneuerung unlesbar, etwa weil er unter einem verlorenen Schlüssel gespeichert wurde. Der Befehl hält an, damit Sie es bemerken. Löschen oder geben Sie den Wert neu ein, oder lassen Sie ihn mit `faden secrets rotate --resume --skip-unreadable` unverändert |
| `The services did not restart` oder `The backend did not become healthy` | Sehen Sie in `faden logs backend` nach, beheben Sie die Ursache, führen Sie `faden secrets rotate --resume` aus |

Um zum Stand vor der Erneuerung zurückzukehren, stellen Sie die Sicherung wieder her, die sie angelegt hat
([Sicherungen](backups.md)): Ihre `.env` enthält die alten Schlüssel.

## Nach einem Abfluss {#after-a-leak}

Das Erneuern ersetzt die Schlüssel, aber ein abgeflossener Schlüssel hat auch die Daten geöffnet, die gespeichert
wurden, solange er galt. Was darin geheim war, ändern Sie am besten an der Quelle: API-Schlüssel der Provider,
in MCP-Servern gespeicherte Tokens, das Hugging-Face-Token. Ältere Sicherungen brauchen weiterhin ihre alte `.env`;
bewahren Sie sie so vertraulich wie bisher auf oder löschen Sie sie.
