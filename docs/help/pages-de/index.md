---
hide:
  - navigation
  - toc
---

<div class="fs-hero" markdown>

# Fadenstack-Hilfe {#fadenstack-help}

<p class="fs-lead">Fadenstack ist ein einziges KI-System für eine Organisation, betrieben von ihrer eigenen IT auf ihren eigenen Servern. Stellen Sie eine Frage, oder beginnen Sie dort, wo Ihre Arbeit beginnt.</p>

<div class="fs-ask" id="fs-ask" data-lang="de" data-none="Dazu gibt es noch keine Seite. Versuchen Sie andere Wörter oder die Suche oben auf der Seite.">
  <label class="fs-ask__field">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/></svg>
    <input type="search" placeholder="Stellen Sie eine Frage, z. B.: Wie füge ich einen Benutzer hinzu?" aria-label="Stellen Sie eine Frage" autocomplete="off" spellcheck="false">
  </label>
  <ol class="fs-ask__results" aria-live="polite" hidden></ol>
  <p class="fs-ask__try">Zum Beispiel:
    <a href="#" data-q="Wie füge ich einen Benutzer hinzu?">Benutzer hinzufügen</a>
    <a href="#" data-q="Wie füge ich eine GPU-Maschine hinzu?">GPU-Maschine verbinden</a>
    <a href="#" data-q="Werden meine personenbezogenen Daten geschützt?">personenbezogene Daten</a>
    <a href="#" data-q="Wie aktualisiere ich Fadenstack?">aktualisieren</a>
  </p>
</div>

</div>

## Häufige Aufgaben {#common-tasks}

<div class="fs-tasks" markdown>

- [:lucide-message-circle: Einen Chat starten](use/chat.md#start-a-chat)
- [:lucide-user-plus: Eine Person hinzufügen](administer/users.md#add-a-person)
- [:lucide-boxes: Ein Modell im Chat anbieten](administer/deployments.md#offer-it-in-the-chat)
- [:lucide-shield-check: Personenbezogene Daten schützen](administer/policies.md#personal-data)
- [:lucide-plug: Ein Tool verbinden (MCP)](administer/mcp-servers.md#register-a-server)
- [:lucide-server: Eine GPU-Maschine hinzufügen](operate/machines.md#add-a-machine)
- [:lucide-circle-arrow-up: Den Server aktualisieren](operate/upgrading.md#upgrade-the-server)
- [:lucide-life-buoy: Fehlerbehebung](operate/troubleshooting.md)

</div>

## Wo Sie beginnen {#where-to-start}

<div class="grid cards fs-sections" markdown>

-   ![Der Chat mit einer Unterhaltung und der Modellauswahl](images/use/chat.png){ loading=lazy }

    :lucide-messages-square:{ .lg .middle } **Nutzen**

    ---

    Mit den Modellen der Organisation chatten, Skills und Tools verwenden und den Assistenten in den Browser und in Office holen. Was mit dem geschieht, was Sie eingeben.

    [Hier beginnen](use/index.md)

-   ![Das Dashboard der Konsole](images/administer/dashboard.png){ loading=lazy }

    :lucide-sliders-horizontal:{ .lg .middle } **Verwalten**

    ---

    Für den IT-Administrator in der Konsole: Personen und Rollen, Modelle und wo sie laufen, Tools, Skills, Agenten, Regeln für personenbezogene Daten und die Nutzung.

    [Hier beginnen](administer/index.md)

-   ![Die Seite eines Clusters mit seinen Maschinen](images/operate/cluster-page.png){ loading=lazy }

    :lucide-server-cog:{ .lg .middle } **Betreiben**

    ---

    Für alle, die den Server und die GPU-Maschinen betreiben: installieren, aktualisieren, absichern, Maschinen zu Clustern verbinden und beheben, was ausfällt.

    [Hier beginnen](operate/index.md)

</div>

Sie bauen auf Fadenstack auf (mit der API, den SDKs, MCP-Servern)? Das steht auf der Website [Developers](https://developers.fadenstack.com/).
