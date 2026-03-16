---
name: knowledge-distiller
description: >
  Destilliert beliebige Dateien (PDF, DOCX, PPTX, XLSX, TXT, HTML, CSV, JSON, Bilder) in
  wissensorientiertes Markdown plus maschinenlesbaren JSON-LD-Graphen. Kein 1:1-Abschreiben,
  sondern echte Wissensextraktion: Konzepte, Beziehungen, Kernaussagen, Entitäten.
  Erzeugt Dual-Output: .knowledge.md (Menschen/LLM-optimiert) + .knowledge.json (Graph-DB/Embedding-optimiert).
  IMMER verwenden bei: Datei zu Markdown, Dokument zusammenfassen, Wissen extrahieren,
  Knowledge Graph erstellen, Zettelkasten, atomare Notizen, Dokument destillieren,
  Wissensrepräsentation, File to Knowledge, Document Distillation, Markdown-Konvertierung
  mit Wissensstruktur, RAG-Chunks, Embedding-Vorbereitung.
metadata:
  author: mark_zimmermann
  version: '3.1'
  language: de/en
---

# Knowledge Distiller v3.1

Destilliert beliebige Dateien in wissensorientiertes Markdown + maschinenlesbaren Knowledge Graph. Kein Abschreiben, sondern Wissensextraktion.

## Kernprinzip

> Eine Datei ist ein Behälter. Das Wissen darin ist das Wertvolle.
> Diese Skill extrahiert das Wissen, nicht den Text.

### Was diese Skill NICHT tut
- 1:1 Abschrift des Dokuments
- Copy-Paste mit Markdown-Formatierung
- Inhaltsverzeichnis nachbauen

### Was diese Skill TUT
- Konzepte identifizieren und atomar beschreiben
- Beziehungen zwischen Konzepten explizit machen
- Redundanzen eliminieren (gleiche Idee = ein Block)
- Wissensstruktur aufbauen, die unabhängig vom Quelldokument verständlich ist
- Concept-Map als Übersicht erzeugen
- Konzepte in hierarchische Themencluster gruppieren
- Confidence pro Wissensblock vergeben
- **v3.0**: Dual-Output — `.knowledge.md` + `.knowledge.json`
- **v3.0**: Reichere Graph-Nodes mit Kernaussagen als Properties
- **v3.0**: Embedding-optimierte Chunks ohne Markdown-Artefakte
- **NEU v3.1**: Temporale Dimension — Zeitbezug pro Wissensblock, Fakt und Edge
- **NEU v3.1**: Quellzeitraum-Erkennung (source_date / source_period)
- **NEU v3.1**: Zeitliche Gültigkeit (valid_from / valid_until) für vergängliches Wissen

## Temporale Dimension (NEU v3.1)

### Problem

Wenn man über Jahre hinweg Dokumente destilliert, entsteht ein Wissensgraph ohne Zeitachse. Ein Jahresbericht 2024 sagt "Umsatz: 28 Mrd. €", der von 2023 sagt "Umsatz: 34 Mrd. €" — ohne temporale Zuordnung sind das widersprüchliche Fakten statt einer Zeitreihe. Strategien ändern sich, Technologien werden abgelöst, Organisationsstrukturen wandeln sich.

### Lösung: Drei temporale Schichten

| Schicht | Feld | Wo | Bedeutung |
|---------|------|----|-----------|
| **Quellzeitraum** | `source_date` / `source_period` | Metadata + Node + Fakt | Wann wurde das Wissen im Original erzeugt/publiziert? |
| **Gültigkeit** | `valid_from` / `valid_until` | Node + Fakt | Ab wann gilt diese Information? Bis wann? |
| **Destillation** | `distilled` | Metadata | Wann wurde die Extraktion durchgeführt? |

### Regeln für temporale Extraktion

1. **Quellzeitraum ist Pflicht.** Jede Destillation MUSS den Bezugszeitraum der Quelle erfassen.
   - Geschäftsbericht 2024 → `source_period: "FY2024"`, `source_date: "2025-03-15"` (Publikationsdatum)
   - Paper von Juni 2023 → `source_date: "2023-06"`, `source_period: null`
   - Webseite ohne Datum → `source_date: null` (ehrlich sein), ggf. `source_date_inferred: "2026-Q1"` mit `temporal_confidence: "low"`

2. **Pro-Block Zeitbezug bei heterogenem Inhalt.** Wenn ein Dokument Daten aus verschiedenen Zeiträumen enthält (z.B. ein Bericht mit Vergleich zu Vorjahren), erhält jeder Wissensblock seinen eigenen Zeitbezug:
   - Strategieblock → `valid_from: "2024"` (neue Strategie ab 2024)
   - Historischer Umsatz → `source_period: "FY2022"`, `valid_from: "2022"`, `valid_until: "2022"`

3. **Gültigkeit ist semantisch.** Nicht jedes Wissen veraltet:
   - Physikalische Gesetze → `valid_until: null` (zeitlos)
   - Quartalszahlen → `valid_until: "2024-Q4"` (punktuell gültig)
   - Strategie → `valid_from: "2024"`, `valid_until: null` (gültig bis Widerruf)
   - Abgelöstes Konzept → `valid_until: "2023"`, `superseded_by: "neues-konzept-id"`

4. **Zeitformat: ISO 8601 mit Granularitätsabstufung.**
   - Exakt: `"2024-12-31"` oder `"2024-12-31T14:30:00Z"`
   - Monat: `"2024-12"`
   - Quartal: `"2024-Q4"`
   - Jahr: `"2024"`
   - Geschäftsjahr: `"FY2024"`
   - Zeitraum: `"2020/2024"` (ISO 8601 Intervall)
   - Unbekannt: `null`

5. **Temporal Confidence.** Wie sicher ist der Zeitbezug?
   - `"explicit"` — Datum direkt im Dokument genannt
   - `"inferred"` — Aus Kontext abgeleitet (z.B. Dateiname, Dokumentstruktur)
   - `"unknown"` — Kein Zeitbezug erkennbar

### Konsequenz für den Wissensgraphen

Mit temporaler Dimension kann ein System:
- **Zeitreihen bilden** — gleicher Fakt über mehrere Dokumente hinweg = Trend
- **Widersprüche auflösen** — "Umsatz 28 Mrd." und "Umsatz 34 Mrd." sind kein Widerspruch, wenn eines FY2024 und das andere FY2023 ist
- **Veraltetes Wissen erkennen** — Nodes mit `valid_until` in der Vergangenheit
- **Wissens-Aktualität abfragen** — "Was ist der aktuelle Stand zu Thema X?" → neuester Node nach `source_date`
- **Supersession-Ketten** — Konzept A (2022) → ersetzt durch B (2023) → erweitert durch C (2024)

## Dual-Output-Architektur (v3.0)

Jede Destillation erzeugt ZWEI Dateien:

| Datei | Zweck | Zielgruppe |
|-------|-------|------------|
| `[name].knowledge.md` | Menschenlesbar, LLM-optimiert, Obsidian-kompatibel | Menschen, LLMs, RAG-Systeme |
| `[name].knowledge.json` | Maschinenlesbar, Graph-DB-kompatibel, Embedding-ready | Neo4j, Gephi, Vektor-DBs, programmatische Pipelines |

Beide Dateien enthalten das GLEICHE Wissen, aber in formatspezifisch optimierter Form. Keine Kompromisse in eine Richtung.

## Unterstützte Dateitypen

| Format | Methode |
|--------|---------|
| PDF | `read` tool (integrierte Textextraktion + Seitenrendering) |
| DOCX | `read` tool (integrierte Textextraktion) |
| PPTX | `read` tool (Folien als Bilder + Text) |
| XLSX | `read` tool (Tabellenextraktion) |
| CSV | `read` tool oder `bash` mit Python/pandas |
| JSON | `read` tool |
| TXT / MD | `read` tool |
| HTML | `fetch_url` oder `read` tool |
| Bilder (PNG, JPG) | `read` tool (visuelle Analyse) |
| URLs | `fetch_url` mit Inhaltsextraktion |

## Workflow

### Phase 1: Rohextraktion

1. **Datei einlesen** mit dem passenden Tool (siehe Tabelle oben)
2. **Bei großen Dateien** (>100 Seiten): In Abschnitten lesen mit `offset`/`limit`
3. **Bei Bildern/Diagrammen**: Visuelle Inhalte beschreiben und als Wissensblöcke aufnehmen
4. **Rohtext zwischenspeichern** in `/home/user/workspace/distiller_temp/` falls nötig

### Phase 2: Wissensanalyse

Analysiere den Rohinhalt und identifiziere:

1. **Kernkonzepte** — Was sind die zentralen Ideen/Begriffe?
2. **Entitäten** — Personen, Organisationen, Technologien, Frameworks, Standards
3. **Beziehungen** — Wie hängen Konzepte zusammen? (nutzt X, basiert auf Y, widerspricht Z)
4. **Kernaussagen** — Was sind die wichtigsten Thesen/Fakten?
5. **Taxonomie & Cluster** — Welche Hierarchie haben die Konzepte? Welche Themencluster bilden sich?
6. **Redundanzen** — Welche Ideen werden mehrfach ausgedrückt? → Zusammenführen
7. **Confidence-Einschätzung** — Wie gut belegt ist jede Kernaussage im Quelldokument?
8. **Zeitbezug** — Von wann stammt das Wissen? Welcher Bezugszeitraum? Gibt es Vergleiche mit Vorperioden? Ist Wissen zeitlos oder zeitgebunden?

### Phase 3: Wissensstrukturierung

Plane die Ausgabe für beide Formate gleichzeitig. Jedes Konzept wird sowohl als Markdown-Block als auch als JSON-Node realisiert.

### Phase 4: Markdown-Ausgabe schreiben (`.knowledge.md`)

#### 4.1 Frontmatter

```markdown
---
title: [Destillierter Titel - beschreibt das WISSEN, nicht das Dokument]
source: [Originaldateiname]
source_type: [PDF|DOCX|PPTX|...]
source_date: [ISO-Datum der Publikation/Erstellung des Originals, z.B. "2024-03-15"]
source_period: [Bezugszeitraum des Inhalts, z.B. "FY2024" oder "2024-Q3" oder "2020/2024"]
temporal_confidence: [explicit|inferred|unknown]
distilled: [Datum der Destillation]
concepts: [Liste der Kernkonzepte als Tags]
entities: [Liste der wichtigsten Entitäten]
domain: [Wissensdomäne]
confidence: [high|medium|low — Gesamteinschätzung]
companion: [name].knowledge.json
clusters:
  - name: [Clustername 1]
    concepts: [Konzept A, Konzept B]
    description: [Kurzbeschreibung]
  - name: [Clustername 2]
    concepts: [Konzept C, Konzept D]
    description: [Kurzbeschreibung]
---
```

Das `companion`-Feld verlinkt auf die JSON-Datei.

#### 4.2 Concept Map (Pflicht)

Gruppiert nach Clustern:

```markdown
## Concept Map

> Beziehungsstruktur der Kernkonzepte, gruppiert nach Themenclustern

### 🏷️ [Clustername 1]: [Kurzbeschreibung]

- **[[Hauptkonzept A]]**
  - → nutzt: [[Konzept B]]
  - → ermöglicht: [[Konzept C]]
  - ↔ steht in Spannung mit: [[Konzept D]]
```

Beziehungstypen:
- `→ nutzt:` / `→ uses:` — Abhängigkeit
- `→ ermöglicht:` / `→ enables:` — Kausalität
- `→ basiert auf:` / `→ based on:` — Fundierung
- `→ ist Teil von:` / `→ part of:` — Komposition
- `↔ steht in Spannung mit:` / `↔ tension with:` — Widerspruch/Trade-off
- `→ ersetzt:` / `→ replaces:` — Ablösung
- `→ erweitert:` / `→ extends:` — Erweiterung
- `→ Beispiel für:` / `→ example of:` — Instanziierung

#### 4.3 Kernwissen (Pflicht)

Atomare Wissensblöcke — jeder Block = ein Konzept:

```markdown
## Kernwissen

### [Konzeptname]

📊 Confidence: `high` | 🏷️ Cluster: [Clustername] | 📅 Stand: [Zeitbezug, z.B. "FY2024" oder "2024-Q3"]

**Was:** [Klare, eigenständige Definition — verständlich ohne Quelldokument]

**Warum relevant:** [Einordnung, Bedeutung]

**Zeitbezug:** Gültig ab [valid_from] · Quelle: [source_period/source_date]

**Beziehungen:**
- Nutzt → [[Anderes Konzept]]
- Basiert auf → [[Fundament]]

**Kernaussagen:**
- [Atomare Aussage 1]
- [Atomare Aussage 2]

> 💡 [Optionale eigene Einordnung oder kritische Anmerkung]
```

**Confidence-Skala pro Block:**
- `high` — Konzept ist im Quelldokument explizit definiert, mit Belegen oder Daten untermauert
- `medium` — Konzept wird beschrieben, aber ohne starke Belege oder mit Interpretationsspielraum
- `low` — Konzept wird nur impliziert, angedeutet, oder ist eine Schlussfolgerung des Destillierers

Regeln für Wissensblöcke:
- **Atomar**: Ein Block = ein Konzept. Nicht zwei vermischen.
- **Eigenständig**: Jeder Block muss ohne den Rest verständlich sein.
- **Verlinkt**: Querverweise mit `[[Konzeptname]]`-Syntax (Obsidian/Zettelkasten-kompatibel).
- **Keine Redundanz**: Gleiche Idee → einmal beschreiben, dann verlinken.
- **Sprache**: Schreibe in der Sprache des Quelldokuments. Fachbegriffe in Originalsprache beibehalten.
- **Zeitbezug**: Jeder Block MUSS eine 📅 Stand-Angabe haben. Bei zeitlosen Konzepten: `📅 Stand: zeitlos`.

#### 4.4 Fakten & Daten (Optional)

```markdown
## Fakten & Daten

| Fakt | Wert | Kontext | Stand | Confidence |
|------|------|---------|-------|------------|
| [Was] | [Zahl/Datum] | [Wozu relevant] | [Zeitbezug, z.B. FY2024] | high/medium/low |
```

#### 4.5 Offene Fragen (Optional)

```markdown
## Offene Fragen

- [Was bleibt unklar oder widersprüchlich?]
- [Welche Annahmen werden gemacht aber nicht belegt?]
```

#### 4.6 Quellenhinweis (Pflicht)

```markdown
---
> Destilliert aus: `[Originaldateiname]` am [Datum]
> Quellzeitraum: [source_period] (Publikation: [source_date])
> Destillationsmethode: Knowledge Distiller Skill v3.1
> Companion: `[name].knowledge.json`
```

### Phase 5: JSON-Ausgabe schreiben (`.knowledge.json`) — v3.0 + Temporal v3.1

Erzeuge die JSON-Datei mit `write` tool. Die JSON-Datei enthält drei Top-Level-Sektionen:

```json
{
  "@context": {
    "@vocab": "https://schema.org/",
    "kd": "https://knowledge-distiller.dev/schema/",
    "nodes": "kd:nodes",
    "edges": "kd:edges",
    "chunks": "kd:chunks",
    "cluster": "kd:cluster",
    "confidence": "kd:confidence",
    "statements": "kd:statements",
    "embedding_text": "kd:embeddingText",
    "temporal": "kd:temporal",
    "valid_from": "kd:validFrom",
    "valid_until": "kd:validUntil",
    "source_date": "kd:sourceDate",
    "source_period": "kd:sourcePeriod"
  },
  "metadata": {
    "title": "[Destillierter Titel]",
    "source": "[Originaldateiname]",
    "source_type": "[PDF|DOCX|...]",
    "source_date": "[ISO-Datum der Publikation, z.B. 2024-03-15, oder null]",
    "source_period": "[Bezugszeitraum, z.B. FY2024 oder 2024-Q3 oder 2020/2024, oder null]",
    "temporal_confidence": "explicit|inferred|unknown",
    "distilled": "[ISO-Datum der Destillation]",
    "domain": "[Wissensdomäne]",
    "confidence": "[high|medium|low]",
    "companion": "[name].knowledge.md",
    "schema_version": "3.1"
  },
  "clusters": [
    {
      "id": "[cluster-id]",
      "name": "[Clustername]",
      "description": "[Kurzbeschreibung]",
      "concepts": ["konzept-a", "konzept-b"]
    }
  ],
  "nodes": [
    {
      "id": "[konzept-id-kebab-case]",
      "label": "[Anzeigename]",
      "cluster": "[cluster-id]",
      "confidence": "high|medium|low",
      "temporal": {
        "source_date": "[ISO-Datum, oder null — überschreibt Metadata wenn Block-spezifisch]",
        "source_period": "[Bezugszeitraum, oder null — überschreibt Metadata wenn Block-spezifisch]",
        "valid_from": "[ISO-Datum/Zeitraum ab dem dieses Wissen gilt, oder null]",
        "valid_until": "[ISO-Datum/Zeitraum bis wann gültig, null = bis auf Weiteres]",
        "temporal_confidence": "explicit|inferred|unknown"
      },
      "definition": "[Klare, eigenständige Definition — identisch mit 'Was:' im Markdown]",
      "relevance": "[Einordnung — identisch mit 'Warum relevant:' im Markdown]",
      "statements": [
        "[Atomare Aussage 1 — identisch mit Kernaussagen im Markdown]",
        "[Atomare Aussage 2]"
      ],
      "note": "[Optionale Einordnung — identisch mit 💡-Block im Markdown, oder null]"
    }
  ],
  "edges": [
    {
      "source": "[quell-konzept-id]",
      "target": "[ziel-konzept-id]",
      "type": "uses|enables|based-on|part-of|tension|replaces|extends|example-of",
      "label": "[Natürlichsprachige Beschreibung der Beziehung]",
      "weight": 1.0,
      "confidence": "high|medium|low"
    }
  ],
  "facts": [
    {
      "fact": "[Was]",
      "value": "[Zahl/Datum]",
      "context": "[Wozu relevant]",
      "source_period": "[Zeitbezug des Fakts, z.B. FY2024]",
      "valid_from": "[Ab wann gültig]",
      "valid_until": "[Bis wann gültig, null = bis auf Weiteres]",
      "confidence": "high|medium|low"
    }
  ],
  "open_questions": [
    "[Was bleibt unklar?]"
  ],
  "chunks": [
    {
      "id": "[konzept-id]",
      "cluster": "[cluster-id]",
      "confidence": "high|medium|low",
      "source_period": "[Zeitbezug, oder null]",
      "embedding_text": "[Clean-Text-Version des Wissensblocks — OHNE Markdown-Syntax, OHNE Emoji, OHNE Pfeile, OHNE Wikilinks. Nur reiner Fließtext, optimiert für Embedding-Modelle. Enthält: Zeitbezug + Definition + Relevanz + alle Kernaussagen als natürlicher Absatz. Der Zeitbezug MUSS als natürlicher Satz am Anfang stehen, z.B. 'Stand Geschäftsjahr 2024: ...']"
    }
  ]
}
```

**Regeln für die JSON-Datei:**

1. **Nodes müssen reich sein**: Jeder Node enthält `definition`, `relevance`, `statements` — nicht nur `id` und `label`. Dies macht den Graphen ohne die Markdown-Datei verwendbar.

2. **Edges haben Gewichtung**: `weight` (0.0-1.0) gibt die Stärke der Beziehung an. Kern-Beziehungen = 1.0, periphere = 0.5.

3. **Edge-Confidence**: Jede Kante hat eine eigene Confidence-Angabe, unabhängig von den Nodes.

4. **Chunks sind Embedding-ready**: Die `chunks`-Sektion enthält Clean-Text-Versionen jedes Wissensblocks:
   - KEIN Markdown (`**`, `###`, `-`, `>`)
   - KEINE Emoji (`📊`, `🏷️`, `💡`)
   - KEINE Pfeile (`→`, `↔`)
   - KEINE Wikilinks (`[[...]]`)
   - NUR natürlicher Fließtext als zusammenhängender Absatz
   - Enthält: Definition + Relevanz + Kernaussagen + optionale Einordnung
   - Direkt verwendbar als Input für Embedding-Modelle (OpenAI, Cohere, etc.)

5. **JSON-LD `@context`**: Macht die Datei kompatibel mit semantischen Web-Standards. Der `kd:`-Namespace ist ein Platzhalter — bei Bedarf ersetzbar durch eine echte Ontologie-URI.

6. **Konsistenz mit Markdown**: Jeder Node in JSON muss einem Wissensblock in Markdown entsprechen. Jede Edge muss einer Beziehung in der Concept Map entsprechen. `definition` = `Was:`, `relevance` = `Warum relevant:`, `statements` = `Kernaussagen:`.

7. **Temporale Felder sind Pflicht auf Node-Ebene**: Jeder Node MUSS ein `temporal`-Objekt haben. Wenn der Block denselben Zeitbezug wie die Metadata hat, kann `source_date`/`source_period` im Node `null` sein (erbt von Metadata). Wenn der Block einen anderen Zeitbezug hat (z.B. Vorjahresdaten in einem aktuellen Bericht), MUSS der Node eigene Werte setzen.

8. **Embedding-Chunks enthalten den Zeitbezug**: Der `embedding_text` MUSS den Zeitbezug als natürlichsprachigen Satz enthalten, z.B. "Stand Geschäftsjahr 2024: Der Umsatz betrug...". Ohne Zeitbezug im Chunk-Text kann ein Embedding-basiertes System nicht zwischen alten und neuen Informationen unterscheiden.

9. **Fakten haben eigenen Zeitbezug**: Jeder Fakt in `facts` hat eigene `source_period` / `valid_from` / `valid_until` Felder. Das ist besonders wichtig bei Kennzahlen, die sich jährlich ändern.

### Phase 6: Qualitätsprüfung

Prüfe das Ergebnis gegen diese Kriterien:

**Markdown-Qualität:**

| Kriterium | Prüfung |
|-----------|---------|
| **Kein Copy-Paste** | Ist jeder Block eigenständig formuliert? |
| **Keine Redundanz** | Wird jedes Konzept genau einmal definiert? |
| **Atomarität** | Behandelt jeder Block genau ein Konzept? |
| **Verständlichkeit** | Ist jeder Block ohne Quelldokument verständlich? |
| **Vollständigkeit** | Sind alle wesentlichen Konzepte erfasst? |
| **Cluster-Kohärenz** | Sind die Cluster sinnvoll? |
| **Confidence-Plausibilität** | Spiegeln die Werte die Beleglage wider? |
| **Zeitbezug vorhanden** | Hat jeder Block eine 📅 Stand-Angabe? Sind source_date/source_period im Frontmatter gesetzt? |

**JSON-Qualität:**

| Kriterium | Prüfung |
|-----------|---------|
| **Valides JSON** | Ist die Datei syntaktisch korrekt? (Prüfe mit `python -m json.tool`) |
| **Node-Reichhaltigkeit** | Hat jeder Node `definition`, `relevance`, `statements`? |
| **Graph-Konsistenz** | Stimmen Nodes/Edges mit der Markdown Concept Map überein? |
| **Chunk-Sauberkeit** | Enthalten Chunks KEIN Markdown, keine Emoji, keine Syntax-Artefakte? |
| **Bidirektionale Referenz** | Verlinken `.md` und `.json` aufeinander via `companion`-Feld? |
| **Temporale Konsistenz** | Hat jeder Node ein `temporal`-Objekt? Sind `source_date`/`source_period` in Metadata gesetzt? Enthält jeder Embedding-Chunk den Zeitbezug als natürlichen Satz? |

Falls ein Kriterium nicht erfüllt ist → überarbeiten vor dem Speichern.

**CRITICAL**: Prüfe die JSON-Datei immer mit:
```bash
python -c "import json; json.load(open('[datei].knowledge.json')); print('Valid JSON')"
```

### Phase 7: Speichern und Übergeben

1. Speichere die Markdown-Datei als `[name].knowledge.md`
2. Speichere die JSON-Datei als `[name].knowledge.json`
3. Verwende `share_file` um BEIDE Dateien dem User zu übergeben
4. Lösche temporäre Dateien in `distiller_temp/` falls vorhanden

## Batch-Modus: Mehrere Dateien

Wenn mehrere Dateien gleichzeitig destilliert werden sollen:

1. Jede Datei bekommt ein eigenes Paar: `.knowledge.md` + `.knowledge.json`
2. Zusätzlich: `_index.knowledge.md` + `_index.knowledge.json` die alle Dateien verknüpfen

## Spezialbehandlung nach Dateityp

### Tabellen / XLSX / CSV
- Nicht die ganze Tabelle kopieren
- Stattdessen: Was sagt die Tabelle aus? Muster, Trends, Ausreißer
- Nur Kerndaten als Fakten-Tabelle aufnehmen

### Präsentationen / PPTX
- Folientitel ≠ Wissensstruktur. Reihenfolge ignorieren wenn nötig.
- Kernbotschaften pro Folie extrahieren und nach Konzepten neu gruppieren
- Speaker Notes einbeziehen (oft wertvoller als Folieninhalt)

### Bilder / Diagramme
- Visuellen Inhalt beschreiben
- Architekturdiagramm: Komponenten und Beziehungen extrahieren
- Chart: Kernaussage und Trend extrahieren

### Code / JSON
- Nicht den Code kopieren
- Welche Konzepte implementiert der Code? Welche Patterns?
- API-Strukturen → Entitäten und Beziehungen extrahieren

### Webseiten / HTML
- Werbe- und Navigationselemente ignorieren
- Nur den inhaltlichen Kern destillieren

## Hinweise

- **Sprache**: Schreibe in der Sprache des Quelldokuments. Wenn gemischt → Hauptsprache verwenden, Fachbegriffe in Originalsprache.
- **Umfang**: Die Knowledge-Datei sollte 20-40% der Länge des Originals haben.
- **Verlinkung**: `[[Konzeptname]]` Syntax ist Obsidian-kompatibel.
- **Confidence**: Pro Block differenzieren, nicht pauschal.
- **Cluster**: 3-7 Cluster sind ideal. Bei sehr kleinen Dokumenten (< 5 Konzepte) können Cluster entfallen.
- **JSON-Validierung**: Immer syntaktisch prüfen vor dem Übergeben.
- **Chunks**: Die Clean-Text-Chunks in der JSON sollen wie ein natürlicher Absatz klingen, nicht wie eine Aufzählung.
- **Zeitbezug**: JEDE Destillation braucht `source_date` und/oder `source_period`. Im Zweifelsfall `temporal_confidence: "inferred"` setzen statt den Zeitbezug wegzulassen. Ein Graph ohne Zeitachse ist für Multi-Dokument-Szenarien unbrauchbar.
