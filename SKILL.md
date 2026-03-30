---
name: knowledge-distiller
description: >
  Destilliert beliebige Dateien (PDF, DOCX, PPTX, XLSX, TXT, HTML, CSV, JSON, Bilder) in
  wissensorientiertes Markdown plus maschinenlesbaren JSON-LD-Graphen. Kein 1:1-Abschreiben,
  sondern echte Wissensextraktion: Konzepte, Beziehungen, Kernaussagen, Entitäten.
  Erzeugt einen wahren Context Graph, der nicht nur Temporale, sondern auch Räumliche
  (Spatial), Herkunfts- (Provenance) und Begründungsdimensionen (Reasoning Trace) abbildet.
  Erzeugt Dual-Output: .knowledge.md (Menschen/LLM-optimiert) + .knowledge.json (Graph-DB/Embedding-optimiert).
  IMMER verwenden bei: Datei zu Markdown, Dokument zusammenfassen, Wissen extrahieren,
  Knowledge Graph erstellen, Zettelkasten, atomare Notizen, Dokument destillieren,
  Wissensrepräsentation, File to Knowledge, Document Distillation, Markdown-Konvertierung
  mit Wissensstruktur, RAG-Chunks, Embedding-Vorbereitung.
metadata:
  author: mark_zimmermann
  version: '4.0'
  language: de/en
---

# Knowledge Distiller v4.0 (Context Graph Edition)

Destilliert beliebige Dateien in wissensorientiertes Markdown + maschinenlesbaren Knowledge Graph, angereichert mit Kontext. Kein Abschreiben, sondern Wissensextraktion.

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
- **v3.1**: Temporale Dimension — Zeitbezug pro Wissensblock, Fakt und Edge
- **v3.1**: Quellzeitraum-Erkennung (source_date / source_period)
- **v3.1**: Zeitliche Gültigkeit (valid_from / valid_until) für vergängliches Wissen
- **NEU v4.0**: Räumliche Dimension — Geografischer Bezug (spatial_scope, locations)
- **NEU v4.0**: Herkunft & Beleg (Provenance) — Autorität der Quelle (source_entity, authoritative_source)
- **NEU v4.0**: Begründungslogik (Reasoning Trace) — "Warum" hinter Beziehungen und Fakten

## Temporale Dimension (v3.1)

### Problem

Wenn man über Jahre hinweg Dokumente destilliert, entsteht ein Wissensgraph ohne Zeitachse. Ein Jahresbericht 2024 sagt "Umsatz: 28 Mrd. €", der von 2023 sagt "Umsatz: 34 Mrd. €" — ohne temporale Zuordnung sind das widersprüchliche Fakten statt einer Zeitreihe. Strategien ändern sich, Technologien werden abgelöst, Organisationsstrukturen wandeln sich.

### Lösung: Drei temporale Schichten

| Schicht | Feld | Wo | Bedeutung |
|---------|------|----|-----------|
| **Quellzeitraum** | `source_date` / `source_period` | Metadata + Node + Fakt | Wann wurde das Wissen im Original erzeugt/publiziert? |
| **Gültigkeit** | `valid_from` / `valid_until` | Node + Fakt | Ab wann gilt diese Information? Bis wann? |
| **Destillation** | `distilled` | Metadata | Wann wurde die Extraktion durchgeführt? |

### Regeln für temporale Extraktion

1.  **Quellzeitraum ist Pflicht.** Jede Destillation MUSS den Bezugszeitraum der Quelle erfassen.
    *   Geschäftsbericht 2024 → `source_period: "FY2024"`, `source_date: "2025-03-15"` (Publikationsdatum)
    *   Paper von Juni 2023 → `source_date: "2023-06"`, `source_period: null`
    *   Webseite ohne Datum → `source_date: null` (ehrlich sein), ggf. `source_date_inferred: "2026-Q1"` mit `temporal_confidence: "low"`

2.  **Pro-Block Zeitbezug bei heterogenem Inhalt.** Wenn ein Dokument Daten aus verschiedenen Zeiträumen enthält (z.B. ein Bericht mit Vergleich zu Vorjahren), erhält jeder Wissensblock seinen eigenen Zeitbezug:
    *   Strategieblock → `valid_from: "2024"` (neue Strategie ab 2024)
    *   Historischer Umsatz → `source_period: "FY2022"`, `valid_from: "2022"`, `valid_until: "2022"`

3.  **Gültigkeit ist semantisch.** Nicht jedes Wissen veraltet:
    *   Physikalische Gesetze → `valid_until: null` (zeitlos)
    *   Quartalszahlen → `valid_until: "2024-Q4"` (punktuell gültig)
    *   Strategie → `valid_from: "2024"`, `valid_until: null` (gültig bis Widerruf)
    *   Abgelöstes Konzept → `valid_until: "2023"`, `superseded_by: "neues-konzept-id"`

4.  **Zeitformat: ISO 8601 mit Granularitätsabstufung.**
    *   Exakt: `"2024-12-31"` oder `"2024-12-31T14:30:00Z"`
    *   Monat: `"2024-12"`
    *   Quartal: `"2024-Q4"`
    *   Jahr: `"2024"`
    *   Geschäftsjahr: `"FY2024"`
    *   Zeitraum: `"2020/2024"` (ISO 8601 Intervall)
    *   Unbekannt: `null`

5.  **Temporal Confidence.** Wie sicher ist der Zeitbezug?
    *   `"explicit"` — Datum direkt im Dokument genannt
    *   `"inferred"` — Aus Kontext abgeleitet (z.B. Dateiname, Dokumentstruktur)
    *   `"unknown"` — Kein Zeitbezug erkennbar

### Konsequenz für den Wissensgraphen

Mit temporaler Dimension kann ein System:
- **Zeitreihen bilden** — gleicher Fakt über mehrere Dokumente hinweg = Trend
- **Widersprüche auflösen** — "Umsatz 28 Mrd." und "Umsatz 34 Mrd." sind kein Widerspruch, wenn eines FY2024 und das andere FY2023 ist
- **Veraltetes Wissen erkennen** — Nodes mit `valid_until` in der Vergangenheit
- **Wissens-Aktualität abfragen** — "Was ist der aktuelle Stand zu Thema X?" → neuester Node nach `source_date`
- **Supersession-Ketten** — Konzept A (2022) → ersetzt durch B (2023) → erweitert durch C (2024)

## Räumliche Dimension (Spatial) (NEU v4.0)

### Problem

Wissen ist oft ortsgebunden, aber diese Information fehlt in generischen Wissensgraphen. "Neue Regulierung" – wo gilt sie? "Marktwachstum" – in welcher Region? Ohne räumlichen Bezug können Fakten missverstanden oder falsch angewendet werden.

### Lösung: Zwei räumliche Schichten

| Schicht | Feld | Wo | Bedeutung |
|---------|------|----|-----------|
| **Geltungsbereich** | `spatial_scope` | Metadata + Node | Der globale Geltungsbereich des Wissens (z.B. EU, Global, APAC, Germany). |
| **Spezifische Orte** | `locations` | Node | Konkrete geografische Orte, auf die sich das Wissen bezieht (z.B. "Berlin", "San Francisco"). |

### Regeln für räumliche Extraktion

1.  **Geltungsbereich ist wichtig.** Jede Destillation MUSS den übergeordneten geografischen Geltungsbereich erfassen, falls relevant.
    *   EU-Datenschutz-Grundverordnung → `spatial_scope: "EU"`
    *   US-Wahlkampf-Analyse → `spatial_scope: "USA"`
    *   Physikalische Gesetze → `spatial_scope: "Global"` (oder null, wenn implizit global)

2.  **Pro-Block-Bezug bei heterogenem Inhalt.** Wenn ein Dokument Wissen mit verschiedenen geografischen Bezügen enthält (z.B. ein globaler Bericht mit regionalen Analysen), erhält jeder Wissensblock seinen eigenen räumlichen Bezug.
    *   Block zur APAC-Strategie → `spatial_scope: "APAC"`
    *   Block zur deutschen Marktanteilsentwicklung → `spatial_scope: "Germany"`, `locations: ["Deutschland"]`

3.  **Spezifische Orte für Entitäten und Ereignisse.** Bei Konzepten, die sich auf konkrete Orte beziehen, sind die `locations` anzugeben.
    *   Konzept "Büroeröffnung in München" → `locations: ["München"]`
    *   Konzept "Silicon Valley Innovation" → `locations: ["Silicon Valley"]`

4.  **Format für `spatial_scope`**: Standardisierte Begriffe (z.B. "Global", "EU", "USA", "Germany", "France", "APAC", "EMEA", "LATAM").
5.  **Format für `locations`**: Array von Strings für Städte, Regionen, Länder.
6.  **Spatial Confidence.** Wie sicher ist der räumliche Bezug?
    *   `"explicit"` — Ort/Region direkt im Dokument genannt
    *   `"inferred"` — Aus Kontext abgeleitet (z.B. Name der Behörde, Adressen in Referenzen)
    *   `"unknown"` — Kein räumlicher Bezug erkennbar

### Konsequenz für den Wissensgraphen

Mit räumlicher Dimension kann ein System:
- **Wissen nach Region filtern** — "Was sind die Herausforderungen in Europa?"
- **Regionale Unterschiede erkennen** — Vergleiche von Fakten über verschiedene `spatial_scope` hinweg
- **Relevanz für Anwender bestimmen** — Zeige nur Wissen an, das für den Standort des Nutzers relevant ist.

## Herkunft & Beleg (Provenance) (NEU v4.0)

### Problem

Nicht alle Informationen sind gleich vertrauenswürdig. Ein Fakt aus einer wissenschaftlichen Studie hat eine andere Autorität als eine Aussage in einem Blogbeitrag. Ohne Herkunftsangabe ist die Glaubwürdigkeit des Wissens schwer zu bewerten.

### Lösung: Detaillierte Herkunftsangabe

| Schicht | Feld | Wo | Bedeutung |
|---------|------|----|-----------|
| **Quelle/Entität** | `source_entity` | Metadata + Node + Fakt | Die Entität, die das Wissen ursprünglich erzeugt oder veröffentlicht hat (z.B. "EU-Kommission", "Analystenfirma X", "John Doe"). |
| **Autorität** | `authoritative_source` | Metadata + Node + Fakt | Einschätzung der Glaubwürdigkeit und Fachkompetenz dieser Quelle für das betreffende Wissen. |

### Regeln für Herkunfts-Extraktion

1.  **Quellenentität ist wichtig.** Identifiziere den Verfasser oder die veröffentlichende Organisation der Quelle.
    *   Studie von Fraunhofer-Institut → `source_entity: "Fraunhofer-Institut"`
    *   Unternehmens-Website → `source_entity: "[Firmenname]"`
    *   Blogbeitrag von Einzelperson → `source_entity: "[Name des Autors]"`

2.  **Autorität bewerten.** Schätze die Glaubwürdigkeit der Quelle für den jeweiligen Wissensbereich ein.
    *   Regulierungsbehörde für Gesetze → `authoritative_source: "high"`
    *   Wissenschaftliche Peer-Review-Publikation → `authoritative_source: "high"`
    *   Marktanalystenbericht → `authoritative_source: "medium"`
    *   Privater Blogbeitrag → `authoritative_source: "low"`

3.  **Pro-Block-Bezug.** Wenn innerhalb eines Dokuments unterschiedliche Quellen zitiert oder referenziert werden, ist der Provenance-Block anzupassen.
    *   Block über interne Unternehmensrichtlinien → `source_entity: "[Unternehmen X]", authoritative_source: "high"`
    *   Block über Marktzahlen, die einen externen Anbieter zitieren → `source_entity: "[Name des externen Anbieters]", authoritative_source: "medium"`

4.  **Provenance Confidence.** Wie sicher ist die Zuweisung der Herkunft?
    *   `"explicit"` — Autor/Herausgeber/Quelle direkt genannt
    *   `"inferred"` — Aus Impressum, URL, Dateipfaden abgeleitet
    *   `"unknown"` — Keine eindeutige Herkunft erkennbar

### Konsequenz für den Wissensgraphen

Mit Provenance kann ein System:
- **Wissen nach Glaubwürdigkeit filtern** — "Zeige mir nur Fakten aus hoch-autoritativen Quellen."
- **Widersprüche bewerten** — Bei widersprüchlichen Aussagen bevorzugt die vertrauenswürdigere Quelle.
- **Transparenz schaffen** — Nachvollziehbarkeit, woher das Wissen stammt.

## Begründungslogik (Reasoning Trace) (NEU v4.0)

### Problem

Ein Wissensgraph zeigt Beziehungen (`A nutzt B`), aber oft nicht das "Warum". Warum nutzt A, B? Warum wurde eine bestimmte Entscheidung getroffen? Diese Begründungslogik ist entscheidend für das Verständnis und die Anwendbarkeit von Wissen.

### Lösung: Explizite Begründungen an Kanten und Fakten

| Schicht | Feld | Wo | Bedeutung |
|---------|------|----|-----------|
| **Begründung** | `reasoning` | Edge + Fakt | Eine natürlichsprachliche Erklärung, die das "Warum" hinter einer Beziehung oder einem Fakt beschreibt. |

### Regeln für Begründungs-Extraktion

1.  **Begründungen für wichtige Beziehungen.** Jede Kante, die eine Kausalität, Abhängigkeit, oder eine strategische Wahl darstellt, sollte eine Begründung erhalten.
    *   `A → nutzt: B (Weil B eine höhere Performance bietet)`
    *   `Entscheidung X → basiert auf: Analyse Y (Um Kosten zu senken)`

2.  **Begründungen für wichtige Fakten.** Wenn ein Fakt eine spezifische Ursache oder eine Implikation hat, sollte diese als Begründung festgehalten werden.
    *   `Umsatz 28 Mrd. € (Aufgrund erfolgreicher Produkteinführung in Q2)`

3.  **Klar und prägnant.** Die Begründung sollte eine kurze, eigenständige Erklärung sein.
4.  **Reasoning Confidence.** Wie sicher ist die extrahierte Begründung?
    *   `"explicit"` — Begründung direkt im Dokument genannt ("Der Grund für X ist Y...")
    *   `"inferred"` — Aus Kontext, Implikationen oder Argumentationsketten abgeleitet
    *   `"unknown"` — Keine Begründung erkennbar, oder nicht relevant

### Konsequenz für den Wissensgraphen

Mit Reasoning Trace kann ein System:
- **"Warum"-Fragen beantworten** — Bessere Erklärbarkeit von Beziehungen und Zuständen.
- **Entscheidungsfindung unterstützen** — Gründe für vergangene Entscheidungen nachvollziehen.
- **Kritische Analyse ermöglichen** — Hinterfragen der Logik und Argumentation im Quelldokument.

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

1.  **Datei einlesen** mit dem passenden Tool (siehe Tabelle oben)
2.  **Bei großen Dateien** (>100 Seiten): In Abschnitten lesen mit `offset`/`limit`
3.  **Bei Bildern/Diagrammen**: Visuelle Inhalte beschreiben und als Wissensblöcke aufnehmen
4.  **Rohtext zwischenspeichern** in `/home/user/workspace/distiller_temp/` falls nötig

### Phase 2: Wissensanalyse

Analysiere den Rohinhalt und identifiziere:

1.  **Kernkonzepte** — Was sind die zentralen Ideen/Begriffe?
2.  **Entitäten** — Personen, Organisationen, Technologien, Frameworks, Standards
3.  **Beziehungen** — Wie hängen Konzepte zusammen? (nutzt X, basiert auf Y, widerspricht Z)
4.  **Kernaussagen** — Was sind die wichtigsten Thesen/Fakten?
5.  **Taxonomie & Cluster** — Welche Hierarchie haben die Konzepte? Welche Themencluster bilden sich?
6.  **Redundanzen** — Welche Ideen werden mehrfach ausgedrückt? → Zusammenführen
7.  **Confidence-Einschätzung** — Wie gut belegt ist jede Kernaussage im Quelldokument?
8.  **Zeitbezug** — Von wann stammt das Wissen? Welcher Bezugszeitraum? Gibt es Vergleiche mit Vorperioden? Ist Wissen zeitlos oder zeitgebunden?
9.  **Räumlicher Bezug** — Welcher geografische Geltungsbereich? Welche spezifischen Orte sind relevant?
10. **Herkunft & Autorität** — Wer ist die Quelle? Wie vertrauenswürdig ist diese Quelle für das gegebene Wissen?
11. **Begründungslogik** — Warum existiert eine Beziehung? Warum ist ein Fakt wahr oder relevant? Welche Argumente stützen Aussagen?

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
spatial_scope: [Global|EU|USA|APAC|Germany|null — Übergeordneter geografischer Bezug]
spatial_confidence: [explicit|inferred|unknown]
source_entity: [Entität, die das Original erzeugt hat, z.B. "EU-Kommission"]
authoritative_source: [high|medium|low]
provenance_confidence: [explicit|inferred|unknown]
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
  - → nutzt: [[Konzept B]] (Begründung: Weil System X abgelöst wird)
  - → ermöglicht: [[Konzept C]] (Begründung: Dadurch können neue Märkte erschlossen werden)
  - ↔ steht in Spannung mit: [[Konzept D]] (Begründung: Trade-off zwischen Kosten und Sicherheit)
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

📊 Confidence: `high` | 🏷️ Cluster: [Clustername] | 📅 Stand: [Zeitbezug, z.B. "FY2024" oder "2024-Q3"] | 🌍 Spatial: [Scope, z.B. "EU" oder "Global"] | 🏛️ Provenance: [Source/Entity, z.B. "EU-Kommission"]

**Was:** [Klare, eigenständige Definition — verständlich ohne Quelldokument]

**Warum relevant:** [Einordnung, Bedeutung]

**Zeitbezug:** Gültig ab [valid_from] · Quelle: [source_period/source_date]
**Räumlicher Bezug:** Geltungsbereich: [spatial_scope] · Orte: [Ort 1, Ort 2]
**Herkunft:** Erzeugt von: [source_entity] · Autorität: [authoritative_source]

**Beziehungen:**
- Nutzt → [[Anderes Konzept]] (Begründung: Um Funktionalität X zu erreichen)
- Basiert auf → [[Fundament]] (Begründung: Um Stabilität Y zu gewährleisten)

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
- **Räumlicher Bezug**: Jeder Block MUSS eine 🌍 Spatial-Angabe haben, wenn relevant.
- **Herkunft**: Jeder Block MUSS eine 🏛️ Provenance-Angabe haben, wenn relevant.

#### 4.4 Fakten & Daten (Optional)

```markdown
## Fakten & Daten

| Fakt | Wert | Kontext | Stand | Spatial | Provenance | Begründung | Confidence |
|------|------|---------|-------|---------|------------|------------|------------|
| [Was] | [Zahl/Datum] | [Wozu relevant] | [Zeitbezug, z.B. FY2024] | [Scope] | [Source] | [Warum/Wie] | high/medium/low |
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
> Quell-Geltungsbereich: [spatial_scope]
> Quell-Autorität: [source_entity] ([authoritative_source])
> Destillationsmethode: Knowledge Distiller Skill v4.0
> Companion: `[name].knowledge.json`
```

### Phase 5: JSON-Ausgabe schreiben (`.knowledge.json`) — v3.0 + Temporal v3.1 + Context Graph v4.0

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
    "source_period": "kd:sourcePeriod",
    "spatial": "kd:spatial",
    "spatial_scope": "kd:spatialScope",
    "locations": "kd:locations",
    "provenance": "kd:provenance",
    "source_entity": "kd:sourceEntity",
    "authoritative_source": "kd:authoritativeSource",
    "reasoning": "kd:reasoning"
  },
  "metadata": {
    "title": "[Destillierter Titel]",
    "source": "[Originaldateiname]",
    "source_type": "[PDF|DOCX|...]",
    "source_date": "[ISO-Datum der Publikation, z.B. 2024-03-15, oder null]",
    "source_period": "[Bezugszeitraum, z.B. FY2024 oder 2024-Q3 oder 2020/2024, oder null]",
    "temporal_confidence": "explicit|inferred|unknown",
    "spatial_scope": "[Global|EU|USA|APAC|Germany|null]",
    "spatial_confidence": "explicit|inferred|unknown",
    "source_entity": "[Name der Quelle/Autorität, z.B. 'EU-Kommission']",
    "authoritative_source": "high|medium|low",
    "provenance_confidence": "explicit|inferred|unknown",
    "distilled": "[ISO-Datum der Destillation]",
    "domain": "[Wissensdomäne]",
    "confidence": "[high|medium|low]",
    "companion": "[name].knowledge.md",
    "schema_version": "4.0"
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
      "spatial": {
        "spatial_scope": "[Global|EU|USA|APAC|Germany|null — überschreibt Metadata wenn Block-spezifisch]",
        "locations": ["[Ort A]", "[Ort B]"],
        "spatial_confidence": "explicit|inferred|unknown"
      },
      "provenance": {
        "source_entity": "[Name der Quelle/Autorität, z.B. 'EU-Kommission' — überschreibt Metadata wenn Block-spezifisch]",
        "authoritative_source": "high|medium|low",
        "provenance_confidence": "explicit|inferred|unknown"
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
      "confidence": "high|medium|low",
      "reasoning": "[Kurze, natürlichsprachige Begründung für diese Beziehung, oder null]"
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
      "spatial_scope": "[Geltungsbereich des Fakts, z.B. EU]",
      "locations": ["[Ort A]"],
      "source_entity": "[Quelle des Fakts]",
      "authoritative_source": "high|medium|low",
      "reasoning": "[Warum dieser Fakt relevant ist oder warum er so ist]",
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
      "spatial_scope": "[Räumlicher Bezug, oder null]",
      "source_entity": "[Quelle des Chunks, oder null]",
      "embedding_text": "[Clean-Text-Version des Wissensblocks — OHNE Markdown-Syntax, OHNE Emoji, OHNE Pfeile, OHNE Wikilinks. Nur reiner Fließtext, optimiert für Embedding-Modelle. Enthält: Zeitbezug + Räumlicher Bezug + Provenance + Definition + Relevanz + alle Kernaussagen als natürlicher Absatz. Der Zeit-, Raum- und Provenance-Bezug sowie Begründungen MÜSSEN als natürliche Sätze am Anfang stehen, z.B. 'Stand Geschäftsjahr 2024, gültig in der EU, laut EU-Kommission, weil neue Regulierungen in Kraft traten: Der Umsatz betrug...']"
    }
  ]
}
```

**Regeln für die JSON-Datei:**

1.  **Nodes müssen reich sein**: Jeder Node enthält `definition`, `relevance`, `statements` — nicht nur `id` und `label`. Dies macht den Graphen ohne die Markdown-Datei verwendbar.
2.  **Edges haben Gewichtung**: `weight` (0.0-1.0) gibt die Stärke der Beziehung an. Kern-Beziehungen = 1.0, periphere = 0.5.
3.  **Edge-Confidence**: Jede Kante hat eine eigene Confidence-Angabe, unabhängig von den Nodes.
4.  **Chunks sind Embedding-ready**: Die `chunks`-Sektion enthält Clean-Text-Versionen jedes Wissensblocks:
    *   KEIN Markdown (`**`, `###`, `-`, `>`)
    *   KEINE Emoji (`📊`, `🏷️`, `💡`)
    *   KEINE Pfeile (`→`, `↔`)
    *   KEINE Wikilinks (`[[...]]`)
    *   NUR natürlicher Fließtext als zusammenhängender Absatz
    *   Enthält: Definition + Relevanz + Kernaussagen + optionale Einordnung
    *   Direkt verwendbar als Input für Embedding-Modelle (OpenAI, Cohere, etc.)
5.  **JSON-LD `@context`**: Macht die Datei kompatibel mit semantischen Web-Standards. Der `kd:`-Namespace ist ein Platzhalter — bei Bedarf ersetzbar durch eine echte Ontologie-URI.
6.  **Konsistenz mit Markdown**: Jeder Node in JSON muss einem Wissensblock in Markdown entsprechen. Jede Edge muss einer Beziehung in der Concept Map entsprechen. `definition` = `Was:`, `relevance` = `Warum relevant:`, `statements` = `Kernaussagen:`.
7.  **Temporale, Räumliche und Provenance-Felder sind Pflicht auf Node-Ebene**: Jeder Node MUSS ein `temporal`, `spatial` und `provenance` Objekt haben. Wenn der Block denselben Kontext wie die Metadata hat, können die entsprechenden Felder im Node `null` sein (erbt von Metadata). Wenn der Block einen anderen Kontext hat (z.B. Vorjahresdaten oder regionale Details in einem aktuellen globalen Bericht), MUSS der Node eigene Werte setzen.
8.  **Embedding-Chunks enthalten den vollen Kontext**: Der `embedding_text` MUSS den Zeitbezug, räumlichen Bezug, Provenance und relevante Begründungen als natürlichsprachigen Satz enthalten, z.B. "Stand Geschäftsjahr 2024, gültig in der EU, laut EU-Kommission: Der Umsatz betrug... weil neue Regulierungen in Kraft traten.". Ohne diesen Kontext im Chunk-Text kann ein Embedding-basiertes System nicht zwischen alten/neuen, globalen/regionalen oder hoch/weniger-autoritativen Informationen unterscheiden.
9.  **Fakten haben eigenen Kontext**: Jeder Fakt in `facts` hat eigene `source_period` / `valid_from` / `valid_until`, `spatial_scope` / `locations`, `source_entity` / `authoritative_source` und `reasoning` Felder. Das ist besonders wichtig bei Kennzahlen, die sich jährlich/regional ändern oder unterschiedliche Begründungen haben.
10. **Edges haben Begründung**: Jede `edge` MUSS ein `reasoning`-Feld enthalten (darf `null` sein, wenn keine Begründung explizit oder implizit erkennbar ist).

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
| **Räumlicher Bezug vorhanden** | Hat jeder relevante Block eine 🌍 Spatial-Angabe? Ist spatial_scope im Frontmatter gesetzt? |
| **Provenance vorhanden** | Hat jeder relevante Block eine 🏛️ Provenance-Angabe? Sind source_entity/authoritative_source im Frontmatter gesetzt? |
| **Begründungen vorhanden** | Haben wichtige Beziehungen und Fakten Begründungen (im Concept Map und Fakten-Tabelle)? |

**JSON-Qualität:**

| Kriterium | Prüfung |
|-----------|---------|
| **Valides JSON** | Ist die Datei syntaktisch korrekt? (Prüfe mit `python -m json.tool`) |
| **Node-Reichhaltigkeit** | Hat jeder Node `definition`, `relevance`, `statements`, `temporal`, `spatial`, `provenance`? |
| **Graph-Konsistenz** | Stimmen Nodes/Edges mit der Markdown Concept Map überein? |
| **Chunk-Sauberkeit** | Enthalten Chunks KEIN Markdown, keine Emoji, keine Syntax-Artefakte? |
| **Bidirektionale Referenz** | Verlinken `.md` und `.json` aufeinander via `companion`-Feld? |
| **Temporale Konsistenz** | Hat jeder Node ein `temporal`-Objekt? Sind `source_date`/`source_period` in Metadata gesetzt? Enthält jeder Embedding-Chunk den Zeitbezug als natürlichen Satz? |
| **Räumliche Konsistenz** | Hat jeder relevante Node ein `spatial`-Objekt? Ist `spatial_scope` in Metadata gesetzt? Enthält jeder Embedding-Chunk den räumlichen Bezug als natürlichen Satz? |
| **Provenance Konsistenz** | Hat jeder relevante Node ein `provenance`-Objekt? Sind `source_entity`/`authoritative_source` in Metadata gesetzt? Enthält jeder Embedding-Chunk die Provenance als natürlichen Satz? |
| **Begründungs-Konsistenz** | Hat jede relevante Edge ein `reasoning`-Feld? Enthält jeder Embedding-Chunk relevante Begründungen als natürlichen Satz? |

Falls ein Kriterium nicht erfüllt ist → überarbeiten vor dem Speichern.

**CRITICAL**: Prüfe die JSON-Datei immer mit:
```bash
python -c "import json; json.load(open('[datei].knowledge.json')); print('Valid JSON')"
```

### Phase 7: Speichern und Übergeben

1.  Speichere die Markdown-Datei als `[name].knowledge.md`
2.  Speichere die JSON-Datei als `[name].knowledge.json`
3.  Verwende `share_file` um BEIDE Dateien dem User zu übergeben
4.  Lösche temporäre Dateien in `distiller_temp/` falls vorhanden

## Batch-Modus: Mehrere Dateien

Wenn mehrere Dateien gleichzeitig destilliert werden sollen:

1.  Jede Datei bekommt ein eigenes Paar: `.knowledge.md` + `.knowledge.json`
2.  Zusätzlich: `_index.knowledge.md` + `_index.knowledge.json` die alle Dateien verknüpfen

## Spezialbehandlung nach Dateityp

### Tabellen / XLSX / CSV
- Nicht die ganze Tabelle kopieren
- Stattdessen: Was sagt die Tabelle aus? Muster, Trends, Ausreißer
- Nur Kerndaten als Fakten-Tabelle aufnehmen, mit allen Kontext-Dimensionen

### Präsentationen / PPTX
- Folientitel ≠ Wissensstruktur. Reihenfolge ignorieren wenn nötig.
- Kernbotschaften pro Folie extrahieren und nach Konzepten neu gruppieren
- Speaker Notes einbeziehen (oft wertvoller als Folieninhalt)

### Bilder / Diagramme
- Visuellen Inhalt beschreiben
- Architekturdiagramm: Komponenten und Beziehungen extrahieren, ggf. Begründungen
- Chart: Kernaussage und Trend extrahieren, mit Zeit- und Raumbezug

### Code / JSON
- Nicht den Code kopieren
- Welche Konzepte implementiert der Code? Welche Patterns? Welche Entscheidungen wurden getroffen (Begründung)?
- API-Strukturen → Entitäten und Beziehungen extrahieren

### Webseiten / HTML
- Werbe- und Navigationselemente ignorieren
- Nur den inhaltlichen Kern destillieren, dabei auf `source_entity` (Impressum), `source_date` und `spatial_scope` (Sprache, Inhalt) achten.

## Hinweise

- **Sprache**: Schreibe in der Sprache des Quelldokuments. Wenn gemischt → Hauptsprache verwenden, Fachbegriffe in Originalsprache.
- **Umfang**: Die Knowledge-Datei sollte 20-40% der Länge des Originals haben.
- **Verlinkung**: `[[Konzeptname]]` Syntax ist Obsidian-kompatibel.
- **Confidence**: Pro Block differenzieren, nicht pauschal.
- **Cluster**: 3-7 Cluster sind ideal. Bei sehr kleinen Dokumenten (< 5 Konzepte) können Cluster entfallen.
- **JSON-Validierung**: Immer syntaktisch prüfen vor dem Übergeben.
- **Chunks**: Die Clean-Text-Chunks in der JSON sollen wie ein natürlicher Absatz klingen, nicht wie eine Aufzählung.
- **Vollständiger Kontext**: JEDE Destillation braucht `source_date`/`source_period`, `spatial_scope`, `source_entity`. Im Zweifelsfall `temporal_confidence`, `spatial_confidence` oder `provenance_confidence` auf `"inferred"` setzen statt den Kontext wegzulassen. Ein Graph ohne Kontext-Dimensionen ist für Multi-Dokument-Szenarien unbrauchbar.
