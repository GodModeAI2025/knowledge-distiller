/* Knowledge Distiller — dependency-free interactive SVG viewer. */
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var G = loadGraphData();
  var META = isRecord(G.metadata) ? G.metadata : {};
  var RAW_NODES = Array.isArray(G.nodes) ? G.nodes : [];
  var RAW_EDGES = Array.isArray(G.edges) ? G.edges : [];
  var CLUSTERS = records(G.clusters);
  var FACTS = records(G.facts);
  var OPEN_Q = Array.isArray(G.open_questions) ? G.open_questions : [];

  var detailEl = document.getElementById("detail");
  var cyEl = document.getElementById("cy");
  var legendEl = document.getElementById("legend");
  var searchEl = document.getElementById("search");

  function loadGraphData() {
    var dataElement = document.getElementById("kd-graph-data");
    if (!dataElement) return {};
    try {
      var parsed = JSON.parse(dataElement.textContent || "{}");
      return isRecord(parsed) ? parsed : {};
    } catch (error) {
      return {};
    }
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function records(value) {
    return Array.isArray(value) ? value.filter(isRecord) : [];
  }

  function text(value) {
    return value === null || value === undefined ? "" : String(value);
  }

  function present(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function clear(element) {
    if (element) element.replaceChildren();
  }

  function element(tag, className, content) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  }

  function svgElement(tag, className) {
    var node = document.createElementNS(SVG_NS, tag);
    if (className) node.setAttribute("class", className);
    return node;
  }

  function appendTextWithBreaks(parent, value) {
    text(value).split("\n").forEach(function (part, index) {
      if (index) parent.appendChild(document.createElement("br"));
      parent.appendChild(document.createTextNode(part));
    });
  }

  /* Only explicit web/mail links are clickable. Other schemes and malformed URLs
     degrade to plain text. Control characters are rejected before URL parsing. */
  function safeUrl(value) {
    if (typeof value !== "string") return null;
    var candidate = value.trim();
    if (!candidate || /[\u0000-\u001f\u007f]/.test(candidate)) return null;
    try {
      var parsed = new URL(candidate, window.location.href);
      if (parsed.protocol === "https:" || parsed.protocol === "http:" || parsed.protocol === "mailto:") {
        if (parsed.username || parsed.password) return null;
        var credentialNames = {
          accesskey: true, apikey: true, auth: true, authorization: true,
          awsaccesskeyid: true, clientsecret: true, credential: true, jwt: true,
          password: true, passwd: true, secret: true, secretkey: true,
          sessionid: true, sig: true, signature: true, token: true
        };
        var credentialSuffixes = ["token", "apikey", "secret", "password", "credential", "signature"];
        var containsCredential = false;
        parsed.searchParams.forEach(function (_value, name) {
          var normalized = name.toLowerCase().replace(/[^a-z0-9]+/g, "");
          if (credentialNames[normalized] || credentialSuffixes.some(function (suffix) {
            return normalized.slice(-suffix.length) === suffix;
          })) containsCredential = true;
        });
        if (containsCredential) return null;
        return parsed.href;
      }
    } catch (error) {
      return null;
    }
    return null;
  }

  function externalLink(label, url) {
    var safe = safeUrl(url);
    if (!safe) return document.createTextNode(text(label));
    var anchor = element("a", "", label);
    anchor.href = safe;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.referrerPolicy = "no-referrer";
    return anchor;
  }

  /* A deliberately small Markdown renderer. It creates DOM nodes directly, so raw
     HTML is displayed as text and cannot become active markup. */
  var INLINE_MARKUP = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^\)\n]+\))/g;

  function wordLike(character) {
    return !!character && /[A-Za-z0-9_\u00c0-\u024f]/.test(character);
  }

  function validUnderscoreDelimiter(source, match) {
    var token = match[0];
    if (token.charAt(0) !== "_") return true;
    var before = source.charAt(match.index - 1);
    var after = source.charAt(match.index + token.length);
    return !wordLike(before) && !wordLike(after);
  }

  function appendInlineMarkdown(parent, value) {
    var source = text(value);
    var cursor = 0;
    var match;
    INLINE_MARKUP.lastIndex = 0;
    while ((match = INLINE_MARKUP.exec(source)) !== null) {
      /* CommonMark-style underscore delimiters cannot open or close inside a
         word.  If a broad regex candidate spans identifiers such as
         window.__state ... window.__state, restart one character later so
         real markup nested in that text remains independently detectable. */
      if (!validUnderscoreDelimiter(source, match)) {
        INLINE_MARKUP.lastIndex = match.index + 1;
        continue;
      }
      appendTextWithBreaks(parent, source.slice(cursor, match.index));
      var token = match[0];
      if (token.charAt(0) === "`") {
        parent.appendChild(element("code", "", token.slice(1, -1)));
      } else if (token.slice(0, 2) === "**" || token.slice(0, 2) === "__") {
        var strong = element("strong");
        appendTextWithBreaks(strong, token.slice(2, -2));
        parent.appendChild(strong);
      } else if (token.charAt(0) === "*" || token.charAt(0) === "_") {
        var emphasis = element("em");
        appendTextWithBreaks(emphasis, token.slice(1, -1));
        parent.appendChild(emphasis);
      } else {
        var linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
        if (linkMatch) parent.appendChild(externalLink(linkMatch[1], linkMatch[2]));
        else appendTextWithBreaks(parent, token);
      }
      cursor = match.index + token.length;
    }
    appendTextWithBreaks(parent, source.slice(cursor));
  }

  function appendMarkdown(parent, value) {
    var lines = text(value).replace(/\r\n?/g, "\n").split("\n");
    var index = 0;
    while (index < lines.length) {
      if (!lines[index].trim()) {
        index += 1;
        continue;
      }

      var bullet = /^\s*[-+*]\s+(.+)$/.exec(lines[index]);
      var numbered = /^\s*\d+[.)]\s+(.+)$/.exec(lines[index]);
      if (bullet || numbered) {
        var list = element(numbered ? "ol" : "ul");
        while (index < lines.length) {
          var itemMatch = numbered
            ? /^\s*\d+[.)]\s+(.+)$/.exec(lines[index])
            : /^\s*[-+*]\s+(.+)$/.exec(lines[index]);
          if (!itemMatch) break;
          var item = element("li");
          appendInlineMarkdown(item, itemMatch[1]);
          list.appendChild(item);
          index += 1;
        }
        parent.appendChild(list);
        continue;
      }

      var heading = /^(#{1,6})\s+(.+)$/.exec(lines[index]);
      if (heading) {
        var headingEl = element(heading[1].length < 3 ? "h3" : "h4");
        appendInlineMarkdown(headingEl, heading[2]);
        parent.appendChild(headingEl);
        index += 1;
        continue;
      }

      if (/^\s*>\s?/.test(lines[index])) {
        var quote = element("blockquote");
        var quoteLines = [];
        while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        appendInlineMarkdown(quote, quoteLines.join("\n"));
        parent.appendChild(quote);
        continue;
      }

      var paragraphLines = [];
      while (index < lines.length && lines[index].trim() &&
             !/^\s*[-+*]\s+/.test(lines[index]) &&
             !/^\s*\d+[.)]\s+/.test(lines[index]) &&
             !/^(#{1,6})\s+/.test(lines[index]) &&
             !/^\s*>\s?/.test(lines[index])) {
        paragraphLines.push(lines[index]);
        index += 1;
      }
      var paragraph = element("p");
      appendInlineMarkdown(paragraph, paragraphLines.join("\n"));
      parent.appendChild(paragraph);
    }
  }

  function clusterLabel(cluster) {
    return text(cluster && (cluster.label || cluster.name || cluster.id));
  }

  var PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#3f8884", "#4f8f45",
    "#9b7818", "#996690", "#c65f76", "#816052", "#337b78",
    "#4b7d77", "#9b4f70", "#5188ad", "#568c4f", "#806f1f"
  ];
  var DEFAULT_COLOR = "#7b8794";
  var clusterColor = Object.create(null);
  var clusterById = Object.create(null);
  CLUSTERS.forEach(function (cluster, index) {
    if (!present(cluster.id)) return;
    var id = text(cluster.id);
    clusterColor[id] = PALETTE[index % PALETTE.length];
    clusterById[id] = cluster;
  });

  function colorFor(clusterId) {
    return clusterColor[text(clusterId)] || DEFAULT_COLOR;
  }

  var NODES = [];
  var nodeById = Object.create(null);
  RAW_NODES.forEach(function (candidate) {
    if (!isRecord(candidate) || !present(candidate.id)) return;
    var id = text(candidate.id);
    if (Object.prototype.hasOwnProperty.call(nodeById, id)) return;
    nodeById[id] = candidate;
    NODES.push(candidate);
  });

  var EDGES = [];
  RAW_EDGES.forEach(function (candidate) {
    if (!isRecord(candidate)) return;
    var source = text(candidate.source);
    var target = text(candidate.target);
    if (!nodeById[source] || !nodeById[target]) return;
    EDGES.push(candidate);
  });

  var supersededSet = Object.create(null);
  NODES.forEach(function (node) {
    var temporal = isRecord(node.temporal) ? node.temporal : {};
    if (present(temporal.valid_until)) supersededSet[text(node.id)] = true;
  });
  EDGES.forEach(function (edge) {
    if (edge.type === "replaces" && present(edge.target)) supersededSet[text(edge.target)] = true;
  });

  function nodeSize(node) {
    var statementCount = Array.isArray(node.statements) ? node.statements.length : 0;
    var definitionLength = text(node.definition).length;
    var score = statementCount * 18 + definitionLength;
    return Math.max(24, Math.min(92, Math.round(24 + Math.sqrt(score) * 1.7)));
  }

  function setupHeader() {
    var title = text(META.title) || "Knowledge Graph";
    document.title = title;
    var titleEl = document.getElementById("kg-title");
    if (titleEl) titleEl.textContent = title;

    var date = META.distillation_date || META.distilled || META.source_date || "";
    var subParts = [];
    if (present(META.domain)) subParts.push(text(META.domain));
    if (present(date)) subParts.push("distilled " + text(date));
    var subEl = document.getElementById("kg-sub");
    if (subEl) subEl.textContent = subParts.join(" · ");

    var concepts = META.concept_count !== null && META.concept_count !== undefined
      ? META.concept_count : NODES.length;
    var relationships = META.relationship_count !== null && META.relationship_count !== undefined
      ? META.relationship_count : EDGES.length;
    var clusterCount = META.cluster_count !== null && META.cluster_count !== undefined
      ? META.cluster_count : CLUSTERS.length;
    var chipsEl = document.getElementById("kg-chips");
    if (!chipsEl) return;
    clear(chipsEl);
    chipsEl.appendChild(chip(concepts, "concepts"));
    chipsEl.appendChild(chip(relationships, "relationships"));
    chipsEl.appendChild(chip(clusterCount, "clusters"));
    var conformance = META.conformance_score !== null && META.conformance_score !== undefined
      ? META.conformance_score : META.quality_score;
    if (conformance !== null && conformance !== undefined) {
      chipsEl.appendChild(chip(conformance, "conformance", true));
    }
  }

  function chip(value, label, highlighted) {
    var item = element("span", "chip" + (highlighted ? " conformance" : ""));
    item.appendChild(element("b", "", value));
    item.appendChild(document.createTextNode(" " + label));
    return item;
  }

  var svg = null;
  var viewport = null;
  var graphWidth = 900;
  var graphHeight = 650;
  var graphNodes = [];
  var graphEdges = [];
  var graphNodeById = Object.create(null);
  var activeCluster = null;
  var selectedId = null;
  var query = "";
  var view = { x: 0, y: 0, zoom: 1 };
  var pointer = null;

  function hash(value) {
    var result = 2166136261;
    var source = text(value);
    for (var index = 0; index < source.length; index += 1) {
      result ^= source.charCodeAt(index);
      result = Math.imul(result, 16777619);
    }
    return result >>> 0;
  }

  function layoutGraph() {
    var count = graphNodes.length;
    if (!count) return;
    var centerX = graphWidth / 2;
    var centerY = graphHeight / 2;
    var clusterIds = Object.keys(clusterById);
    var clusterCount = Math.max(1, clusterIds.length);
    var clusterIndex = Object.create(null);
    clusterIds.forEach(function (id, index) { clusterIndex[id] = index; });

    graphNodes.forEach(function (node, index) {
      var cid = text(node.raw.cluster);
      var cIndex = Object.prototype.hasOwnProperty.call(clusterIndex, cid)
        ? clusterIndex[cid] : (hash(cid) % clusterCount);
      var clusterAngle = (Math.PI * 2 * cIndex) / clusterCount - Math.PI / 2;
      var clusterRadius = Math.min(graphWidth, graphHeight) * (clusterCount > 1 ? 0.24 : 0);
      var jitterAngle = ((hash(node.id) % 10000) / 10000) * Math.PI * 2;
      var jitterRadius = 30 + ((hash(node.id + ":radius") % 1000) / 1000) *
        Math.min(150, Math.min(graphWidth, graphHeight) * 0.2);
      node.x = centerX + Math.cos(clusterAngle) * clusterRadius + Math.cos(jitterAngle) * jitterRadius;
      node.y = centerY + Math.sin(clusterAngle) * clusterRadius + Math.sin(jitterAngle) * jitterRadius;
      if (count === 1) {
        node.x = centerX;
        node.y = centerY;
      } else if (!clusterIds.length) {
        var angle = (Math.PI * 2 * index) / count - Math.PI / 2;
        node.x = centerX + Math.cos(angle) * Math.min(graphWidth, graphHeight) * 0.3;
        node.y = centerY + Math.sin(angle) * Math.min(graphWidth, graphHeight) * 0.3;
      }
    });

    if (count > 250) return;
    var iterations = count > 120 ? 75 : 150;
    var margin = 58;
    for (var iteration = 0; iteration < iterations; iteration += 1) {
      var forces = graphNodes.map(function () { return { x: 0, y: 0 }; });
      for (var left = 0; left < count; left += 1) {
        for (var right = left + 1; right < count; right += 1) {
          var dx = graphNodes[right].x - graphNodes[left].x;
          var dy = graphNodes[right].y - graphNodes[left].y;
          var distanceSquared = Math.max(36, dx * dx + dy * dy);
          var distance = Math.sqrt(distanceSquared);
          var repulsion = Math.min(18, 7500 / distanceSquared);
          var fx = (dx / distance) * repulsion;
          var fy = (dy / distance) * repulsion;
          forces[left].x -= fx;
          forces[left].y -= fy;
          forces[right].x += fx;
          forces[right].y += fy;
        }
      }
      graphEdges.forEach(function (edge) {
        var source = edge.sourceNode;
        var target = edge.targetNode;
        var dx = target.x - source.x;
        var dy = target.y - source.y;
        var distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        var pull = (distance - 145) * 0.007;
        var fx = (dx / distance) * pull;
        var fy = (dy / distance) * pull;
        forces[source.index].x += fx;
        forces[source.index].y += fy;
        forces[target.index].x -= fx;
        forces[target.index].y -= fy;
      });
      graphNodes.forEach(function (node, index) {
        forces[index].x += (centerX - node.x) * 0.0018;
        forces[index].y += (centerY - node.y) * 0.0018;
        var cooling = 1 - iteration / iterations;
        node.x += Math.max(-12, Math.min(12, forces[index].x)) * cooling;
        node.y += Math.max(-12, Math.min(12, forces[index].y)) * cooling;
        node.x = Math.max(margin, Math.min(graphWidth - margin, node.x));
        node.y = Math.max(margin, Math.min(graphHeight - margin, node.y));
      });
    }
  }

  function buildGraph() {
    if (!cyEl) return;
    clear(cyEl);
    var bounds = cyEl.getBoundingClientRect();
    graphWidth = Math.max(520, Math.round(bounds.width || 900));
    graphHeight = Math.max(420, Math.round(bounds.height || 650));

    svg = svgElement("svg", "graph-canvas");
    svg.setAttribute("viewBox", "0 0 " + graphWidth + " " + graphHeight);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Interactive knowledge graph");
    svg.setAttribute("tabindex", "0");

    var defs = svgElement("defs");
    var marker = svgElement("marker");
    marker.setAttribute("id", "kd-arrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "6");
    marker.setAttribute("markerHeight", "6");
    marker.setAttribute("orient", "auto-start-reverse");
    marker.appendChild(svgElement("path"));
    marker.firstChild.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    defs.appendChild(marker);
    svg.appendChild(defs);

    viewport = svgElement("g", "graph-viewport");
    var edgeLayer = svgElement("g", "edge-layer");
    var edgeLabelLayer = svgElement("g", "edge-label-layer");
    var nodeLayer = svgElement("g", "node-layer");
    viewport.appendChild(edgeLayer);
    viewport.appendChild(edgeLabelLayer);
    viewport.appendChild(nodeLayer);
    svg.appendChild(viewport);
    cyEl.appendChild(svg);

    graphNodes = NODES.map(function (raw, index) {
      var item = { id: text(raw.id), raw: raw, index: index, radius: nodeSize(raw) / 2 };
      graphNodeById[item.id] = item;
      return item;
    });
    graphEdges = EDGES.map(function (raw, index) {
      var weight = typeof raw.weight === "number" && Number.isFinite(raw.weight) ? raw.weight : 0.5;
      return {
        raw: raw,
        id: present(raw.id) ? text(raw.id) : "edge-" + index,
        sourceNode: graphNodeById[text(raw.source)],
        targetNode: graphNodeById[text(raw.target)],
        width: 1 + Math.max(0, Math.min(1, weight)) * 4
      };
    }).filter(function (edge) { return edge.sourceNode && edge.targetNode; });

    layoutGraph();

    graphEdges.forEach(function (edge) {
      var line = svgElement("line", "graph-edge" + (edge.raw.type === "tension" ? " tension" : ""));
      line.setAttribute("stroke-width", edge.width);
      if (edge.raw.type !== "tension") line.setAttribute("marker-end", "url(#kd-arrow)");
      edge.line = line;
      edgeLayer.appendChild(line);

      if (present(edge.raw.type)) {
        var label = svgElement("text", "edge-label");
        label.textContent = text(edge.raw.type);
        edge.label = label;
        edgeLabelLayer.appendChild(label);
      }
    });

    graphNodes.forEach(function (node) {
      var group = svgElement("g", "graph-node" + (supersededSet[node.id] ? " superseded" : ""));
      group.setAttribute("data-node-id", node.id);
      group.setAttribute("role", "button");
      group.setAttribute("tabindex", "0");
      group.setAttribute("aria-label", text(node.raw.label) || node.id);

      var circle = svgElement("circle", "node-circle");
      circle.setAttribute("r", node.radius);
      circle.setAttribute("fill", colorFor(node.raw.cluster));
      group.appendChild(circle);

      var label = svgElement("text", "node-label");
      appendSvgLabel(label, text(node.raw.label) || node.id, node.radius);
      group.appendChild(label);
      node.group = group;
      node.circle = circle;
      node.label = label;
      nodeLayer.appendChild(group);

      group.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode(node.id, true);
        }
      });
    });

    updateAllGeometry();
    applyView();
    updateGraphState();
    installGraphInteraction();

    if (!graphNodes.length) {
      var empty = svgElement("text", "graph-empty");
      empty.setAttribute("x", graphWidth / 2);
      empty.setAttribute("y", graphHeight / 2);
      empty.textContent = "No graph nodes available";
      svg.appendChild(empty);
    }
  }

  function appendSvgLabel(label, value, radius) {
    var source = text(value).trim();
    var maximum = radius < 24 ? 10 : 15;
    var words = source.split(/\s+/).filter(Boolean);
    var lines = [];
    var current = "";
    words.forEach(function (word) {
      if (!current) current = word;
      else if ((current + " " + word).length <= maximum) current += " " + word;
      else {
        lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    if (!lines.length) lines.push("");
    if (lines.length > 3) {
      lines = lines.slice(0, 3);
      lines[2] = lines[2].slice(0, Math.max(1, maximum - 1)) + "…";
    }
    lines.forEach(function (line, index) {
      var span = svgElement("tspan");
      span.setAttribute("x", "0");
      span.setAttribute("dy", index ? "1.05em" : String(-0.5 * (lines.length - 1)) + "em");
      span.textContent = line;
      label.appendChild(span);
    });
  }

  function updateAllGeometry() {
    graphNodes.forEach(updateNodeGeometry);
    graphEdges.forEach(updateEdgeGeometry);
  }

  function updateNodeGeometry(node) {
    if (node.group) node.group.setAttribute("transform", "translate(" + node.x + " " + node.y + ")");
  }

  function edgeEndpoint(source, target, radius) {
    var dx = target.x - source.x;
    var dy = target.y - source.y;
    var distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    return { x: source.x + dx / distance * radius, y: source.y + dy / distance * radius };
  }

  function updateEdgeGeometry(edge) {
    var start = edgeEndpoint(edge.sourceNode, edge.targetNode, edge.sourceNode.radius + 2);
    var end = edgeEndpoint(edge.targetNode, edge.sourceNode, edge.targetNode.radius + 8);
    edge.line.setAttribute("x1", start.x);
    edge.line.setAttribute("y1", start.y);
    edge.line.setAttribute("x2", end.x);
    edge.line.setAttribute("y2", end.y);
    if (edge.label) {
      edge.label.setAttribute("x", (start.x + end.x) / 2);
      edge.label.setAttribute("y", (start.y + end.y) / 2 - 4);
    }
  }

  function updateIncidentEdges(node) {
    graphEdges.forEach(function (edge) {
      if (edge.sourceNode === node || edge.targetNode === node) updateEdgeGeometry(edge);
    });
  }

  function applyView() {
    if (viewport) viewport.setAttribute("transform", "translate(" + view.x + " " + view.y + ") scale(" + view.zoom + ")");
  }

  function svgPoint(clientX, clientY) {
    var bounds = svg.getBoundingClientRect();
    return {
      x: (clientX - bounds.left) * graphWidth / Math.max(1, bounds.width),
      y: (clientY - bounds.top) * graphHeight / Math.max(1, bounds.height)
    };
  }

  function graphPoint(clientX, clientY) {
    var point = svgPoint(clientX, clientY);
    return { x: (point.x - view.x) / view.zoom, y: (point.y - view.y) / view.zoom };
  }

  function installGraphInteraction() {
    svg.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      var group = event.target.closest ? event.target.closest(".graph-node") : null;
      var node = group ? graphNodeById[group.getAttribute("data-node-id")] : null;
      pointer = {
        mode: node ? "node" : "pan",
        node: node,
        id: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        lastClientX: event.clientX,
        lastClientY: event.clientY,
        moved: false
      };
      svg.setPointerCapture(event.pointerId);
      if (node) node.group.classList.add("dragging");
    });

    svg.addEventListener("pointermove", function (event) {
      if (!pointer || pointer.id !== event.pointerId) return;
      if (Math.abs(event.clientX - pointer.startClientX) > 3 ||
          Math.abs(event.clientY - pointer.startClientY) > 3) pointer.moved = true;
      if (pointer.mode === "node") {
        var point = graphPoint(event.clientX, event.clientY);
        pointer.node.x = point.x;
        pointer.node.y = point.y;
        updateNodeGeometry(pointer.node);
        updateIncidentEdges(pointer.node);
      } else {
        var bounds = svg.getBoundingClientRect();
        view.x += (event.clientX - pointer.lastClientX) * graphWidth / Math.max(1, bounds.width);
        view.y += (event.clientY - pointer.lastClientY) * graphHeight / Math.max(1, bounds.height);
        applyView();
      }
      pointer.lastClientX = event.clientX;
      pointer.lastClientY = event.clientY;
    });

    function endPointer(event) {
      if (!pointer || pointer.id !== event.pointerId) return;
      var state = pointer;
      pointer = null;
      if (state.node) state.node.group.classList.remove("dragging");
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      if (!state.moved && state.node) selectNode(state.node.id, false);
      else if (!state.moved && state.mode === "pan") {
        selectedId = null;
        updateGraphState();
        renderOverview();
      }
    }
    svg.addEventListener("pointerup", endPointer);
    svg.addEventListener("pointercancel", endPointer);

    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var point = svgPoint(event.clientX, event.clientY);
      var oldZoom = view.zoom;
      var newZoom = Math.max(0.45, Math.min(3.5, oldZoom * Math.exp(-event.deltaY * 0.001)));
      var graphX = (point.x - view.x) / oldZoom;
      var graphY = (point.y - view.y) / oldZoom;
      view.x = point.x - graphX * newZoom;
      view.y = point.y - graphY * newZoom;
      view.zoom = newZoom;
      applyView();
    }, { passive: false });
  }

  function updateGraphState() {
    var lowerQuery = query.trim().toLowerCase();
    graphNodes.forEach(function (node) {
      var haystack = (text(node.raw.label) + " " + text(node.raw.definition)).toLowerCase();
      node.hidden = Boolean(lowerQuery && haystack.indexOf(lowerQuery) === -1);
      node.group.classList.toggle("is-hidden", node.hidden);
      node.group.classList.toggle("is-dim", Boolean(activeCluster && text(node.raw.cluster) !== activeCluster));
      node.group.classList.toggle("is-selected", selectedId === node.id);
      node.group.setAttribute("aria-pressed", selectedId === node.id ? "true" : "false");
    });
    graphEdges.forEach(function (edge) {
      var hidden = edge.sourceNode.hidden || edge.targetNode.hidden;
      var connected = !activeCluster || text(edge.sourceNode.raw.cluster) === activeCluster ||
        text(edge.targetNode.raw.cluster) === activeCluster;
      edge.line.classList.toggle("is-hidden", hidden);
      edge.line.classList.toggle("is-dim", !connected);
      if (edge.label) {
        edge.label.classList.toggle("is-hidden", hidden);
        edge.label.classList.toggle("is-dim", !connected);
      }
    });
  }

  function buildLegend() {
    if (!legendEl) return;
    clear(legendEl);
    if (!CLUSTERS.length) {
      legendEl.hidden = true;
      return;
    }
    legendEl.hidden = false;
    legendEl.appendChild(element("div", "legend-title", "Clusters · click to focus"));
    CLUSTERS.forEach(function (cluster) {
      if (!present(cluster.id)) return;
      var cid = text(cluster.id);
      var item = element("button", "legend-item");
      item.type = "button";
      item.setAttribute("data-cluster", cid);
      item.setAttribute("aria-pressed", "false");
      var swatch = element("span", "legend-swatch");
      swatch.style.backgroundColor = colorFor(cid);
      item.appendChild(swatch);
      item.appendChild(element("span", "", clusterLabel(cluster)));
      item.addEventListener("click", function () {
        toggleCluster(cid === activeCluster ? null : cid);
      });
      legendEl.appendChild(item);
    });
  }

  function toggleCluster(clusterId) {
    activeCluster = clusterId;
    if (legendEl) {
      legendEl.querySelectorAll(".legend-item").forEach(function (item) {
        var active = item.getAttribute("data-cluster") === activeCluster;
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }
    updateGraphState();
  }

  function selectNode(id, center) {
    var node = graphNodeById[text(id)];
    if (!node) return;
    selectedId = node.id;
    if (center) {
      view.zoom = Math.max(1, view.zoom);
      view.x = graphWidth / 2 - node.x * view.zoom;
      view.y = graphHeight / 2 - node.y * view.zoom;
      applyView();
    }
    updateGraphState();
    renderDetail(node.raw);
  }

  window.__kgSelect = function (id) { selectNode(id, true); };

  function sectionLabel(parent, label) {
    parent.appendChild(element("div", "section-label", label));
  }

  function confidenceClass(value) {
    var normalized = text(value).toLowerCase();
    return normalized === "high" || normalized === "medium" || normalized === "low"
      ? " conf-" + normalized : "";
  }

  function appendTemporal(parent, temporal) {
    if (!isRecord(temporal)) return;
    var hasSource = present(temporal.source_date);
    var hasValidity = present(temporal.valid_from) || present(temporal.valid_until);
    var hasConfidence = present(temporal.temporal_confidence);
    if (!hasSource && !hasValidity && !hasConfidence) return;
    var line = element("div", "temporal-line");
    line.appendChild(document.createTextNode("◷ "));
    var needsSeparator = false;
    function separator() {
      if (needsSeparator) line.appendChild(document.createTextNode(" · "));
      needsSeparator = true;
    }
    if (hasSource) {
      separator();
      line.appendChild(document.createTextNode("source "));
      line.appendChild(element("code", "", temporal.source_date));
    }
    if (hasValidity) {
      separator();
      line.appendChild(document.createTextNode("valid "));
      line.appendChild(element("code", "", text(temporal.valid_from) || "?"));
      line.appendChild(document.createTextNode(" → "));
      line.appendChild(element("code", "", text(temporal.valid_until) || "now"));
    }
    if (hasConfidence) {
      separator();
      line.appendChild(document.createTextNode("(" + text(temporal.temporal_confidence) + ")"));
    }
    parent.appendChild(line);
  }

  function renderDetail(node) {
    if (!detailEl || !isRecord(node)) {
      renderOverview();
      return;
    }
    clear(detailEl);
    detailEl.appendChild(element("h2", "", node.label || node.id));

    var meta = element("div", "meta-row");
    var cluster = clusterById[text(node.cluster)];
    if (cluster) {
      var clusterBadge = element("span", "badge cluster", clusterLabel(cluster));
      clusterBadge.style.backgroundColor = colorFor(node.cluster);
      meta.appendChild(clusterBadge);
    }
    if (present(node.confidence)) {
      meta.appendChild(element("span", "badge" + confidenceClass(node.confidence),
        text(node.confidence) + " confidence"));
    }
    if (supersededSet[text(node.id)]) meta.appendChild(element("span", "badge superseded", "⚠ superseded"));
    if (meta.childNodes.length) detailEl.appendChild(meta);

    appendTemporal(detailEl, node.temporal);

    if (present(node.definition)) {
      sectionLabel(detailEl, "Definition");
      var definition = element("div", "detail-md");
      appendMarkdown(definition, node.definition);
      detailEl.appendChild(definition);
    }
    if (present(node.relevance)) {
      sectionLabel(detailEl, "Relevance");
      var relevance = element("div", "detail-md");
      appendMarkdown(relevance, node.relevance);
      detailEl.appendChild(relevance);
    }

    if (Array.isArray(node.statements) && node.statements.length) {
      sectionLabel(detailEl, "Statements");
      var statementWrap = element("div", "detail-md");
      var statements = element("ul");
      node.statements.forEach(function (statement) {
        var item = element("li");
        appendInlineMarkdown(item, statement);
        statements.appendChild(item);
      });
      statementWrap.appendChild(statements);
      detailEl.appendChild(statementWrap);
    }

    var citations = records(node.citations);
    if (citations.length) {
      sectionLabel(detailEl, "Citations");
      var citationList = element("ul", "cite-list");
      citations.forEach(function (citation) {
        var item = element("li");
        item.appendChild(element("span", "cn", "[" + (present(citation.n) ? text(citation.n) : "?") + "]"));
        item.appendChild(externalLink(citation.label || citation.source || "", citation.url));
        if (present(citation.locator)) item.appendChild(document.createTextNode(" (" + text(citation.locator) + ")"));
        citationList.appendChild(item);
      });
      detailEl.appendChild(citationList);
    }

    appendNodeRelationships(detailEl, "Cited by", function (edge) {
      return text(edge.target) === text(node.id) ? nodeById[text(edge.source)] : null;
    });
    appendNodeRelationships(detailEl, "Outgoing", function (edge) {
      return text(edge.source) === text(node.id) ? nodeById[text(edge.target)] : null;
    });
  }

  function appendNodeRelationships(parent, label, resolve) {
    var relationships = [];
    EDGES.forEach(function (edge) {
      var related = resolve(edge);
      if (related) relationships.push({ edge: edge, node: related });
    });
    if (!relationships.length) return;
    sectionLabel(parent, label);
    var list = element("ul", "link-list");
    relationships.forEach(function (relationship) {
      var item = element("li");
      if (present(relationship.edge.type)) {
        item.appendChild(element("span", "edge-type", relationship.edge.type));
      }
      var button = element("button", "node-link", relationship.node.label || relationship.node.id);
      button.type = "button";
      button.addEventListener("click", function () { selectNode(relationship.node.id, true); });
      item.appendChild(button);
      list.appendChild(item);
    });
    parent.appendChild(list);
  }

  function renderOverview() {
    if (!detailEl) return;
    clear(detailEl);
    detailEl.appendChild(element("h2", "", "Overview"));
    detailEl.appendChild(element("div", "pane-hint",
      "Click any node to inspect it. Use the legend to focus a cluster, or the search box to filter."));

    if (FACTS.length) {
      sectionLabel(detailEl, "Key facts");
      var table = element("table", "facts-table");
      var thead = element("thead");
      var header = element("tr");
      ["Statement", "Value", "Conf."].forEach(function (label) { header.appendChild(element("th", "", label)); });
      thead.appendChild(header);
      table.appendChild(thead);
      var tbody = element("tbody");
      FACTS.forEach(function (fact) {
        var row = element("tr");
        row.appendChild(element("td", "", fact.statement || fact.fact || ""));
        row.appendChild(element("td", "v", fact.value));
        row.appendChild(element("td", "", fact.confidence));
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
      detailEl.appendChild(table);
    }

    if (OPEN_Q.length) {
      sectionLabel(detailEl, "Open questions");
      var questions = element("ol", "oq-list");
      OPEN_Q.forEach(function (question) {
        var value = typeof question === "string" ? question
          : (isRecord(question) ? (question.question || question.text || "") : "");
        questions.appendChild(element("li", "", value));
      });
      detailEl.appendChild(questions);
    }

    if (!FACTS.length && !OPEN_Q.length) {
      detailEl.appendChild(element("p", "empty", "No facts or open questions in this graph."));
    }
  }

  if (searchEl) {
    searchEl.addEventListener("input", function () {
      query = text(searchEl.value);
      updateGraphState();
    });
  }

  setupHeader();
  renderOverview();
  buildGraph();
  buildLegend();
})();
