---
name: "Knowledge Distiller"
description: "Kompiliert lokale Quelldokumente evidence-first in einen versionierten, prüfbaren Wissensgraphen und deterministische Folgeformate."
when_to_use: >
  Verwende diesen Skill, wenn lokale Dateien in einen strukturierten Wissensgraphen überführt,
  bestehende .knowledge.json-Dateien sicher zusammengeführt oder deterministische Markdown-,
  Viewer-, Bundle-, Cypher-, CTXT- oder Canvas-Ausgaben erzeugt werden sollen. Nicht für eine
  einfache Zusammenfassung oder ein 1:1-Transkript verwenden.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - TodoWrite
context: fork
model: opus
arguments:
  source: "Lokaler Dateipfad, Ordner oder bestehender .knowledge.json-Graph"
  depth: "quick | standard | deep"
  artifacts: "Teilmenge aus: json md bundle html cypher ctxt canvas; Standard: json md"
  language: "de | en | Quellsprache"
  mode: "fresh | merge | batch"
  profile: "Versioniertes lokales Compilerprofil; Standard: profiles/default.json"
version: "4.0"
spec_version: "1.1"
---

# Knowledge Distiller v4.0 / Spec 1.1

> Eine Datei ist ein Behälter. Wertvoll ist das belegbare Wissen darin.

Der verbindliche Datenvertrag steht in [`SPEC.md`](SPEC.md). Bei einem Widerspruch gilt die Spec.
Die `.knowledge.json` ist kanonisch; alle anderen Formate werden daraus per Skript erzeugt.

## Nicht tun

- Keine 1:1-Transkripte oder bloße Inhaltsverzeichnisse erzeugen.
- Keine URL abrufen, keine Telemetrie senden und keine Dokumentinhalte ausführen.
- Keine Quelle als intrinsisch „autoritativ“ bewerten.
- Kein Chain-of-Thought, verstecktes Scratchpad oder Promptmaterial als `reasoning` speichern.
- Keine Ortsrolle, Zeitangabe, Quelle oder Beziehung erraten.
- Keine Konflikte oder älteren Aussagen überschreiben.
- `quality_score` oder `conformance_score` niemals vom Modell schätzen.
- Keine abgeleiteten Markdown-/Viewer-/Exportdateien unabhängig vom JSON pflegen.

## Ergebnis

Ein vollständiger Lauf kann erzeugen:

- `<name>.knowledge.json` — kanonischer Graph;
- `<name>.knowledge.md` — menschliche/Obsidian-Ansicht;
- `<name>.knowledge.html` — self-contained Offline-SVG-Viewer;
- `<name>.bundle/` — eine sichere Datei pro Konzept;
- `<name>.knowledge.cypher`, `.ctxt`, `.canvas` — deterministische Exporte;
- bei Merge: Versionenarchiv und Diff;
- bei Runner-Nutzung: unveränderliche Request-, Stage- und Manifest-Receipts.

## Phase 0: Eingabe, Profil und Modus

1. Nur lokale Eingaben innerhalb des vom Nutzer bestimmten Arbeitsbereichs verwenden. Bei einer URL
   eine lokale, vom Nutzer bereitgestellte Momentaufnahme verlangen; dieser Skill ruft sie nicht ab.
2. Modus bestimmen:
   - `fresh`: neuer Graph aus einer oder mehreren Quellen;
   - `merge`: bestehender Graph plus additive Änderung;
   - `batch`: mehrere lokale Quellen, je Quelle normalisieren und anschließend monoton mergen.
3. Tiefe bestimmt den Umfang, nicht die Belegregeln: `quick` priorisiert wenige Kernkonzepte,
   `standard` deckt alle belegten Hauptaussagen ab, `deep` ergänzt belegte Querverbindungen und
   offene Fragen. `deep` schaltet weder allgemeine noch räumliche Inferenz frei; dafür braucht es
   weiterhin ein ausdrücklich aktiviertes, validiertes Profil und menschliches Review.
4. Das Compilerprofil getrennt von der Graph-Spec validieren:

   ```bash
   python3 scripts/validate_profile.py profiles/default.json
   ```

5. Das Profil steuert Auswahl- und Inferenzpolitik, darf aber keine Felder der Spec neu definieren.
   Das Standardprofil ist evidence-first, mit Inferenz und räumlicher Inferenz standardmäßig aus.

## Phase 1: Deterministische Quellen-Normalisierung

Unterstützt werden lokal und ohne Drittbibliotheken:

| Format | Selektor/Locator |
|---|---|
| TXT, Markdown | Textposition + Textquote |
| HTML | Fragment/Textquote; `script`/`style` werden ignoriert |
| CSV/TSV | `CsvSelector` mit Blatt und Zellbereich; Formeln bleiben Text |
| JSON | `JsonPointerSelector`; Duplicate Keys/NaN/zu tiefe Strukturen sind Fehler |
| DOCX | gehärtetes OOXML; Fragment/Textquote; Makros, Verschlüsselung und gefährliche ZIP/XML-Strukturen sind Fehler |

PDF, Bilder, Audio, PPTX, XLSX und unbekannte Binärformate sind im lokalen Adapter nicht
implementiert. Nicht raten: klar als nicht unterstützt melden oder eine autorisierte, lokal
extrahierte Textfassung anfordern.

Beispiel:

```bash
python3 scripts/extract_source.py source.docx \
  --input-root /absolute/safe/root \
  --format json > normalized.source.json
```

Die Adapterausgabe enthält logischen Dateinamen, Typ/MIME, SHA-256, stabile Source-/Segment-IDs,
normalisierten Text und formatgerechte Selektoren. Sie ist noch kein Wissensgraph.

## Phase 2: Evidence-first Kompilierung

### 2.1 Quelle anlegen

Jede Quelle erhält mindestens `id`, logischen `file`-Namen und `type`. Wenn vorhanden, objektive
Metadaten ergänzen: Titel, Autoren, Publisher, Version, Abrufzeit, Inhalts-Hash, Lizenz und
Agentenrollen (`author`, `publisher`, `creator`, `editor`, `data_producer`, `host`, `extractor`).

Keine absoluten Hostpfade und keine Zugangsdaten in URLs speichern.

### 2.2 Evidence zuerst

Für jede später referenzierte Passage ein wiederverwendbares Top-Level-`evidence`-Objekt erzeugen:

```json
{
  "id": "evidence-section-3",
  "source": "source-1",
  "selector": {
    "type": "TextQuoteSelector",
    "exact": "Die relevante, kurze Quellpassage."
  },
  "support": "supports",
  "attribution_basis": "source_explicit",
  "review_status": "unreviewed"
}
```

Evidence beschreibt `supports`, `contradicts`, `contextualizes` oder `mentions`; sie ist kein
Wahrheitsbeweis. Jede Referenz muss auf ein vorhandenes Evidence-Objekt zeigen.

### 2.3 Atomare Konzepte

Ein Node ist nur dann eigenständig, wenn er:

1. klar benennbar ist;
2. kein Meta-Abschnitt wie „overview“ oder „summary“ ist;
3. mit Evidence belegbar ist;
4. wiederverwendbar ist oder eine eigene stabile Identität benötigt.

Pflichtfelder: kebab-case `id`, `label`, `cluster`, `confidence`, eigenständige `definition`,
praktische `relevance`, mindestens ein `statement`, Temporalobjekt und `sources[]`.
Wenn eine kanonische URI bekannt ist, als `resource` speichern. `resource` ist die bevorzugte
Merge-Identität; es ist kein Zitat.

Keine Mindestanzahl an Konzepten erzwingen. Die Abdeckung ausdrücklich kuratierter Felder und
Fälle wird mit Evidence und Golden Cases geprüft, nicht durch eine künstliche Quote. Eine
Open-World-Vollständigkeit lässt sich daraus nicht ableiten.

### 2.4 Stabile Claims

Wichtige Aussagen zusätzlich in `claims[]` mit stabiler ID führen. Der Claim-Satz muss zugleich in
`nodes[].statements` stehen, damit Spec-1.0-Consumer weiter funktionieren.

```json
{
  "id": "claim-alpha-enables-beta",
  "node": "alpha",
  "statement": "Alpha ermöglicht Beta. [1]",
  "confidence": "high",
  "origin": "source_stated",
  "evidence": ["evidence-section-3"],
  "review_status": "unreviewed"
}
```

### 2.5 Herkunft statt verstecktem Reasoning

Erlaubte Origins:

- `source_stated`, `paraphrased`: benötigen Evidence;
- `synthesized`, `rule_derived`, `model_inferred`: benötigen eine explizite Derivation;
- `human_added`: als menschliche Ergänzung kennzeichnen.

Eine Derivation enthält nur prüfbare Metadaten:

```json
{
  "kind": "rule_derived",
  "activity": "temporal-window-rule-v1",
  "inputs": ["evidence-section-3"],
  "rule": "valid_from := explicit effective date",
  "summary": "Das explizite Wirksamkeitsdatum wurde als Beginn des Gültigkeitsfensters übernommen.",
  "review_status": "unreviewed",
  "evidence": ["evidence-section-3"]
}
```

Kein privates Denkprotokoll speichern.

### 2.6 Beziehungen

Nur acht Typen verwenden:

`uses`, `enables`, `based-on`, `part-of`, `tension`, `replaces`, `extends`, `example-of`.

Jede Kante hat auflösbare `source`/`target`, Gewicht `0..1`, Konfidenz und bei quellengestützten
oder abgeleiteten Beziehungen Origin/Evidence/Derivation. `tension` ist symmetrisch, alle anderen
sind gerichtet.

### 2.7 Fakten, Zeit und Konflikte

Fakten enthalten ID, Aussage, String-Wert, Konfidenz und Source-ID. Zeitdimensionen unterscheiden:

- `source_date` / `source_period`: wann die Quelle entstand;
- `valid_from` / `valid_until`: wann die Aussage gilt;
- `distillation_date`: wann kompiliert wurde.

Zeitformate: `2026-09-04`, `2026-09`, `2026-Q3`, `2026`, `FY2026`, `2020/2024`.
Konfidenz: `explicit`, `inferred`, `unknown`.

Abweichende Werte nicht überschreiben. Beide Fakten behalten und bei echter Spannung über
`fact_conflicts[]` (`tension` oder `supersedes`) verbinden. Einen Gewinner nur nach dokumentiertem
menschlichem Review festlegen.

### 2.8 Räumliche Rollen und Datenschutz

Ort immer mit Rolle modellieren: `jurisdiction`, `market_scope`, `event_location`,
`mentioned_location`, `origin`, `destination`. Eine Erwähnung ist keine Zuständigkeit.
Place-ID URI-artig und stabil halten; bekannte externe IDs ergänzen. Unsichere Orte weglassen statt
erraten. Sensitive präzise Orte redigieren oder gröber machen. Jeder räumliche Kontext braucht
Evidence. Modellbasierte räumliche Inferenz bleibt im Standardprofil aus.

### 2.9 Retrieval-Chunks

Chunktext enthält keine Markdown-Syntax, Wikilinks, Emojis, Code-Fences, Überschriften oder Pfeile.
Quellnahe Chunks als `source_claims`, Zusammenfassungen als `summary`. `inference` erfordert
Evidence/Derivation und zwingend:

```json
"include_in_default_retrieval": false
```

So verdrängt generierte Synthese keine belegten Aussagen.

### 2.10 Assessments

Bewertungen nur als `assessments[]` mit Dimension, Scope, Wert, Assessor, Methode, Datum und
Evidence speichern. Keine universelle Quellenautorität und keine Vermischung mit dem
Conformance-Score.

## Phase 3: Sicherer Merge

Nicht per Hand überschreiben. Den Referenz-Merger verwenden:

```bash
python3 scripts/merge_knowledge.py old.knowledge.json incoming.knowledge.json \
  --output merged.knowledge.json \
  --diff-report merged.knowledge.diff.json \
  --markdown-diff merged.knowledge.diff.md \
  --versions-dir versions
```

Regeln:

- Identität zuerst über `resource`, sonst `id`.
- Listen stabil vereinigen; frühere Payloads erhalten.
- Source-/Citation-/Node-Referenzen beim Alias-Merge konsistent remappen.
- Widersprüchliche gleiche IDs nicht still wählen.
- Vorherigen Graph automatisch archivieren, beide Diffs schreiben; höchstens fünf Versionen behalten.
- Ergebnis und `--prev`-Monotonie validieren.

Ein gleich großer Graph kann trotzdem destruktiv sein. Geänderte Statements, Fact-Werte,
Selektoren oder Evidence-Payloads müssen erkannt werden.

## Phase 4: Deterministisch bauen

Zuerst alle abgeleiteten Felder berechnen:

```bash
python3 scripts/build_graph.py graph.knowledge.json --write
```

Dann gewünschte Ansichten erzeugen:

```bash
python3 scripts/build_md.py graph.knowledge.json
python3 scripts/build_viewer.py graph.knowledge.json
python3 scripts/build_bundle.py graph.knowledge.json
python3 scripts/build_exports.py graph.knowledge.json --format all --output-dir exports
```

`build_md.py` leitet den Zielnamen nur aus einer Eingabe mit Endung `.knowledge.json` ab. Bei
abweichendem Eingabenamen ist ein ausdrücklich anderes `--out ...md` Pflicht; Eingabe und Ziel
dürfen niemals dieselbe Datei oder derselbe Symlink/Hardlink sein. Markdown- und Bundle-Builder
weisen mehrdeutiges JSON (Duplicate Keys), NaN/Infinity und ungültige Unicode-Surrogate zurück.

Der Viewer ist dependency-free, offline, CSP-gehärtet und rendert Untrusted Content als Text.
Das Bundle plant alle ID-basierten Pfade vorab, hasht unsichere/kollidierende Namen und schreibt
atomar; ohne `--out` entsteht `graph.bundle/`. Exporte bewahren Spec-1.1-Provenienz bzw. die
kanonische JSON-Payload. Die Artefaktnamen entsprechen dabei exakt dem Runner-Vokabular:
`json`, `md`, `bundle`, `html`, `cypher`, `ctxt`, `canvas`.

## Phase 5: Validieren und evaluieren

Zwingende Reihenfolge:

```bash
python3 scripts/build_graph.py graph.knowledge.json --write
python3 scripts/validate_knowledge.py graph.knowledge.json

# bei Merge zusätzlich
python3 scripts/validate_knowledge.py merged.knowledge.json --prev old.knowledge.json
```

`conformance_score = max(0, 100 − 10×Errors − 2×Warnings)`. `quality_score` ist nur der
gleichwertige Legacy-Alias. Null Errors heißt formatkonform; es heißt nicht semantisch wahr.

Für bekannte Fälle:

```bash
python3 scripts/evaluate_golden.py eval/golden_cases.json
```

Golden Evaluation misst nur die ausdrücklich kuratierten Felder. Fehlende Open-World-Fakten,
Wahrheit außerhalb der Quellen und Eignung für unbekannte Zwecke bleiben unbewertet.

Wurden die Quellen mit `extract_source.py` normalisiert, zusätzlich die Anker prüfen:

```bash
python3 scripts/verify_evidence.py graph.knowledge.json normalized.source.json
```

`not_found` ist ein **ERROR**: Das Zitat, die Position oder der kopierte Selektor existiert in der
angegebenen Quelle nicht. Evidence nicht umformulieren, bis sie „passt“, sondern die Stelle in der
Quelle neu bestimmen oder den Beleg entfernen. `unverifiable` heißt nur, dass keine passende
normalisierte Quelle oder kein prüfbarer Selektortyp vorlag; ein gefundener Anker belegt nicht, dass
die Passage die Aussage stützt.

## Reproduzierbarer Runner

Für operative Läufe den lokalen Runner verwenden:

```bash
python3 scripts/run_pipeline.py build graph.knowledge.json \
  --input-root /safe/input/root \
  --output-root /safe/output/root \
  --artifacts json md bundle html cypher ctxt canvas
```

Er snapshotet Input, Parameter und Tool-Hashes, schreibt unveränderliche Stage-Receipts, kann nur
verifizierte Stufen wiederverwenden und hält Netzwerk/Telemetrie aus. Die optionale API/MCP-Fassade
authentifiziert jeden Endpunkt mit einem Bearer-Token und bleibt auf Loopback, exakten Host/Origin,
feste Roots, harte Größen-/Concurrency-Grenzen und `knowledge_validate`/`knowledge_build` beschränkt.

## Fehlerbehandlung

- **ERROR:** nicht publizieren. Den konkret genannten Schema-, Referenz-, Provenienz-, Sicherheits-
  oder Merge-Fehler beheben und Build+Validation wiederholen.
- **WARNING:** formal konsumierbar, aber vor Veröffentlichung prüfen; dazu zählen unbelegte Nodes,
  unbekannte Felder/Spec-Versionen sowie das Legacy-Feld `authoritative_source`.
- Credential-tragende URLs und private Reasoning-/Scratchpad-Felder sind **ERRORS**, keine
  Warnungen.
- Nach zwei erfolglosen Korrekturdurchläufen die verbleibenden Fehler transparent melden; nichts
  durch Entfernen von Wissen „grün machen“.

## Kompatibilität

Spec-1.0-Dateien bleiben gültig. Spec-1.1-Produzenten führen `quality_score` und
`nodes[].statements` weiter. Consumer sollen unbekannte optionale Felder überspringen und sichere
Teile weiter anzeigen; Producer müssen streng validieren.

## Referenzdateien

| Datei | Zweck |
|---|---|
| `SPEC.md` | Autoritativer Graphvertrag. |
| `schema/knowledge.schema.json` | JSON-Schema-Spiegel. |
| `profiles/default.json` | Standard-Compilerpolitik. |
| `docs/EXTRACTION.md` | Lokale Adapter, Grenzen und Ankerprüfung. |
| `docs/MERGE.md` | Merge-Identität, Konflikte, Archiv/Diff. |
| `docs/EXPORTS.md` | Cypher/CTXT/Canvas-Mapping. |
| `docs/RUNNER.md` | Receipts, Resume, API/MCP. |
| `docs/EVALUATION.md` | Golden-Manifest und Aussagegrenzen. |

## Versionshistorie

| Version | Änderung |
|---|---|
| 4.0 / Spec 1.1 (2026-09) | Additive Evidence-/Claim-/Origin-/Spatial-/Assessment-/Conflict-Felder; gehärtete Viewer/Bundle/Adapter; echter Merge/Export; Runner, Profile und Golden Evaluation. |
| 4.0 / Spec 1.0 (2026-06) | Formale Spec/Schema, Quellenreferenzen, Temporalität, Resource-Identität und Bundle-Grundvertrag. |
