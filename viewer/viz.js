/* Knowledge Distiller — interactive viewer (reads global GRAPH; no fetch). */
(function () {
  "use strict";

  var G = (typeof window !== "undefined" && window.GRAPH) ? window.GRAPH : {};
  var META = G.metadata || {};
  var NODES = Array.isArray(G.nodes) ? G.nodes : [];
  var EDGES = Array.isArray(G.edges) ? G.edges : [];
  var CLUSTERS = Array.isArray(G.clusters) ? G.clusters : [];
  var FACTS = Array.isArray(G.facts) ? G.facts : [];
  var OPEN_Q = Array.isArray(G.open_questions) ? G.open_questions : [];

  var detailEl = document.getElementById("detail");
  var cyEl = document.getElementById("cy");
  var legendEl = document.getElementById("legend");

  /* ---------- small helpers ---------- */
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function md(s) {
    if (!s) return "";
    if (typeof window.marked !== "undefined") {
      try {
        var fn = window.marked.parse || window.marked;
        return fn(String(s));
      } catch (e) { /* fall through */ }
    }
    return "<p>" + esc(s) + "</p>";
  }
  function val(v) { return v !== null && v !== undefined && v !== "" ? v : null; }

  function clusterLabel(c) {
    return (c && (c.label || c.name)) || (c && c.id) || "";
  }

  /* ---------- header / metadata (works even without cytoscape) ---------- */
  function setupHeader() {
    var title = META.title || "Knowledge Graph";
    document.title = title;
    var tEl = document.getElementById("kg-title");
    if (tEl) tEl.textContent = title;

    var date = META.distillation_date || META.distilled || META.source_date || "";
    var subParts = [];
    if (META.domain) subParts.push(esc(META.domain));
    if (date) subParts.push("distilled " + esc(date));
    var subEl = document.getElementById("kg-sub");
    if (subEl) subEl.innerHTML = subParts.join(" &middot; ");

    var concepts = (META.concept_count != null) ? META.concept_count : NODES.length;
    var rels = (META.relationship_count != null) ? META.relationship_count : EDGES.length;
    var clus = (META.cluster_count != null) ? META.cluster_count : CLUSTERS.length;

    var chips = [
      chip(concepts, "concepts"),
      chip(rels, "relationships"),
      chip(clus, "clusters")
    ];
    if (META.quality_score != null) {
      chips.push('<span class="chip quality"><b>' + esc(META.quality_score) + '</b> quality</span>');
    }
    var chipsEl = document.getElementById("kg-chips");
    if (chipsEl) chipsEl.innerHTML = chips.join("");
  }
  function chip(n, label) {
    return '<span class="chip"><b>' + esc(n) + "</b> " + esc(label) + "</span>";
  }

  /* ---------- cluster palette ---------- */
  var PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#499894",
    "#86bcb6", "#d37295", "#a0cbe8", "#8cd17d", "#b6992d"
  ];
  var DEFAULT_COLOR = "#9aa6b2";
  var clusterColor = {};
  var clusterById = {};
  CLUSTERS.forEach(function (c, i) {
    clusterColor[c.id] = PALETTE[i % PALETTE.length];
    clusterById[c.id] = c;
  });
  function colorFor(cid) { return clusterColor[cid] || DEFAULT_COLOR; }

  /* ---------- node index + superseded detection ---------- */
  var nodeById = {};
  NODES.forEach(function (n) { nodeById[n.id] = n; });

  var supersededSet = {};
  NODES.forEach(function (n) {
    var t = n.temporal || {};
    if (val(t.valid_until)) supersededSet[n.id] = true;
  });
  EDGES.forEach(function (e) {
    if (e.type === "replaces" && e.target) supersededSet[e.target] = true;
  });

  /* ---------- content-based node size ---------- */
  function nodeSize(n) {
    var nStmt = Array.isArray(n.statements) ? n.statements.length : 0;
    var defLen = (n.definition || "").length;
    var score = nStmt * 18 + defLen; // "statements.length + definition.length"
    var size = 24 + Math.sqrt(score) * 1.7; // base 24px + scaled
    return Math.max(24, Math.min(92, Math.round(size)));
  }

  setupHeader();
  renderOverview();

  /* ---------- guard: cytoscape must have loaded from CDN ---------- */
  if (typeof window.cytoscape === "undefined") {
    if (cyEl) {
      cyEl.innerHTML =
        '<div class="load-error"><b>&#9888;&#65039; Could not load Cytoscape</b>' +
        "This viewer needs internet for the CDN libs (cytoscape + marked).<br>" +
        "Reopen the file while online to see the interactive graph.<br>" +
        "Metadata, facts and open questions are still shown on the right.</div>";
    }
    return;
  }

  buildGraph();

  /* ===================================================================== */
  /* GRAPH                                                                  */
  /* ===================================================================== */
  var cy = null;
  var activeCluster = null;

  function buildGraph() {
    var elements = [];

    NODES.forEach(function (n) {
      elements.push({
        data: {
          id: n.id,
          label: n.label || n.id,
          cluster: n.cluster || "",
          definition: n.definition || "",
          color: colorFor(n.cluster),
          size: nodeSize(n),
          raw: n
        },
        classes: supersededSet[n.id] ? "superseded" : ""
      });
    });

    EDGES.forEach(function (e, i) {
      if (!nodeById[e.source] || !nodeById[e.target]) return; // skip dangling
      var weight = (typeof e.weight === "number") ? e.weight : 0.5;
      elements.push({
        data: {
          id: e.id || ("e" + i),
          source: e.source,
          target: e.target,
          type: e.type || "",
          width: 1 + Math.max(0, Math.min(1, weight)) * 4,
          raw: e
        },
        classes: e.type === "tension" ? "tension" : ""
      });
    });

    cy = cytoscape({
      container: cyEl,
      elements: elements,
      wheelSensitivity: 0.25,
      style: graphStyle(),
      layout: {
        name: "cose",
        animate: false,
        padding: 40,
        nodeRepulsion: 9000,
        idealEdgeLength: 130,
        nodeOverlap: 14,
        gravity: 0.5,
        nestingFactor: 0.9,
        componentSpacing: 90
      }
    });

    cy.on("tap", "node", function (evt) { selectNode(evt.target.id()); });
    cy.on("tap", function (evt) {
      if (evt.target === cy) {
        cy.$(":selected").unselect();
        renderOverview();
      }
    });

    buildLegend();
  }

  function graphStyle() {
    return [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          "width": "data(size)",
          "height": "data(size)",
          "label": "data(label)",
          "font-size": 11,
          "color": "#15202b",
          "text-outline-color": "#ffffff",
          "text-outline-width": 2,
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 96,
          "border-width": 1.5,
          "border-color": "rgba(0,0,0,0.18)"
        }
      },
      {
        selector: "node.superseded",
        style: {
          "background-color": "#c2c9d1",
          "opacity": 0.55,
          "color": "#7a838d",
          "border-style": "dashed"
        }
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 4,
          "border-color": "#2f6fed",
          "text-outline-width": 3
        }
      },
      {
        selector: "edge",
        style: {
          "width": "data(width)",
          "label": "data(type)",
          "font-size": 9,
          "color": "#5a6672",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.85,
          "text-background-shape": "roundrectangle",
          "text-background-padding": 1,
          "curve-style": "bezier",
          "line-color": "#c2cad3",
          "target-arrow-color": "#aab4bf",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.9,
          "opacity": 0.85
        }
      },
      {
        selector: "edge.tension",
        style: {
          "line-style": "dashed",
          "line-color": "#e15759",
          "target-arrow-shape": "none",
          "source-arrow-shape": "none",
          "color": "#b23b3b"
        }
      },
      {
        selector: "edge:selected",
        style: { "line-color": "#2f6fed", "target-arrow-color": "#2f6fed", "opacity": 1 }
      },
      { selector: ".hidden", style: { "display": "none" } },
      { selector: ".cl-dim", style: { "opacity": 0.12 } },
      { selector: "node.cl-hi", style: { "opacity": 1 } }
    ];
  }

  /* ---------- legend ---------- */
  function buildLegend() {
    if (!legendEl) return;
    if (!CLUSTERS.length) { legendEl.style.display = "none"; return; }
    var html = '<div class="legend-title">Clusters &middot; click to focus</div>';
    CLUSTERS.forEach(function (c) {
      html += '<div class="legend-item" data-cluster="' + esc(c.id) + '">' +
        '<span class="legend-swatch" style="background:' + colorFor(c.id) + '"></span>' +
        "<span>" + esc(clusterLabel(c)) + "</span></div>";
    });
    legendEl.innerHTML = html;
    legendEl.querySelectorAll(".legend-item").forEach(function (item) {
      item.addEventListener("click", function () {
        var cid = item.getAttribute("data-cluster");
        toggleCluster(cid === activeCluster ? null : cid);
      });
    });
  }

  function toggleCluster(cid) {
    activeCluster = cid;
    legendEl.querySelectorAll(".legend-item").forEach(function (it) {
      it.classList.toggle("active", it.getAttribute("data-cluster") === cid);
    });
    if (!cy) return;
    cy.batch(function () {
      cy.elements().removeClass("cl-dim cl-hi");
      if (!cid) return;
      var inSet = cy.nodes('[cluster = "' + cid + '"]');
      cy.nodes().not(inSet).addClass("cl-dim");
      inSet.addClass("cl-hi");
      var keepEdges = inSet.connectedEdges();
      cy.edges().not(keepEdges).addClass("cl-dim");
    });
  }

  /* ---------- selection ---------- */
  function selectNode(id) {
    if (!cy) return;
    var n = cy.$id(id);
    if (!n || !n.length) return;
    cy.$(":selected").unselect();
    n.select();
    cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1) }, { duration: 320 });
    renderDetail(n.data("raw"));
  }
  // expose for inline link handlers
  window.__kgSelect = selectNode;

  /* ===================================================================== */
  /* DETAIL PANE                                                            */
  /* ===================================================================== */
  function temporalLine(t) {
    if (!t) return "";
    var parts = [];
    if (val(t.source_date)) parts.push("source <code>" + esc(t.source_date) + "</code>");
    if (val(t.valid_from) || val(t.valid_until)) {
      parts.push("valid <code>" + esc(t.valid_from || "?") + "</code> &rarr; <code>" +
        esc(t.valid_until || "now") + "</code>");
    }
    if (val(t.temporal_confidence)) parts.push("(" + esc(t.temporal_confidence) + ")");
    if (!parts.length) return "";
    return '<div class="temporal-line">&#128336; ' + parts.join(" &middot; ") + "</div>";
  }

  function renderDetail(n) {
    if (!n) { renderOverview(); return; }
    var html = "";
    html += "<h2>" + esc(n.label || n.id) + "</h2>";

    var c = clusterById[n.cluster];
    html += '<div class="meta-row">';
    if (c) {
      html += '<span class="badge cluster" style="--cluster-bg:' + colorFor(n.cluster) +
        ";background:" + colorFor(n.cluster) + '">' + esc(clusterLabel(c)) + "</span>";
    }
    if (n.confidence) {
      html += '<span class="badge conf-' + esc(n.confidence) + '">' + esc(n.confidence) + " confidence</span>";
    }
    if (supersededSet[n.id]) {
      html += '<span class="badge superseded">&#9888; superseded</span>';
    }
    html += "</div>";

    html += temporalLine(n.temporal);

    if (val(n.definition)) {
      html += '<div class="section-label">Definition</div><div class="detail-md">' + md(n.definition) + "</div>";
    }
    if (val(n.relevance)) {
      html += '<div class="section-label">Relevance</div><div class="detail-md">' + md(n.relevance) + "</div>";
    }

    if (Array.isArray(n.statements) && n.statements.length) {
      html += '<div class="section-label">Statements</div><div class="detail-md"><ul>';
      n.statements.forEach(function (s) {
        var inner = md(s).replace(/^\s*<p>/, "").replace(/<\/p>\s*$/, "");
        html += "<li>" + inner + "</li>";
      });
      html += "</ul></div>";
    }

    if (Array.isArray(n.citations) && n.citations.length) {
      html += '<div class="section-label">Citations</div><ul class="cite-list">';
      n.citations.forEach(function (ci) {
        var txt = esc(ci.label || ci.source || "");
        var loc = ci.locator ? " (" + esc(ci.locator) + ")" : "";
        var body = ci.url
          ? '<a href="' + esc(ci.url) + '" target="_blank" rel="noopener">' + txt + "</a>"
          : txt;
        html += '<li><span class="cn">[' + esc(ci.n != null ? ci.n : "?") + "]</span>" + body + loc + "</li>";
      });
      html += "</ul>";
    }

    // Cited by (edges targeting this node)
    var citedBy = EDGES.filter(function (e) { return e.target === n.id && nodeById[e.source]; });
    if (citedBy.length) {
      html += '<div class="section-label">Cited by</div><ul class="link-list">';
      citedBy.forEach(function (e) {
        var src = nodeById[e.source];
        html += "<li>" + edgeBadge(e.type) + nodeLink(src) + "</li>";
      });
      html += "</ul>";
    }

    // Outgoing (edges from this node)
    var outgoing = EDGES.filter(function (e) { return e.source === n.id && nodeById[e.target]; });
    if (outgoing.length) {
      html += '<div class="section-label">Outgoing</div><ul class="link-list">';
      outgoing.forEach(function (e) {
        var tgt = nodeById[e.target];
        html += "<li>" + edgeBadge(e.type) + nodeLink(tgt) + "</li>";
      });
      html += "</ul>";
    }

    detailEl.innerHTML = html;
    wireNodeLinks();
  }

  function edgeBadge(type) {
    return type ? '<span class="edge-type">' + esc(type) + "</span>" : "";
  }
  function nodeLink(n) {
    return '<a class="node-link" data-node="' + esc(n.id) + '">' + esc(n.label || n.id) + "</a>";
  }
  function wireNodeLinks() {
    detailEl.querySelectorAll(".node-link").forEach(function (a) {
      a.addEventListener("click", function () { selectNode(a.getAttribute("data-node")); });
    });
  }

  /* ===================================================================== */
  /* OVERVIEW (no node selected): facts + open questions                   */
  /* ===================================================================== */
  function renderOverview() {
    var html = '<h2>Overview</h2><div class="pane-hint">Click any node to inspect it. ' +
      "Use the legend to focus a cluster, or the search box to filter.</div>";

    if (FACTS.length) {
      html += '<div class="section-label">Key facts</div>';
      html += '<table class="facts-table"><thead><tr><th>Statement</th><th>Value</th><th>Conf.</th></tr></thead><tbody>';
      FACTS.forEach(function (f) {
        var stmt = f.statement || f.fact || "";
        html += "<tr><td>" + esc(stmt) + '</td><td class="v">' + esc(f.value || "") +
          "</td><td>" + esc(f.confidence || "") + "</td></tr>";
      });
      html += "</tbody></table>";
    }

    if (OPEN_Q.length) {
      html += '<div class="section-label">Open questions</div><ol class="oq-list">';
      OPEN_Q.forEach(function (q) {
        var t = (typeof q === "string") ? q : (q && (q.question || q.text)) || "";
        html += "<li>" + esc(t) + "</li>";
      });
      html += "</ol>";
    }

    if (!FACTS.length && !OPEN_Q.length) {
      html += '<p class="empty">No facts or open questions in this graph.</p>';
    }
    detailEl.innerHTML = html;
  }

  /* ===================================================================== */
  /* SEARCH                                                                 */
  /* ===================================================================== */
  var searchEl = document.getElementById("search");
  if (searchEl) {
    searchEl.addEventListener("input", function () { applySearch(searchEl.value); });
  }
  function applySearch(q) {
    if (!cy) return;
    q = (q || "").trim().toLowerCase();
    cy.batch(function () {
      if (!q) { cy.elements().removeClass("hidden"); return; }
      cy.nodes().forEach(function (nd) {
        var hay = ((nd.data("label") || "") + " " + (nd.data("definition") || "")).toLowerCase();
        nd.toggleClass("hidden", hay.indexOf(q) === -1);
      });
      cy.edges().forEach(function (e) {
        e.toggleClass("hidden", e.source().hasClass("hidden") || e.target().hasClass("hidden"));
      });
    });
  }
})();
