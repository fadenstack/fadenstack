# Modelle auf einem Mac bereitstellen

Ein Mac mit Apple-Silicon kann ein Modell allein bereitstellen: ein Mac, ein Modell zur Zeit, kein Cluster. Diese
Seite beschreibt die Einrichtung in der Konsole, was die Statuskarte des Macs zeigt, wie viel Speicher ein Modell
nutzen darf und was ein Mac nicht kann.

## Voraussetzungen

- Auf dem Mac ist der Fadenstack-Agent installiert, und er erscheint unter **KI-Infrastruktur → Maschinen**
  (siehe [Maschinen verbinden](../operate/machines.md)). Der Agent muss mindestens Version 0.3.0 haben.
- Er hat genug Speicher für das Modell. Ein Mac gibt der Engine des Modells etwa vier Fünftel seines Speichers
  (der Rest bleibt macOS), abzüglich einer Reserve: 3 GiB bei 36 GiB, 4 GiB bei 64 GiB, 6 GiB bei 128 GiB.

## Den Mac einrichten

1. Öffnen Sie den Mac unter **KI-Infrastruktur → Maschinen**.
2. Wählen Sie **Modelle auf diesem Mac bereitstellen**. Dadurch entsteht eine Umgebung nur für diesen Mac, die sich
   öffnet. Wiederholen Sie den Schritt, öffnet sich dieselbe Umgebung.
3. Stellen Sie ein Modell wie üblich bereit (siehe [Bereitstellungen](deployments.md)). Die erste Bereitstellung lädt
   die Laufzeit auf den Mac, was einige Minuten dauert.

Das Laden eines Modells dauert länger als der Start eines Containers: ein kleines Modell braucht ein bis zwei Minuten,
ein großes mehrere. Die Bereitstellung zeigt **Modell wird geladen**, bis sie bereit ist; wird ein Modell nicht
rechtzeitig bereit, gilt die Bereitstellung als fehlgeschlagen.

## Die Statuskarte des Macs

| Zeile | Bedeutung | Was zu tun ist |
|---|---|---|
| Native Laufzeit | Installiert und bereit, oder wird mit dem ersten Modell geladen. | Nichts. |
| Sandbox | An: Die Engine kann die eigenen Dateien des Agenten nicht lesen. | An lassen. Nur zum Testen ausschalten. |
| Anwendungs-Firewall | Wenn an, kann sie das Gateway am Erreichen des Modell-Ports hindern. | Unter Systemeinstellungen, Netzwerk, Firewall eingehende Verbindungen für `faden-agent` erlauben. |
| FileVault | Wenn an, wartet der Mac nach einem Stromausfall auf eine Anmeldung. | Mit `sudo fdesetup authrestart` neu starten, damit er unbeaufsichtigt zurückkommt, oder FileVault ausgeschaltet lassen. |

## Speicher

Wählen Sie den Mac im Bereitstellungsdialog, zeigt er, was die GPU nutzen kann, was dem System bleibt, die Gewichte
des Modells, den Platz für Unterhaltungen und den Speicheranteil des Modells. Eine als *geschätzt* markierte Zahl
stammt aus dem Gesamtspeicher des Macs, bis die Laufzeit den echten Wert gemeldet hat.

Ein Modell, das mehr braucht, als der Mac geben kann, wird abgelehnt, mit dem nötigen und dem verfügbaren Speicher.
Ein kleinerer Kontext oder weniger parallele Unterhaltungen können es passend machen; der Dialog schlägt beides vor.

## Was ein Mac nicht kann

- Ein Modell zur Zeit. Löschen Sie die laufende Bereitstellung, bevor Sie ein anderes bereitstellen.
- Keine Skalierung, keine Kopien auf anderen Maschinen, kein Entleeren und kein Netzwerkplan: das sind Funktionen von Clustern.
- GPTQ-Modelle werden abgelehnt; AWQ- und MLX-Modelle funktionieren. Ein quantisierter KV-Cache, ein Limit für Denk-Tokens,
  Prefix-Caching, spekulatives Dekodieren und Modelle, die Code aus ihrem Repository ausführen, werden nicht angeboten.
- Angeboten werden nur der Tool-Call-Parser `hermes` und der Reasoning-Parser `qwen3`. Weitere folgen nach ihrer Prüfung.
- Ein Mac kann nicht gleichzeitig Mitglied eines Clusters sein und allein bereitstellen.
