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
  format: "Ausgabeformat: md, json, both, all (inkl. Mermaid, Cypher, CTXT)"
  language: "Ausgabesprache: de, en (Default: Sprache des Quelldokuments)"
  mode: "Verarbeitungsmodus: fresh (neu), merge (mit bestehendem Graph), batch (Ordner)"
version: "4.0"
---

# Knowledge Distiller v4.0

> **Leitprinzip:** Eine Datei ist ein Behälter. Das Wissen darin ist wertvoll.

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
- **v4.0 NEU:** Inkrementelle Graph-Evolution (Merge, Delta, Multi-Source)
- **v4.0 NEU:** Automatisierte Qualitätssicherung mit Validation-Agent
- **v4.0 NEU:** Context-Management für Dokumente jeder Größe
- **v4.0 NEU:** Erweiterte Ausgabeformate (Mermaid, Cypher, CTXT, Canvas, HTML)

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

**Qualitätsregeln:**
- Jedes Konzept MUSS ohne Quelldokument verständlich sein
- Keine Synonyme als separate Konzepte
- Minimum 10 Konzepte bei `standard`, 5 bei `quick`, 20+ bei `deep`

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

#### 6. Validation-Agent
**Aufgabe:** Automatisierte Qualitätsprüfung des gesamten Outputs.
**Prüfcheckliste:**

```
MARKDOWN-VALIDIERUNG:
□ Kein Copy-Paste aus Originaldokument
□ Jedes Konzept exakt einmal definiert (Single-Definition-Prinzip)
□ Jeder Concept-Map-Eintrag verweist auf existierenden Kernwissen-Block
□ Alle Wikilinks [[...]] zeigen auf definierte Konzepte
□ Cluster-Zuordnung konsistent zwischen Frontmatter und Kernwissen
□ Zeitliche Annotationen in jedem Block vorhanden
□ Embedding-Chunks frei von Markdown-Syntax, Emojis, Pfeilen, Wikilinks

JSON-VALIDIERUNG:
□ Syntaktisch valides JSON
□ JSON-LD @context korrekt (schema.org)
□ Jeder Knoten hat: id, label, cluster, confidence, definition, relevance, statements[], temporal{}
□ Jede Kante hat: source, target, type (aus 8 Typen), label, weight, confidence
□ Alle source/target-Referenzen zeigen auf existierende Knoten-IDs
□ Chunks enthalten keinen Syntax-Ballast
□ Temporale Felder vollständig ausgefüllt (mindestens source_date + temporal_confidence)

CROSS-VALIDIERUNG:
□ Markdown und JSON enthalten identisches Wissen
□ Anzahl Konzepte stimmt überein
□ Anzahl Beziehungen stimmt überein
□ Cluster-Struktur identisch
```

**Qualitäts-Score:**
Berechne einen Gesamtscore (0-100) basierend auf der Checkliste. Score wird im Frontmatter beider Outputs gespeichert.

**Self-Healing:**
Bei Score < 80 → Fehler identifizieren, automatisch korrigieren, Re-Validierung durchführen. Maximal 2 Korrektur-Iterationen.

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

### Merge-Modus
Wenn ein bestehender `.knowledge.json` im Ausgabeordner existiert:

1. **Bestehenden Graph laden** und parsen
2. **Neue Konzepte** hinzufügen (ID-Kollisionen durch Suffix `-v2` auflösen)
3. **Bestehende Konzepte aktualisieren:**
   - Höhere Konfidenz gewinnt
   - Bei gleicher Konfidenz: neueres Quelldatum gewinnt
   - Statements werden vereinigt (keine Duplikate)
4. **Neue Beziehungen** ergänzen, bestehende Gewichte anpassen
5. **Multi-Source-Provenance:** Jeder Knoten/Kante bekommt `sources[]`-Array mit Herkunftsdokumenten
6. **Delta-Output:** Zusätzlich `.knowledge.diff.md` mit nur den Änderungen

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

#### Frontmatter-Block
```yaml
---
title: "{Titel des Wissensgraphen}"
source: "{Quelldatei(en)}"
source_type: "{pdf|docx|pptx|xlsx|csv|json|txt|md|html|image|url}"
source_date: "{ISO 8601}"
distillation_date: "{ISO 8601}"
distiller_version: "4.0"
domain: "{Fachgebiet}"
language: "{de|en}"
depth: "{quick|standard|deep}"
mode: "{fresh|merge|batch}"
quality_score: {0-100}
temporal_confidence: "{explicit|inferred|unknown}"
concept_count: {N}
relationship_count: {N}
cluster_count: {N}
sources:
  - file: "{Dateiname}"
    date: "{ISO 8601}"
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
| {Beschreibung} | {Zahl/Aussage} | {Zeitraum} | {high/medium/low} | {Datei} |
```

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
    "sources": [
      {
        "file": "",
        "type": "",
        "date": "",
        "url": ""
      }
    ],
    "distillation_date": "",
    "distiller_version": "4.0",
    "domain": "",
    "language": "",
    "depth": "",
    "mode": "",
    "quality_score": 0,
    "concept_count": 0,
    "relationship_count": 0,
    "cluster_count": 0
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
      "statements": [
        ""
      ],
      "temporal": {
        "source_date": "",
        "source_period": "",
        "valid_from": "",
        "valid_until": "",
        "temporal_confidence": "explicit|inferred|unknown"
      },
      "sources": []
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
      "source": ""
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

### 4.3 Zusätzliche Ausgabeformate (NEU v4.0)

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
        "description": "JSON-LD nach Schreibung validieren",
        "matcher": {
          "tool_name": "Write",
          "file_pattern": "*.knowledge.json"
        },
        "command": "python3 -c \"import json,sys; json.load(open(sys.argv[1])); print('JSON valid')\" \"$TOOL_INPUT_FILE_PATH\""
      }
    ]
  }
}
```

### Auto-Trigger (für fortgeschrittene Nutzung)
Benutzer können einen Scheduled Task einrichten der regelmäßig einen Ordner auf neue Dokumente prüft und automatisch destilliert.

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

5. VALIDIERUNG
   └── Validation-Agent: Qualitätsprüfung, Self-Healing bei Score < 80

6. MERGE (falls mode=merge)
   └── Bestehenden Graph laden, Delta berechnen, zusammenführen

7. OUTPUT
   ├── .knowledge.md (immer)
   ├── .knowledge.json (bei format: json/both/all)
   ├── .knowledge.diff.md (bei mode: merge)
   ├── .knowledge.cypher (bei format: all)
   ├── .knowledge.ctxt (bei format: all)
   ├── .knowledge.canvas (bei format: all)
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
