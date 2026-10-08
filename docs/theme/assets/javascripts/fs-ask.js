// The question search on Help's front page. It runs in the browser on two files the build writes next to the
// pages: search.json (every section, from Zensical) and assets/questions.json (the questions each page answers,
// from docs/help/questions*.toml). No model and no server: words are normalised (case, accents, endings), widened
// with a few synonyms, matched with some tolerance for typos, and scored so that questions and titles count more
// than body text. It only starts on a page with an element #fs-ask.
(function () {
  "use strict";

  var STOP = {
    en: "a an and are as at be by can could do does doing for from get how i if in into is it its me my of on or our should so that the their there this to was we what when where which who why will with would you your",
    de: "a am an auf aus bei bin bis da das dem den der des die du ein eine einem einen einer es fur gibt hat ich ihr ihre im in ist kann man mein meine meinen mich mir mit nach nicht oder sich sie sind so soll uber um und uns unsere vom von was welche welchen welcher welches wenn wer werden wie wird wo wir zu zum zur",
  };

  // Words people use for the same thing; each group is matched as one. Written already normalised (no accents).
  var SYNONYMS = {
    en: [
      "user person people account member colleague someone employee",
      "add create invite new",
      "remove delete block disable deactivate end revoke",
      "signin login logon",
      "pii personal gdpr privacy redact mask anonymise anonymize sensitive",
      "model llm ai",
      "machine gpu node worker",
      "upgrade update",
      "backup restore",
      "https ssl tls certificate cert",
      "token key apikey",
      "cost usage price budget billing spend",
      "tool mcp plugin integration",
      "skill prompt instruction template",
      "chat conversation message",
      "error fail failing problem broken troubleshoot missing offline unreachable",
      "install setup",
      "log logs",
      "office word excel powerpoint outlook addin",
      "browser chrome extension",
      "deployment deploy host serve",
    ],
    de: [
      "benutzer person personen konto mitarbeiter nutzer user kollege jemand",
      "hinzufugen anlegen erstellen einladen neu",
      "entfernen loschen sperren deaktivieren beenden widerrufen",
      "anmelden anmeldung login einloggen",
      "personenbezogen pii dsgvo datenschutz schwarzen maskieren anonymisieren",
      "modell llm ki",
      "maschine gpu rechner knoten",
      "aktualisieren upgrade update",
      "sicherung backup wiederherstellen",
      "https ssl tls zertifikat",
      "token schlussel key",
      "kosten preis budget abrechnung",
      "tool tools werkzeug mcp plugin",
      "skill vorlage anweisung prompt",
      "chat unterhaltung nachricht",
      "fehler problem kaputt fehlschlag fehlt offline unerreichbar",
      "installieren einrichten installation",
      "protokoll protokolle log logs",
      "office word excel powerpoint outlook addin",
      "browser chrome erweiterung",
      "bereitstellung bereitstellen deployment",
    ],
  };

  function fold(s) {
    return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ß/g, "ss");
  }

  // Phrases that are one idea, before the text is split into words.
  function phrases(s) {
    return s
      .replace(/\b(sign|log)(ged)?[\s-]+(in|on)\b/g, "signin")
      .replace(/\bapi[\s-]+(key|token)s?\b/g, "apikey")
      .replace(/\badd[\s-]+ins?\b/g, "addin")
      .replace(/\bset[\s-]+up\b/g, "setup")
      .replace(/\bback[\s-]+up\b/g, "backup")
      .replace(/\bpersonal\s+data\b/g, "pii")
      .replace(/\bpersonenbezogenen?\s+daten\b/g, "pii");
  }

  function stem(w, lang) {
    if (w.length <= 3) return w;
    if (lang === "de") {
      var de = ["ungen", "ung", "en", "er", "es", "e", "n", "s"];
      for (var i = 0; i < de.length; i++) {
        var suf = de[i];
        if (w.length - suf.length >= 4 && w.slice(-suf.length) === suf) return w.slice(0, -suf.length);
      }
      return w;
    }
    if (w.slice(-3) === "ies" && w.length > 4) return w.slice(0, -3) + "y";
    if (w.slice(-3) === "ing" && w.length > 5) return w.slice(0, -3);
    if (w.slice(-2) === "ed" && w.length > 4) return w.slice(0, -2);
    if (w.slice(-2) === "es" && /(ss|sh|ch|x)es$/.test(w)) return w.slice(0, -2);
    if (w.slice(-1) === "s" && !/(ss|us|is)$/.test(w)) return w.slice(0, -1);
    return w;
  }

  function words(text, lang, keepStop) {
    var stop = STOP[lang] || STOP.en;
    var out = [];
    phrases(fold(text))
      .split(/[^a-z0-9]+/)
      .forEach(function (w) {
        if (!w) return;
        if (!keepStop && (" " + stop + " ").indexOf(" " + w + " ") !== -1) return;
        out.push(stem(w, lang));
      });
    return out;
  }

  function editDistanceAtMost1(a, b) {
    if (Math.abs(a.length - b.length) > 1) return false;
    var i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a.length > b.length) i++;
      else if (a.length < b.length) j++;
      else { i++; j++; }
    }
    return edits + (a.length - i) + (b.length - j) <= 1;
  }

  function stripHtml(html) {
    var d = document.createElement("div");
    d.innerHTML = html;
    return (d.textContent || "").replace(/\s+/g, " ").trim();
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function Index(lang, sections, questions) {
    this.lang = lang;
    this.docs = [];
    this.df = Object.create(null);
    var syn = Object.create(null);
    (SYNONYMS[lang] || SYNONYMS.en).forEach(function (group, gi) {
      group.split(" ").forEach(function (w) { syn[stem(w, lang)] = gi; });
    });
    this.syn = syn;
    var pageTitles = Object.create(null);
    sections.forEach(function (it) {
      if (it.level === 1 || it.location.indexOf("#") === -1) pageTitles[it.location] = it.title;
    });
    var self = this;
    questions.forEach(function (p) {
      p.questions.forEach(function (q) {
        self.add({
          url: p.url + (q.at ? "#" + q.at : ""),
          page: p.url,
          title: q.q,
          crumb: p.title,
          text: "",
          fields: [[words(q.q, lang), 3], [words(p.title, lang), 1]],
        });
      });
    });
    sections.forEach(function (it) {
      var page = it.location.split("#")[0];
      var text = stripHtml(it.text || "");
      var top = it.location.indexOf("#") === -1;
      self.add({
        url: it.location,
        page: page,
        title: stripHtml(it.title),
        crumb: top ? "" : pageTitles[page] || "",
        text: text,
        fields: [[words(stripHtml(it.title), lang), top ? 2.5 : 2], [words(text, lang).slice(0, 400), 1]],
      });
    });
    this.vocab = Object.keys(this.df);
  }

  // Each field counts on its own: a word in a question or a heading is worth much more than the same word
  // repeated in body text, which saturates slowly.
  Index.prototype.add = function (doc) {
    var seen = Object.create(null);
    doc.f = doc.fields.map(function (f) {
      var tf = Object.create(null);
      f[0].forEach(function (w) {
        tf[w] = (tf[w] || 0) + 1;
        seen[w] = true;
      });
      return { tf: tf, w: f[1], k: f[1] > 1 ? 0.5 : 2 };
    });
    for (var w in seen) this.df[w] = (this.df[w] || 0) + 1;
    this.docs.push(doc);
  };

  Index.prototype.weigh = function (doc, word) {
    var sum = 0;
    doc.f.forEach(function (f) {
      var tf = f.tf[word];
      if (tf) sum += f.w * (tf / (tf + f.k));
    });
    return sum;
  };

  // Each word of the query becomes a set of words that count for it, with a weight: itself, its synonyms, words
  // one typo away, and (for the last word, while it is being typed) words that start with it.
  Index.prototype.expand = function (term, isLast) {
    var out = Object.create(null);
    var self = this;
    out[term] = 1;
    if (term in this.syn) {
      this.vocab.forEach(function (v) {
        if (self.syn[v] === self.syn[term] && v !== term) out[v] = Math.max(out[v] || 0, 0.8);
      });
    }
    if (!(term in this.df) && term.length >= 5) {
      this.vocab.forEach(function (v) { if (editDistanceAtMost1(term, v)) out[v] = Math.max(out[v] || 0, 0.7); });
    }
    if (isLast && term.length >= 3) {
      this.vocab.forEach(function (v) { if (v.length > term.length && v.indexOf(term) === 0) out[v] = Math.max(out[v] || 0, 0.6); });
    }
    return out;
  };

  Index.prototype.search = function (query, limit) {
    var terms = words(query, this.lang);
    if (!terms.length) terms = words(query, this.lang, true);
    if (!terms.length) return [];
    var n = this.docs.length;
    var self = this;
    var expanded = terms.map(function (t, i) { return self.expand(t, i === terms.length - 1); });
    var best = Object.create(null);
    this.docs.forEach(function (doc) {
      var score = 0, matched = 0;
      expanded.forEach(function (alts) {
        var top = 0;
        for (var w in alts) {
          var weight = self.weigh(doc, w);
          if (!weight) continue;
          var idf = Math.log(1 + n / self.df[w]);
          top = Math.max(top, alts[w] * idf * weight);
        }
        if (top > 0) matched++;
        score += top;
      });
      if (!matched) return;
      score *= 0.5 + 0.5 * (matched / expanded.length);
      var prev = best[doc.page];
      if (!prev || score > prev.score) best[doc.page] = { doc: doc, score: score };
    });
    // Only answers close to the best one: a weak match after a good one is noise.
    var hits = Object.keys(best)
      .map(function (k) { return best[k]; })
      .sort(function (a, b) { return b.score - a.score; });
    var floor = hits.length ? hits[0].score * 0.35 : 0;
    return hits.filter(function (h) { return h.score >= floor; }).slice(0, limit || 5);
  };

  function snippet(text, query, lang) {
    if (!text) return "";
    var terms = words(query, lang);
    var lower = fold(text);
    var at = -1;
    terms.some(function (t) { at = lower.indexOf(t); return at !== -1; });
    var start = Math.max(0, at - 60);
    var cut = text.slice(start, start + 180);
    return (start > 0 ? "… " : "") + escapeHtml(cut) + (start + 180 < text.length ? " …" : "");
  }

  function setUp(box) {
    if (box.dataset.ready) return;
    box.dataset.ready = "1";
    var input = box.querySelector("input");
    var list = box.querySelector(".fs-ask__results");
    var lang = box.dataset.lang || document.documentElement.lang || "en";
    lang = lang.slice(0, 2) === "de" ? "de" : "en";
    var index = null, loading = null, timer = null;

    function load() {
      if (index || loading) return loading;
      loading = Promise.all([
        fetch("search.json").then(function (r) { return r.json(); }),
        fetch("assets/questions.json").then(function (r) { return r.json(); }),
      ]).then(function (res) {
        index = new Index(lang, res[0].items || [], res[1].pages || []);
      });
      return loading;
    }

    function render() {
      var q = input.value.trim();
      if (q.length < 2) { list.innerHTML = ""; list.hidden = true; return; }
      load().then(function () {
        var hits = index.search(q);
        list.hidden = false;
        if (!hits.length) {
          list.innerHTML = '<li class="fs-ask__none">' + escapeHtml(box.dataset.none || "No page answers that yet.") + "</li>";
          return;
        }
        list.innerHTML = hits.map(function (h) {
          var d = h.doc;
          return '<li><a href="' + escapeHtml(d.url) + '"><b>' + escapeHtml(d.title) + "</b>" +
            (d.crumb ? '<span class="fs-ask__crumb">' + escapeHtml(d.crumb) + "</span>" : "") +
            (d.text ? '<span class="fs-ask__text">' + snippet(d.text, q, lang) + "</span>" : "") +
            "</a></li>";
        }).join("");
      });
    }

    input.addEventListener("focus", load);
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(render, 120); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var first = list.querySelector("a");
        if (first) { e.preventDefault(); first.click(); }
      } else if (e.key === "ArrowDown") {
        var a = list.querySelector("a");
        if (a) { e.preventDefault(); a.focus(); }
      }
    });
    list.addEventListener("keydown", function (e) {
      var links = Array.prototype.slice.call(list.querySelectorAll("a"));
      var i = links.indexOf(document.activeElement);
      if (e.key === "ArrowDown" && i < links.length - 1) { e.preventDefault(); links[i + 1].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); (i > 0 ? links[i - 1] : input).focus(); }
    });
    box.querySelectorAll("[data-q]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        input.value = el.dataset.q;
        input.focus();
        render();
      });
    });
  }

  function start() {
    var box = document.getElementById("fs-ask");
    if (box) setUp(box);
  }

  // With instant navigation the theme swaps pages without a reload and announces each one on document$.
  if (window.document$ && typeof window.document$.subscribe === "function") window.document$.subscribe(start);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
