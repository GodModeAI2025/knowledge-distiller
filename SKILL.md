---
name: "Knowledge Distiller"
description: "Transformiert Dokumente in strukturierte Wissensgraphen mit temporaler Dimension, Multi-Agent-Architektur und inkrementeller Verarbeitung."
when_to_use: >
  Verwende diesen Skill wenn der Benutzer ein Dokument, eine Datei, eine URL oder mehrere Quellen
  in einen strukturierten Wissensgraphen transformieren möchte. Auch bei Anfragen wie
  'extrahiere das Wissen aus...', 'erstelle einen Knowledge Graph', 'destilliere...',
  'was sind die Kernkonzepte in...', 'analysiere die Beziehungen zwischen...',
  'fasse das Wissen zusammen als Graph', 'Obsidian-kompatible Wissensbasis erstellen'.
  NICHT verwenden für einfache Zusammenfassungen oder Transkriptionen.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - WebFetch
  - Agent
  - Glob
  - Grep
  - TodoWrite
context: fork
model: opus
arguments:
  source: "Dateipfad, URL oder Ordnerpfad der zu verarbeitenden Quelle(n)"
  depth: "Verarbeitungstiefe: quick (Überblick), standard (vollständig), deep (mit Inferenz)"
  format: "Ausgabeformat: md, json, both, all (inkl. Mermaid, Cypher, CTXT, HTML-Viewer), bundle (Ordner: eine Datei pro Konzept)"
  language: "Ausgabesprache: de, en (Default: Sprache des Quelldokuments)"
  mode: "Verarbeitungsmodus: fresh (neu), merge (mit bestehendem Graph), batch (Ordner)"
version: "4.0"
spec_version: "1.0"
---

# Knowledge Distiller v4.0

> **Leitprinzip:** Eine Datei ist ein Behälter. Das Wissen darin ist wertvoll.

> **Kanonischer Vertrag:** Das Ausgabeformat ist verbindlich in [`SPEC.md`](SPEC.md) definiert
> und maschinell prüfbar über [`schema/knowledge.schema.json`](schema/knowledge.schema.json).
> Bei Widerspruch zwischen dieser Anleitung und `SPEC.md` **gilt `SPEC.md`.** Abgeleitete
> Artefakte (Zähler, Concept Map, Mermaid, `index.md`, `quality_score`) werden **deterministisch
> per Skript** erzeugt, nicht vom Modell von Hand (siehe `scripts/`).

## Was dieser Skill NICHT tut
- 1:1-Transkripte erstellen
- Copy-Paste mit Markdown-Formatierung
- Inhaltsverzeichnisse rekonstruieren
- Einfache Zusammenfassungen generieren

## Was dieser Skill tut
- Atomare Konzepte identifizieren und eigenständig beschreiben
- Beziehungen zwischen Konzepten explizit machen
- Redundanzen eliminieren — identische Ideen = ein Wissensblock
- Wissensstrukturen bauen, die ohne Quelldokument verständlich sind
- Concept Maps als Navigationsübersicht generieren
- Konzepte in thematische Cluster gruppieren
- Konfidenz-Scores pro Wissensblock vergeben
- Dual-Format-Output produzieren (`.knowledge.md` + `.knowledge.json`)
- Graph-Knoten mit Kernaussagen anreichern
- Embedding-optimierte Chunks ohne Markdown-Artefakte erzeugen
- **v3.1:** Temporale Dimensionen — Zeitbezüge pro Wissensblock, Fakt und Kante
- **v4.0 NEU:** Multi-Agent-Distillation für parallele Tiefenanalyse
- **v4.0 NEU:** Inkrementelle Graph-Evolution (monotoner Merge, Delta, Multi-Source)
- **v4.0 NEU:** Context-Management für Dokumente jeder Größe
- **v4.0 NEU:** Erweiterte Ausgabeformate (Mermaid, Cypher, CTXT, Canvas, HTML)
- **v4.0 / Spec 1.0 NEU:** Formale, maschinen-prüfbare Spezifikation (`SPEC.md` + JSON-Schema)
- **v4.0 / Spec 1.0 NEU:** Deterministische Validierung per Skript statt LLM-Selbstbewertung
- **v4.0 / Spec 1.0 NEU:** Quellenbelege pro Aussage (`[n]`-Marker + `## Quellen`), `resource`-Identität
- **v4.0 / Spec 1.0 NEU:** Skalierbares Bundle-Format (eine Datei pro Konzept + `index.md`)
- **v4.0 / Spec 1.0 NEU:** Self-contained HTML-Viewer aus dem Graphen

---

## Phase 0: Initialisierung & Modus-Erkennung

Bevor die Distillation beginnt, bestimme den Verarbeitungsmodus:

### Modus-Erkennung
| Situation | Modus | Verhalten |
|-----------|-------|-----------|
| Neue Datei, kein bestehender Graph | `fresh` | Vollständige Distillation |
| Neue Datei + bestehender `.knowledge.json` im selben Ordner | `merge` | Graph laden, anreichern, Delta berechnen |
| Ordnerpfad als Quelle | `batch` | Alle Dateien im Ordner sequentiell verarbeiten, dann Gesamt-Graph mergen |
| URL als Quelle | `fresh` | Inhalt fetchen, dann wie Datei verarbeiten |

### Tiefensteuerung (`depth`)
| Stufe | Verhalten | Empfohlen für |
|-------|-----------|---------------|
| `quick` | Nur Konzepte + Concept Map, max. 10 Kernwissen-Einträge | Ersteinschätzung, kurze Dokumente |
| `standard` | Vollständige Distillation mit allen Outputs | Standard-Dokumente (< 50 Seiten) |
| `deep` | Zusätzlich: Inferenz fehlender Beziehungen, offene Fragen, Cross-Domain-Links | Komplexe Dokumente, wissenschaftliche Arbeiten |

---

## Phase 1: Multi-Agent-Distillation-Pipeline

### Architektur-Übersicht

```
Quelle → [Extraction-Agent]
                ↓
    ┌───────────┼───────────┐
    ↓           ↓           ↓
[Concept]  [Relationship] [Temporal]    ← parallel
    ↓           ↓           ↓
    └───────────┼───────────┘
                ↓
        [Synthesis-Agent]
                ↓
        [Validation-Agent]
                ↓
        Ausgabe-Dateien
```

### Agent-Definitionen

#### 1. Extraction-Agent
**Aufgabe:** Rohtext aus Quelldokument extrahieren.
**Tools:** Read, Bash (für PDF/DOCX-Konvertierung), WebFetch (für URLs)
**Fallback-Strategie:**
- PDF → `pdftotext` oder `python -c "import fitz; ..."`
- DOCX → `python -c "import docx; ..."`
- PPTX → `python -c "from pptx import Presentation; ..."`
- XLSX/CSV → Read + Bash
- Bilder → Multimodal-Analyse (Bild direkt an Claude)
- URL → WebFetch
- Falls Tool nicht verfügbar → nächsten Fallback versuchen

**Output:** Rohtext als temporäre Datei `.extraction.tmp.md`

#### 2. Concept-Agent
**Aufgabe:** Atomare Konzepte identifizieren und beschreiben.
**Input:** Extrahierter Rohtext
**Output:** Liste atomarer Konzepte mit:
- Eindeutige ID (kebab-case)
- Label (menschenlesbar)
- Definition (1-2 Sätze, eigenständig verständlich)
- Relevanz (warum wichtig für die Praxis)
- Cluster-Zuordnung
- Konfidenz (high/medium/low)
- `resource` (kanonische Identitäts-URI des bezeichneten Dings, falls vorhanden — dient als
  Dedup-Schlüssel über Quellen hinweg; sonst `null`)

**Qualitätsregeln:**
- Jedes Konzept MUSS ohne Quelldokument verständlich sein
- Keine Synonyme als separate Konzepte
- Minimum 10 Konzepte bei `standard`, 5 bei `quick`, 20+ bei `deep`

**Vier-Gate-Test für eigenständige Wissens-Einheiten** (verhindert Rauschen, fördert DRY):
Lege ein eigenes Konzept (statt es in einem anderen zu vergraben) nur an, wenn ALLE gelten:
1. **Referenzierbar:** Es ist per Name benennbar (eine Metrik, eine Formel, eine Definition,
   ein Verfahren) — nicht bloß ein Abschnittstitel.
2. **Nicht Meta:** Der Slug steht NICHT auf der Denylist (`overview`, `introduction`,
   `getting-started`, `summary`, `conclusion`, `misc`, `notes`).
3. **Belegbar:** Es lässt sich mit mindestens einer Quelle belegen.
4. **Wiederverwendung ≥ 2:** Es wird an mehr als einer Stelle gebraucht → einmal als Konzept
   anlegen und mehrfach verlinken, statt es zu wiederholen.
Im Zweifel: **weglassen** (lieber in ein bestehendes Konzept einbetten).

#### 3. Relationship-Agent
**Aufgabe:** Beziehungen zwischen Konzepten kartieren.
**Input:** Extrahierter Rohtext + Konzept-Liste vom Concept-Agent
**8 Beziehungstypen:**

| Typ | Symbol | Bedeutung | Beispiel |
|-----|--------|-----------|----------|
| `uses` | `→` | Abhängigkeit | Skill → Tool |
| `enables` | `→` | Kausalität | Training → Kompetenz |
| `based-on` | `→` | Fundament | Strategie → Analyse |
| `part-of` | `→` | Komposition | Kapitel → Buch |
| `tension` | `↔` | Spannung/Widerspruch | Sicherheit ↔ Usability |
| `replaces` | `→` | Ablösung | v2 → v1 |
| `extends` | `→` | Erweiterung | Plugin → Core |
| `example-of` | `→` | Instanziierung | "GPT-4" → "LLM" |

**Output:** Kanten-Liste mit source, target, type, label, weight (0-1.0), confidence

#### 4. Temporal-Agent
**Aufgabe:** Zeitliche Dimensionen jedes Konzepts und jeder Kante analysieren.
**Drei Zeitebenen:**

| Ebene | Feld | Bedeutung |
|-------|------|-----------|
| Quelldatum | `source_date` / `source_period` | Wann das Wissen entstanden ist |
| Gültigkeitsfenster | `valid_from` / `valid_until` | Wann die Information gilt |
| Extraktionsdatum | `distillation_date` | Wann destilliert wurde |

**Zeitformate (ISO 8601 erweitert):**
- Exakt: `2024-12-31`
- Monat: `2024-12`
- Quartal: `2024-Q4`
- Jahr: `2024`
- Geschäftsjahr: `FY2024`
- Intervall: `2020/2024`

**Temporale Konfidenz:**
- `explicit` — direkt im Text genannt
- `inferred` — aus Kontext abgeleitet
- `unknown` — nicht bestimmbar

**Warum das kritisch ist:**
> Ohne temporale Zuordnung werden widersprüchliche Fakten wie "28 Mrd. Euro" und "34 Mrd. Euro"
> zu Datenkonflikten statt zu Zeitreihen-Trends.

#### 5. Synthesis-Agent
**Aufgabe:** Ergebnisse der drei parallelen Agenten zusammenführen.
**Schritte:**
1. Konzepte mit Beziehungen und Zeitdaten anreichern
2. Redundanzen eliminieren (gleiches Konzept aus verschiedenen Agenten)
3. Cluster kohärenz prüfen und optimieren
4. Concept Map generieren
5. Embedding-Chunks erzeugen (ohne Markdown-Syntax)
6. Fakten-Tabelle konsolidieren
7. Offene Fragen sammeln (bei `deep` Modus)

#### 6. Validation-Agent (deterministisch, skriptbasiert)
**Aufgabe:** Maschinelle Qualitätssicherung. **Das Modell bewertet sich NICHT selbst** —
die Prüfung läuft in Code, damit das Format erzwungen und der Score reproduzierbar ist.

**Ablauf (zwingend in dieser Reihenfolge):**
1. Abgeleitete Felder berechnen lassen — Zähler, Cluster-Mitgliedschaft, kanonische Kanten-IDs,
   `quality_score`:
   ```bash
   python3 scripts/build_graph.py <out>.knowledge.json --write
   ```
2. Validieren gegen `SPEC.md` / `schema/knowledge.schema.json`:
   ```bash
   python3 scripts/validate_knowledge.py <out>.knowledge.json
   # bei mode=merge zusätzlich die Monotonie-Garantie prüfen:
   python3 scripts/validate_knowledge.py --prev <out>.knowledge.v1.json <out>.knowledge.json
   ```
3. Das Skript trennt **ERRORS** (nicht-konform → MUSS behoben werden) von **WARNINGS**
   (konform, sollte behoben werden) und gibt `quality_score = 100 − 10·Fehler − 2·Warnungen`
   aus (siehe `SPEC.md` §9). Dieser Score wird ins Frontmatter beider Outputs übernommen —
   nicht ein vom Modell geschätzter Wert.

**Self-Healing:**
Solange das Skript ERRORS meldet → die *genannten* Fehler beheben (fehlende Pflichtfelder,
unaufgelöste Kanten/Quellen, Zähler-Abweichungen, Syntax-Ballast in Chunks, Zitat-Marker außer
Reichweite), dann `build_graph.py --write` + `validate_knowledge.py` erneut ausführen. Maximal
2 Korrektur-Iterationen; danach verbleibende ERRORS dem Nutzer berichten statt verstecken.

> Wenn `validate_knowledge.py` mit 0 ERRORS endet, ist das Dokument **konform** (Exit-Code 0).
> Das optionale `jsonschema`-Paket aktiviert zusätzlich die Schema-Ebene; ohne es laufen die
> strukturellen Prüfungen trotzdem (reine Standardbibliothek).

---

## Phase 2: Context-Management für große Dokumente

### Chunked Processing
Dokumente die das Context-Fenster überschreiten werden in semantische Abschnitte geteilt:

1. **Erkennung:** Dokument > 30.000 Wörter → Chunked Processing aktivieren
2. **Segmentierung:** Teile an natürlichen Grenzen (Kapitel, H1/H2-Überschriften, Seitenumbrüche)
3. **Progressive Distillation:**
   - Jedes Segment einzeln destillieren → Segment-Graph
   - Alle Segment-Graphen mergen → Gesamt-Graph
   - Cross-Segment-Beziehungen im Merge-Schritt identifizieren
4. **Summary-Caching:** Zwischen-Ergebnisse als `.tmp`-Dateien im Ausgabeordner speichern
5. **Aufräumen:** Nach erfolgreicher Fertigstellung alle `.tmp`-Dateien löschen

### Context-Budget
- Pro Agent-Durchlauf maximal ~80.000 Token Input
- Bei Überschreitung: automatisch in Chunks teilen
- Zwischen-Ergebnisse in Dateien auslagern, nicht im Konversations-Context halten

---

## Phase 3: Inkrementelle Graph-Evolution

### Merge-Modus — anreichernd, nie zerstörend (Monotonie-Vertrag, `SPEC.md` §6)
Wenn ein bestehender `.knowledge.json` im Ausgabeordner existiert:

1. **Bestehenden Graph laden** und parsen.
2. **Identität auflösen über `resource`** (falls vorhanden), sonst über `id`. Dasselbe Konzept
   unter anderer `id` aber gleicher `resource` wird **vereinigt, nicht geforkt** (kein `-v2`).
3. **Anreichern statt überschreiben:** `statements`, `sources` und `citations` werden
   **vereinigt**. Kein Knoten, keine Kante, kein Statement, kein Zitat und kein Fakt aus dem
   Vorgänger-Graph darf entfernt werden. Der gemergte Graph darf **nie weniger** Knoten,
   Kanten, Fakten oder Zitate haben als zuvor.
4. **Widersprüche werden zur Zeitreihe, nicht aufgelöst:** Liefern zwei Fakten zum selben
   Konzept+Kennzahl verschiedene `value`s → **beide behalten** (verschiedene `temporal`-Perioden)
   und eine `tension`-Kante setzen; wenn einer den anderen ablöst, eine `replaces`-Kante mit
   `valid_until` auf dem abgelösten Eintrag. (Damit wird „Zeitreihe statt Widerspruch" real.)
5. **Versionierung:** Vorherigen Graph als `.knowledge.v{N}.json` archivieren (max. 5).
6. **Delta-Output:** `.knowledge.diff.md` mit nur den Änderungen.
7. **Erzwingen:** Der Merge wird mit
   `python3 scripts/validate_knowledge.py --prev <vorher>.json <neu>.json` geprüft — schrumpft
   der Graph, schlägt die Validierung fehl (ERROR). Der Monotonie-Vertrag ist Code, nicht Vorsatz.

### Batch-Modus
Bei Ordner-Eingabe:
1. Alle unterstützten Dateien im Ordner erkennen
2. Nach Änderungsdatum sortieren (älteste zuerst)
3. Sequentiell verarbeiten mit Merge nach jeder Datei
4. Fortschritt per TodoWrite tracken
5. Finalen Gesamt-Graph ausgeben

### Versionshistorie
- Bei jedem Merge: vorherigen Graph als `.knowledge.v{N}.json` archivieren
- Maximale Historie: 5 Versionen (ältere werden überschrieben)

---

## Phase 4: Output-Generierung

### 4.1 Markdown-Output (`.knowledge.md`)

> **Wird deterministisch erzeugt:** `python3 scripts/build_md.py <out>.knowledge.json`.
> Das Modell schreibt das Wissen in die `.knowledge.json`; die `.md` (Frontmatter, Concept Map,
> Kernwissen, Mermaid, Fakten, Quellen) wird daraus gerendert — so driften `.md` und `.json` nie
> auseinander (`SPEC.md` §5/§8).

#### Frontmatter-Block
```yaml
---
title: "{Titel des Wissensgraphen}"
distiller_version: "4.0"
distiller_spec_version: "1.0"
distillation_date: "{ISO 8601}"
domain: "{Fachgebiet}"
language: "{de|en}"
depth: "{quick|standard|deep}"
mode: "{fresh|merge|batch}"
quality_score: {0-100}          # aus validate_knowledge.py, NICHT geschätzt
temporal_confidence: "{explicit|inferred|unknown}"
concept_count: {N}              # = len(nodes), per build_graph.py
relationship_count: {N}         # = len(edges)
cluster_count: {N}              # = len(clusters)
sources:
  - id: "s1"                    # Zitat-Nummer [1] = 1. Quelle, [2] = 2. Quelle, ...
    file: "{Dateiname}"
    type: "{pdf|docx|…|url}"
    date: "{ISO 8601}"
    url: "{URL oder null}"
clusters:
  "{cluster-id}":
    label: "{Cluster-Name}"
    concepts: ["{concept-1}", "{concept-2}", ...]
---
```

#### Concept Map
```
## Concept Map

{concept-a} → uses: [[{concept-b}]]
{concept-a} → enables: [[{concept-c}]]
{concept-b} ↔ tension with: [[{concept-d}]]
{concept-c} → part-of: [[{concept-e}]]
{concept-d} → replaces: [[{concept-f}]]
...
```

Beziehungstypen in der Map:
- `→ uses:` (Abhängigkeit)
- `→ enables:` (Kausalität)
- `→ based-on:` (Fundament)
- `→ part-of:` (Komposition)
- `↔ tension with:` (Spannung)
- `→ replaces:` (Ablösung)
- `→ extends:` (Erweiterung)
- `→ example-of:` (Instanziierung)

#### Kernwissen-Blöcke
```
### {Konzept-Label}

- **Konfidenz:** {high|medium|low}
- **Cluster:** {cluster-label}
- **Definition:** {1-2 Sätze, eigenständig verständlich}
- **Warum relevant:** {Auswirkung auf Praxis}
- **Zeitbezug:** {natürlichsprachlich, z.B. "Gültig seit Q3 2024"}
- **Beziehungen:**
  - → uses: [[{anderes-konzept}]]
  - → enables: [[{weiteres-konzept}]]
- **Kernaussagen:**
  - {Aussage 1 — spezifisch, überprüfbar, mit Beispiel}
  - {Aussage 2}
  - {Aussage 3}
  - {Aussage 4 — optional bei deep}
  - {Aussage 5 — optional bei deep}
```

#### Mermaid-Diagramm (NEU v4.0)
```
## Wissensgraph (Mermaid)

```mermaid
graph LR
    concept-a[Konzept A] -->|uses| concept-b[Konzept B]
    concept-a -->|enables| concept-c[Konzept C]
    concept-b <-->|tension| concept-d[Konzept D]
    ...
```​
```

#### Fakten & Daten
```
## Fakten & Daten

| Fakt | Wert | Zeitbezug | Konfidenz | Quelle |
|------|------|-----------|-----------|--------|
| {Beschreibung} | {Zahl/Aussage} | {Zeitraum} | {high/medium/low} | [{n}] |
```
Die `Quelle`-Spalte verweist mit `[n]` auf die nummerierte Quellenliste (`## Quellen`).

#### Offene Fragen (bei `deep` Modus)
```
## Offene Fragen

1. {Unbeantwortete Frage, die sich aus der Analyse ergibt}
2. {Weitere Frage}
```

#### Embedding-Chunks
```
## Chunks (Embedding-optimiert)

> {Chunk 1: Natürlichsprachlicher Absatz ohne Markdown-Syntax, Emojis, Pfeile oder Wikilinks.
> Enthält Zeitbezug als natürliche Sprache. Optimiert für semantische Suche.}

> {Chunk 2: ...}
```

#### Quellen (nummerierte Zitatliste — Auflösungsziel für jeden `[n]`-Marker)
```
## Quellen

[1] [{Label oder Dateiname}]({URL oder Pfad}) — {Typ}, {Datum}
[2] ...
```
Jede `Kernaussage` und jeder Fakt SOLL die stützende Quelle mit `[n]` markieren (`n` = Position
in `## Quellen` / `metadata.sources`). `validate_knowledge.py` prüft, dass jeder `[n]` auflösbar
ist, und warnt bei Knoten ganz ohne Beleg.

#### Provenance-Footer
```
---
Destilliert am {Datum} mit Knowledge Distiller v4.0
Quelle(n): {Dateinamen}
Qualitäts-Score: {Score}/100
```

### 4.2 JSON-LD-Output (`.knowledge.json`)

```json
{
  "@context": {
    "@vocab": "https://schema.org/",
    "kd": "https://knowledge-distiller.dev/v4/",
    "nodes": "kd:nodes",
    "edges": "kd:edges",
    "chunks": "kd:chunks",
    "confidence": "kd:confidence",
    "temporal": "kd:temporal",
    "cluster": "kd:cluster",
    "weight": "kd:weight",
    "sources": "kd:sources"
  },
  "metadata": {
    "title": "",
    "distiller_version": "4.0",
    "distiller_spec_version": "1.0",
    "sources": [
      {
        "id": "s1",
        "file": "",
        "type": "",
        "date": "",
        "url": null
      }
    ],
    "distillation_date": "",
    "domain": "",
    "language": "",
    "depth": "",
    "mode": "",
    "quality_score": 0,
    "concept_count": 0,
    "relationship_count": 0,
    "cluster_count": 0,
    "fact_count": 0
  },
  "clusters": [
    {
      "id": "",
      "label": "",
      "description": "",
      "concepts": []
    }
  ],
  "nodes": [
    {
      "id": "",
      "label": "",
      "cluster": "",
      "confidence": "high|medium|low",
      "definition": "",
      "relevance": "",
      "resource": null,
      "statements": [
        "{Aussage, optional mit Beleg-Marker [n]}"
      ],
      "temporal": {
        "source_date": "",
        "source_period": "",
        "valid_from": "",
        "valid_until": "",
        "temporal_confidence": "explicit|inferred|unknown"
      },
      "sources": ["s1"],
      "citations": [
        {"n": 1, "source": "s1", "locator": null, "label": "", "url": null}
      ]
    }
  ],
  "edges": [
    {
      "source": "",
      "target": "",
      "type": "uses|enables|based-on|part-of|tension|replaces|extends|example-of",
      "label": "",
      "weight": 0.0,
      "confidence": "high|medium|low",
      "temporal": {
        "valid_from": "",
        "valid_until": ""
      }
    }
  ],
  "facts": [
    {
      "id": "",
      "statement": "",
      "value": "",
      "temporal": {
        "source_date": "",
        "valid_from": "",
        "valid_until": "",
        "temporal_confidence": ""
      },
      "confidence": "",
      "source": "s1"
    }
  ],
  "chunks": [
    {
      "id": "",
      "text": "",
      "concepts": [],
      "temporal_scope": "",
      "token_estimate": 0
    }
  ]
}
```

### 4.3 Interaktiver Viewer & Bundle (NEU v4.0 / Spec 1.0)

#### Self-contained HTML-Viewer (`.knowledge.html`)
Bei `format: all` aus dem Graphen erzeugen:
```bash
python3 scripts/build_viewer.py <out>.knowledge.json
```
Ergebnis ist eine **einzelne, offline öffenbare** HTML-Datei (Cytoscape-Graph + Detailpane):
Farbe nach Cluster, Knotengröße nach Inhalt, Kantenstärke nach `weight`, abgelöste Knoten
(`valid_until` oder eingehende `replaces`-Kante) ausgegraut, „Cited by"-Backlinks zur Laufzeit.

#### Bundle-Ausgabe (`format: bundle`) — skalierbar, git-freundlich
```bash
python3 scripts/build_bundle.py <out>.knowledge.json -o <name>/
```
Erzeugt einen Verzeichnisbaum mit **einer Datei pro Konzept** plus deterministisch generierten
`index.md`-Manifesten auf jeder Ebene und `# Citations` je Blatt (`SPEC.md` §7). Konzept-ID =
Pfad unter `concepts/<cluster>/` ohne `.md`. Sinnvoll ab großen Korpora (saubere Diffs, atomare
Einzeländerungen, progressive disclosure). Das monolithische Dual-Format bleibt Default.

### 4.4 Weitere Exportformate (NEU v4.0)

#### Neo4j Cypher-Export (`.knowledge.cypher`)
Nur bei `format: all` generieren:
```cypher
// Knoten
CREATE (n:Concept {id: '{id}', label: '{label}', cluster: '{cluster}', confidence: '{confidence}', definition: '{definition}'});

// Kanten
MATCH (a:Concept {id: '{source}'}), (b:Concept {id: '{target}'})
CREATE (a)-[r:{TYPE} {weight: {weight}, confidence: '{confidence}'}]->(b);
```

#### CTXT-Format (`.knowledge.ctxt`)
Nur bei `format: all` generieren. Cognigy-kompatible Knowledge-Chunks:
```
title: {Konzept-Label}
---
{Chunk-Text ohne Markdown-Syntax}
===
title: {Nächstes Konzept}
---
{Chunk-Text}
```

#### Obsidian Canvas (`.knowledge.canvas`)
Nur bei `format: all` generieren. JSON-Struktur für Obsidian Canvas:
```json
{
  "nodes": [
    {"id": "", "type": "text", "text": "", "x": 0, "y": 0, "width": 250, "height": 60, "color": ""}
  ],
  "edges": [
    {"id": "", "fromNode": "", "toNode": "", "label": ""}
  ]
}
```

---

## Phase 5: Hook-Konfiguration (optional)

### Empfohlene Hooks für settings.json

```json
{
  "hooks": {
    "post-tool": [
      {
        "description": "Graph nach Schreibung gegen SPEC/Schema validieren",
        "matcher": {
          "tool_name": "Write",
          "file_pattern": "*.knowledge.json"
        },
        "command": "python3 scripts/validate_knowledge.py \"$TOOL_INPUT_FILE_PATH\" --quiet"
      }
    ]
  }
}
```

### Auto-Trigger (für fortgeschrittene Nutzung)
Benutzer können einen Scheduled Task einrichten der regelmäßig einen Ordner auf neue Dokumente prüft und automatisch destilliert.

---

## Deterministische Skripte (`scripts/`)

Diese Skripte erzwingen das Format und erzeugen alle abgeleiteten Artefakte. Reine
Standardbibliothek; `validate_knowledge.py` nutzt zusätzlich `jsonschema`, falls installiert.

| Skript | Zweck |
|--------|-------|
| `validate_knowledge.py <json> [--prev <alt>] [--md <md>]` | Validiert gegen `SPEC.md`/Schema, trennt ERRORS/WARNINGS, liefert reproduzierbaren `quality_score`, erzwingt den Merge-Monotonie-Vertrag. Exit 0 = konform. |
| `build_graph.py <json> --write` | Berechnet Zähler, Cluster-Mitgliedschaft, kanonische Kanten-IDs und `quality_score`; kann Concept Map + Mermaid ausgeben (`--emit-md`). |
| `build_md.py <json>` | Rendert die `.knowledge.md` deterministisch aus dem Graphen. |
| `build_bundle.py <json> -o <dir>` | Explodiert den Graphen in ein Verzeichnis-Bundle (`SPEC.md` §7). |
| `build_viewer.py <json>` | Erzeugt den self-contained `.knowledge.html`-Viewer. |

Tests: `python3 -m pytest tests/` (bzw. `python3 -m unittest discover tests`).

---

## Unterstützte Eingabeformate

| Format | Methode | Fallback |
|--------|---------|----------|
| PDF | `pdftotext` | `python3 -c "import fitz; ..."` |
| DOCX | `python3 -c "import docx; ..."` | Read (als XML) |
| PPTX | `python3 -c "from pptx import Presentation; ..."` | Read (als XML) |
| XLSX/CSV | Read + Bash | `python3 -c "import openpyxl; ..."` |
| JSON | Read | — |
| TXT/MD | Read | — |
| HTML | Read + Bash (`lynx -dump`) | WebFetch |
| Bilder | Multimodal (Bild direkt analysieren) | OCR via `tesseract` |
| URL | WebFetch | `curl` via Bash |

---

## Workflow-Zusammenfassung

```
1. INITIALISIERUNG
   ├── Modus erkennen (fresh/merge/batch)
   ├── Tiefe bestimmen (quick/standard/deep)
   ├── Ausgabeformat festlegen (md/json/both/all)
   └── TodoWrite: Aufgaben anlegen

2. EXTRACTION
   └── Extraction-Agent: Rohtext gewinnen → .extraction.tmp.md

3. ANALYSE (parallel)
   ├── Concept-Agent → Konzept-Liste
   ├── Relationship-Agent → Kanten-Liste
   └── Temporal-Agent → Zeitdaten

4. SYNTHESE
   └── Synthesis-Agent: Zusammenführung, Deduplizierung, Chunk-Erzeugung

5. VALIDIERUNG (deterministisch)
   ├── build_graph.py --write   → Zähler, Cluster, Kanten-IDs, quality_score
   └── validate_knowledge.py    → ERRORS/WARNINGS, Self-Healing bis 0 ERRORS (max 2x)

6. MERGE (falls mode=merge)
   └── Bestehenden Graph laden, Delta berechnen, zusammenführen

7. OUTPUT (abgeleitete Artefakte per Skript erzeugen, nicht von Hand)
   ├── .knowledge.json (kanonisch; build_graph.py füllt abgeleitete Felder)
   ├── .knowledge.md (build_md.py)
   ├── .knowledge.diff.md (bei mode: merge)
   ├── .knowledge.html (build_viewer.py, bei format: all)
   ├── <name>/ Bundle (build_bundle.py, bei format: bundle)
   ├── .knowledge.cypher / .ctxt / .canvas (bei format: all)
   └── Temporäre Dateien aufräumen

8. ABSCHLUSS
   └── TodoWrite: Alle Tasks als completed markieren
```

---

## Versions-Historie

| Version | Datum | Neuerungen |
|---------|-------|------------|
| 3.0 | 2026-03 | Initiale Version mit Dual-Output |
| 3.1 | 2026-03 | Temporale Dimension (3 Zeitebenen) |
| **4.0** | **2026-04** | **Multi-Agent-Pipeline, Inkrementelle Verarbeitung, Context-Management, Validation-Agent, erweiterte Ausgabeformate, Hook-Integration, standardisierte Frontmatter** |
| **4.0 / Spec 1.0** | **2026-06** | **Formale `SPEC.md` + JSON-Schema, deterministische Validierung & abgeleitete Artefakte (`scripts/`), Quellenbelege pro Aussage (`[n]` + `## Quellen`), `resource`-Identität, monotoner Merge-Vertrag, skalierbares Bundle-Format, self-contained HTML-Viewer, Testsuite** |
