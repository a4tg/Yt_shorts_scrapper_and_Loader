const WORLD_WIDTH = 2200;
const WORLD_HEIGHT = 1500;
const TYPE_LABELS = { project: 'Проект', content: 'Контент', asset: 'Файлы', conversation: 'Чаты', review: 'Замечания', insight: 'Решения и риски', user: 'Команда', diagram: 'Схемы' };
const TYPE_ICONS = { project: 'AAP', content: '▤', asset: '◇', conversation: '◌', review: '!', insight: '◆', user: '●', diagram: '⌘' };

function node(tag, className, text) {
  const value = document.createElement(tag); if (className) value.className = className;
  if (text !== undefined) value.textContent = text; return value;
}

function svg(tag, attributes = {}) {
  const value = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attributes).forEach(([key, item]) => value.setAttribute(key, item)); return value;
}

function key() { return globalThis.crypto?.randomUUID?.().slice(0, 12) || `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function editable(context) { return ['owner', 'admin', 'editor'].includes(context?.workspace?.role); }
function clone(value) { return JSON.parse(JSON.stringify(value)); }

export function initProjectGraph({ bus, bridge }) {
  const root = document.querySelector('.project-graph-page'); if (!root) return {};
  document.querySelector('#graph-nav-button')?.classList.remove('hidden');
  const mapView = root.querySelector('.project-graph-map-view'); const diagramView = root.querySelector('.project-diagram-view');
  const graphViewport = root.querySelector('#project-graph-viewport'); const graphWorld = graphViewport.querySelector('.project-graph-world');
  const graphNodes = graphWorld.querySelector('.project-graph-nodes'); const graphEdges = graphWorld.querySelector('.project-graph-edges');
  const graphInspector = root.querySelector('#project-graph-inspector');
  const diagramViewport = root.querySelector('#project-diagram-viewport'); const diagramWorld = diagramViewport.querySelector('.project-diagram-world');
  const diagramNodes = diagramWorld.querySelector('.project-diagram-nodes'); const diagramEdges = diagramWorld.querySelector('.project-diagram-edges');
  const diagramForm = root.querySelector('.project-diagram-node-form');
  const state = {
    context: bridge.getContext?.() || {}, projectId: null, graph: { nodes: [], edges: [] },
    mapTransform: { x: 0, y: 0, zoom: 1 }, selectedGraphId: null, linkSource: null, linkTarget: null,
    filters: new Set(Object.keys(TYPE_LABELS)), source: null, refreshTimer: null,
    diagrams: [], diagram: null, selectedNodeKey: null, diagramTransform: { x: 0, y: 0, zoom: 1 },
    history: [], future: [], dirty: false, saveTimer: null, connectSource: null,
  };

  function showError(error) { bridge.notify?.(error?.message || 'Не удалось обновить карту.', 'error'); }
  function applyTransform(world, transform) { world.style.transform = `translate(${transform.x}px,${transform.y}px) scale(${transform.zoom})`; }

  function layoutGraph(useSaved = true) {
    const groups = {};
    for (const item of state.graph.nodes) (groups[item.entity_type] ||= []).push(item);
    const columns = { user: 80, content: 360, diagram: 700, project: 950, asset: 1130, review: 1500, conversation: 1780 };
    columns.insight = 1650;
    for (const [type, items] of Object.entries(groups)) {
      if (type === 'project') { items[0]._x = 970; items[0]._y = 690; continue; }
      const x = columns[type] || 1100; const spacing = Math.min(135, 1080 / Math.max(1, items.length));
      items.forEach((item, index) => { item._x = x + (index % 2) * 85; item._y = 170 + index * spacing; });
    }
    try {
      const saved = JSON.parse(localStorage.getItem(`aapGraphPositions:${state.projectId}`) || '{}');
      if (useSaved) for (const item of state.graph.nodes) if (saved[item.id]) { item._x = saved[item.id].x; item._y = saved[item.id].y; }
    } catch (_) { /* Keep deterministic layout. */ }
    renderGraph();
  }

  function saveGraphPositions() {
    const value = Object.fromEntries(state.graph.nodes.map((item) => [item.id, { x: item._x, y: item._y }]));
    localStorage.setItem(`aapGraphPositions:${state.projectId}`, JSON.stringify(value));
  }

  function edgePath(source, target) {
    const sx = source._x + 78, sy = source._y + 31, tx = target._x + 78, ty = target._y + 31;
    const bend = Math.max(70, Math.abs(tx - sx) * .45);
    return `M ${sx} ${sy} C ${sx + (tx >= sx ? bend : -bend)} ${sy}, ${tx - (tx >= sx ? bend : -bend)} ${ty}, ${tx} ${ty}`;
  }

  function renderGraphEdges() {
    graphEdges.replaceChildren(); graphEdges.setAttribute('viewBox', `0 0 ${WORLD_WIDTH} ${WORLD_HEIGHT}`);
    const byId = new Map(state.graph.nodes.map((item) => [item.id, item]));
    const defs = svg('defs'); const marker = svg('marker', { id: 'graph-arrow', markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: 'auto' }); marker.append(svg('path', { d: 'M0,0 L8,4 L0,8 Z' })); defs.append(marker); graphEdges.append(defs);
    for (const edge of state.graph.edges) {
      const source = byId.get(edge.source), target = byId.get(edge.target); if (!source || !target) continue;
      const group = svg('g', { class: `project-graph-edge${edge.manual ? ' manual' : ''}`, 'data-edge-id': edge.id });
      const path = svg('path', { d: edgePath(source, target), 'marker-end': 'url(#graph-arrow)' }); group.append(path);
      if (edge.label || edge.relation) { const x = (source._x + target._x) / 2 + 78, y = (source._y + target._y) / 2 + 25; const label = svg('text', { x, y }); label.textContent = edge.label || edge.relation.replaceAll('_', ' '); group.append(label); }
      graphEdges.append(group);
    }
  }

  function renderMinimap() {
    const minimap = graphViewport.querySelector('.project-graph-minimap'); minimap.replaceChildren();
    for (const item of state.graph.nodes) { const dot = node('i', item.entity_type); dot.style.left = `${(item._x / WORLD_WIDTH) * 100}%`; dot.style.top = `${(item._y / WORLD_HEIGHT) * 100}%`; minimap.append(dot); }
  }

  function renderGraph() {
    graphNodes.replaceChildren(); const query = root.querySelector('#project-graph-search').value.trim().toLocaleLowerCase('ru-RU');
    for (const item of state.graph.nodes) {
      if (item._x === undefined) { item._x = WORLD_WIDTH / 2 + Number(item.x || 0); item._y = WORLD_HEIGHT / 2 + Number(item.y || 0); }
      const visible = state.filters.has(item.entity_type) && (!query || `${item.label} ${item.subtitle}`.toLocaleLowerCase('ru-RU').includes(query));
      const card = node('button', `project-graph-node ${item.entity_type}${item.id === state.selectedGraphId ? ' selected' : ''}${visible ? '' : ' filtered'}`); card.type = 'button'; card.dataset.graphNodeId = item.id; card.style.transform = `translate(${item._x}px,${item._y}px)`;
      const icon = node('span', '', TYPE_ICONS[item.entity_type] || '•'); const copy = node('span'); copy.append(node('strong', '', item.label), node('small', '', item.subtitle || TYPE_LABELS[item.entity_type] || item.entity_type));
      if (item.status) copy.append(node('i', '', item.status)); card.append(icon, copy); graphNodes.append(card);
    }
    renderGraphEdges(); renderMinimap();
  }

  function renderGraphFilters() {
    const filters = root.querySelector('#project-graph-filters'); filters.replaceChildren();
    for (const [type, label] of Object.entries(TYPE_LABELS)) {
      const button = node('button', state.filters.has(type) ? 'active' : '', label); button.type = 'button'; button.dataset.graphFilter = type; filters.append(button);
    }
  }

  function graphNode(id) { return state.graph.nodes.find((item) => item.id === id); }
  function renderGraphInspector() {
    const selected = graphNode(state.selectedGraphId); graphInspector.replaceChildren();
    if (!selected) { const empty = node('div', 'project-graph-inspector-empty'); empty.append(node('span', '', '⌘'), node('strong', '', 'Выберите узел'), node('p', '', 'Здесь появятся свойства, связи и быстрые действия.')); graphInspector.append(empty); return; }
    const header = node('header'); header.append(node('span', selected.entity_type, TYPE_ICONS[selected.entity_type] || '•'));
    const copy = node('div'); copy.append(node('small', '', TYPE_LABELS[selected.entity_type] || selected.entity_type), node('strong', '', selected.label)); header.append(copy); graphInspector.append(header);
    const meta = node('dl'); for (const [label, value] of [['Тип', selected.kind], ['Статус', selected.status || '—'], ['ID', selected.entity_id]]) meta.append(node('dt', '', label), node('dd', '', value)); graphInspector.append(meta);
    const actions = node('div', 'project-graph-inspector-actions');
    const open = node('button', '', 'Открыть'); open.type = 'button'; open.dataset.graphOpen = selected.id; actions.append(open);
    if (editable(state.context)) { const link = node('button', '', 'Связать'); link.type = 'button'; link.dataset.graphLinkSource = selected.id; actions.append(link); } graphInspector.append(actions);
    const relationships = node('section'); relationships.append(node('strong', '', 'Связи'));
    for (const edge of state.graph.edges.filter((item) => item.source === selected.id || item.target === selected.id)) {
      const other = graphNode(edge.source === selected.id ? edge.target : edge.source); if (!other) continue;
      const row = node('button'); row.type = 'button'; row.dataset.graphSelect = other.id; row.append(node('span', '', edge.label || edge.relation), node('b', '', other.label));
      if (edge.manual && editable(state.context)) { const remove = node('i', '', '×'); remove.dataset.deleteLink = edge.id; row.append(remove); } relationships.append(row);
    }
    graphInspector.append(relationships);
    if (state.linkSource && state.linkTarget) renderLinkComposer();
  }

  function renderLinkComposer() {
    const existing = graphInspector.querySelector('.project-graph-link-form'); existing?.remove();
    const form = node('form', 'project-graph-link-form'); form.innerHTML = `<strong>Новая связь</strong><small>${graphNode(state.linkSource)?.label} → ${graphNode(state.linkTarget)?.label}</small><select name="relation"><option value="relates_to">Связано с</option><option value="depends_on">Зависит от</option><option value="blocks">Блокирует</option><option value="produces">Создаёт</option><option value="references">Ссылается на</option><option value="assigned_to">Назначено</option><option value="custom">Другое</option></select><input name="label" maxlength="160" placeholder="Подпись связи"><footer><button data-cancel-link type="button">Отмена</button><button class="primary" type="submit">Создать</button></footer>`;
    form.addEventListener('submit', createLink); form.querySelector('[data-cancel-link]').addEventListener('click', cancelLinking); graphInspector.append(form);
  }

  function startLinking(sourceId = state.selectedGraphId) {
    if (!editable(state.context)) return; state.linkSource = sourceId || null; state.linkTarget = null;
    root.classList.add('graph-linking'); bridge.notify?.(state.linkSource ? 'Выберите второй узел.' : 'Выберите первый узел.');
  }
  function cancelLinking() { state.linkSource = null; state.linkTarget = null; root.classList.remove('graph-linking'); renderGraphInspector(); }
  async function createLink(event) {
    event.preventDefault(); const source = graphNode(state.linkSource), target = graphNode(state.linkTarget); if (!source || !target) return;
    const form = event.currentTarget;
    try {
      await bridge.api(`/api/projects/${state.projectId}/entity-links`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_type: source.entity_type, source_id: source.entity_id, target_type: target.entity_type, target_id: target.entity_id, relation_type: form.elements.relation.value, label: form.elements.label.value.trim() || null }) });
      cancelLinking(); await loadGraph();
    } catch (error) { showError(error); }
  }

  function openGraphEntity(item) {
    if (item.entity_type === 'asset') bus.emit('asset:open', { assetId: item.entity_id, projectId: state.projectId });
    else if (item.entity_type === 'conversation') bus.emit('chat:open', { conversationId: item.entity_id, context: state.context });
    else if (item.entity_type === 'content') bridge.navigate?.('content', true);
    else if (item.entity_type === 'diagram') { setView('diagrams'); openDiagram(item.entity_id).catch(showError); }
    else if (item.entity_type === 'review' && item.extra?.attachment_id) {
      bus.emit('asset:open', { assetId: item.extra.attachment_id, projectId: state.projectId });
      bus.emit('review:focus', { reviewId: item.entity_id, attachmentId: item.extra.attachment_id });
    }
    else if (item.entity_type === 'insight') bridge.navigate?.('attention', true);
  }

  async function loadGraph() {
    if (!state.projectId) return; const graph = await bridge.api(`/api/projects/${state.projectId}/graph`);
    state.graph = graph; state.selectedGraphId = state.graph.nodes.some((item) => item.id === state.selectedGraphId) ? state.selectedGraphId : null;
    layoutGraph(); renderGraphFilters(); renderGraphInspector();
  }

  function fitGraph() {
    const bounds = graphViewport.getBoundingClientRect();
    if (bounds.width < 1 || bounds.height < 1) return false;
    const zoom = Math.min(.9, bounds.width / WORLD_WIDTH, bounds.height / WORLD_HEIGHT);
    Object.assign(state.mapTransform, { x: (bounds.width - WORLD_WIDTH * zoom) / 2, y: (bounds.height - WORLD_HEIGHT * zoom) / 2, zoom }); applyTransform(graphWorld, state.mapTransform);
    return true;
  }

  function connectRealtime() {
    state.source?.close(); if (!state.projectId || typeof EventSource === 'undefined') return;
    const source = new EventSource(`/api/projects/${state.projectId}/message-events`); state.source = source;
    for (const type of ['graph.link.created', 'graph.link.deleted', 'asset.version.created', 'asset.review.created', 'asset.review.updated', 'diagram.created', 'diagram.updated', 'diagram.deleted']) {
      source.addEventListener(type, () => { clearTimeout(state.refreshTimer); state.refreshTimer = setTimeout(() => { if (!state.dirty) { loadGraph().catch(showError); loadDiagrams().catch(showError); } }, 250); });
    }
    for (const type of ['insight.created', 'insight.updated', 'insight.dismissed', 'insights.extracted']) {
      source.addEventListener(type, () => { clearTimeout(state.refreshTimer); state.refreshTimer = setTimeout(() => loadGraph().catch(showError), 250); });
    }
  }

  function diagramSnapshot() { return state.diagram ? { nodes: clone(state.diagram.nodes), edges: clone(state.diagram.edges) } : null; }
  function remember() { const snapshot = diagramSnapshot(); if (!snapshot) return; state.history.push(snapshot); if (state.history.length > 60) state.history.shift(); state.future = []; }
  function restore(snapshot) { if (!snapshot || !state.diagram) return; state.diagram.nodes = clone(snapshot.nodes); state.diagram.edges = clone(snapshot.edges); state.selectedNodeKey = null; markDirty(false); renderDiagram(); }
  function undo() { if (!state.history.length) return; state.future.push(diagramSnapshot()); restore(state.history.pop()); }
  function redo() { if (!state.future.length) return; state.history.push(diagramSnapshot()); restore(state.future.pop()); }

  function diagramNode(nodeKey) { return state.diagram?.nodes.find((item) => item.key === nodeKey); }
  function renderDiagramEdges() {
    diagramEdges.replaceChildren(); diagramEdges.setAttribute('viewBox', `0 0 ${WORLD_WIDTH} ${WORLD_HEIGHT}`);
    const defs = svg('defs'); const marker = svg('marker', { id: 'diagram-arrow', markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: 'auto' }); marker.append(svg('path', { d: 'M0,0 L8,4 L0,8 Z' })); defs.append(marker); diagramEdges.append(defs);
    for (const edge of state.diagram?.edges || []) {
      const source = diagramNode(edge.source_key), target = diagramNode(edge.target_key); if (!source || !target) continue;
      const sx = source.x + source.width, sy = source.y + source.height / 2, tx = target.x, ty = target.y + target.height / 2;
      const bend = Math.max(70, Math.abs(tx - sx) / 2); const group = svg('g', { class: `project-diagram-edge ${edge.edge_type}`, 'data-edge-id': edge.id || '' });
      group.append(svg('path', { d: `M${sx} ${sy} C${sx + bend} ${sy},${tx - bend} ${ty},${tx} ${ty}`, 'marker-end': 'url(#diagram-arrow)' }));
      if (edge.label) { const text = svg('text', { x: (sx + tx) / 2, y: (sy + ty) / 2 - 7 }); text.textContent = edge.label; group.append(text); } diagramEdges.append(group);
    }
  }

  function renderDiagram() {
    diagramNodes.replaceChildren(); const diagram = state.diagram;
    root.querySelector('#project-diagram-title').value = diagram?.title || '';
    root.querySelector('#project-diagram-visibility').value = diagram?.visibility || 'team';
    root.querySelector('#project-diagram-visibility').disabled = !diagram || !editable(state.context);
    if (!diagram) { renderDiagramInspector(); diagramEdges.replaceChildren(); return; }
    for (const item of diagram.nodes) {
      const card = node('button', `project-diagram-node ${item.kind}${item.key === state.selectedNodeKey ? ' selected' : ''}`); card.type = 'button'; card.dataset.diagramNodeKey = item.key;
      card.style.transform = `translate(${item.x}px,${item.y}px)`; card.style.width = `${item.width}px`; card.style.height = `${item.height}px`; if (item.color) card.style.setProperty('--node-color', item.color);
      card.append(node('i'), node('strong', '', item.title)); if (item.description) card.append(node('small', '', item.description)); card.append(node('span', 'diagram-port input'), node('span', 'diagram-port output')); diagramNodes.append(card);
    }
    renderDiagramEdges(); renderDiagramInspector(); updateDiagramStatus();
  }

  function renderDiagramInspector() {
    const item = diagramNode(state.selectedNodeKey); const empty = root.querySelector('.project-diagram-inspector-empty');
    empty.classList.toggle('hidden', Boolean(item)); diagramForm.classList.toggle('hidden', !item); if (!item) return;
    diagramForm.elements.title.value = item.title; diagramForm.elements.description.value = item.description || ''; diagramForm.elements.kind.value = item.kind; diagramForm.elements.color.value = item.color || '#6f61d9';
    diagramForm.querySelector('[data-diagram-action="connect"]').textContent = state.connectSource ? 'Выберите цель…' : 'Соединить';
    diagramForm.querySelector('.project-diagram-edge-list')?.remove();
    const edgeList = node('section', 'project-diagram-edge-list'); edgeList.append(node('strong', '', 'Связи узла'));
    for (const edge of state.diagram.edges.filter((value) => value.source_key === item.key || value.target_key === item.key)) {
      const row = node('div'); const otherKey = edge.source_key === item.key ? edge.target_key : edge.source_key;
      row.append(node('small', '', `${edge.source_key === item.key ? '→' : '←'} ${diagramNode(otherKey)?.title || otherKey}`));
      const type = node('select');
      for (const [value, labelText] of [['default', 'Обычная'], ['success', 'Успех'], ['failure', 'Ошибка'], ['conditional', 'Условие']]) type.append(new Option(labelText, value));
      type.value = edge.edge_type; const label = node('input'); label.maxLength = 160; label.placeholder = 'Подпись'; label.value = edge.label || '';
      const remove = node('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', 'Удалить связь');
      type.addEventListener('change', () => { remember(); edge.edge_type = type.value; markDirty(); renderDiagramEdges(); });
      label.addEventListener('change', () => { remember(); edge.label = label.value.trim(); markDirty(); renderDiagramEdges(); });
      remove.addEventListener('click', () => { remember(); state.diagram.edges = state.diagram.edges.filter((value) => value !== edge); markDirty(); renderDiagram(); });
      row.append(type, label, remove); edgeList.append(row);
    }
    if (edgeList.children.length === 1) edgeList.append(node('small', '', 'Связей пока нет'));
    diagramForm.insertBefore(edgeList, diagramForm.querySelector('footer'));
  }

  function markDirty(push = true) {
    state.dirty = true; if (push) clearTimeout(state.saveTimer);
    if (push && editable(state.context)) state.saveTimer = setTimeout(() => saveDiagram(true).catch(showError), 1400); updateDiagramStatus();
  }
  function updateDiagramStatus(text = null) { const status = root.querySelector('.project-diagram-status'); status.querySelector('span').textContent = text || (state.diagram ? state.dirty ? 'Есть несохранённые изменения' : 'Все изменения сохранены' : 'Выберите схему или создайте новую'); status.querySelector('small').textContent = state.diagram ? `${state.diagram.nodes.length} узлов · ${state.diagram.edges.length} связей` : ''; }

  function addDiagramNode(kind) {
    if (!state.diagram || !editable(state.context)) return; remember(); const bounds = diagramViewport.getBoundingClientRect();
    const x = (bounds.width / 2 - state.diagramTransform.x) / state.diagramTransform.zoom - 90;
    const y = (bounds.height / 2 - state.diagramTransform.y) / state.diagramTransform.zoom - 40;
    const item = { key: `node-${key()}`, kind, title: { start: 'Старт', end: 'Финиш', decision: 'Условие', document: 'Документ', note: 'Заметка' }[kind] || 'Новая задача', description: '', x: Math.round(x / 20) * 20, y: Math.round(y / 20) * 20, width: kind === 'decision' ? 190 : 180, height: 80, color: '#6f61d9', entity_type: null, entity_id: null, extra: {} };
    state.diagram.nodes.push(item); state.selectedNodeKey = item.key; markDirty(); renderDiagram();
  }

  async function loadDiagrams(preferredId = null) {
    if (!state.projectId) return; state.diagrams = await bridge.api(`/api/projects/${state.projectId}/diagrams`); renderDiagramList();
    const routeId = new URLSearchParams(location.hash.split('?')[1] || '').get('diagram'); const target = preferredId || routeId || state.diagram?.id || state.diagrams[0]?.id;
    if (target && state.diagrams.some((item) => item.id === target) && state.diagram?.id !== target) await openDiagram(target);
  }

  function renderDiagramList() {
    const list = root.querySelector('.project-diagram-list'); list.replaceChildren();
    for (const item of state.diagrams) { const button = node('button', item.id === state.diagram?.id ? 'active' : ''); button.type = 'button'; button.dataset.diagramId = item.id; button.append(node('strong', '', item.title), node('small', '', `${item.visibility === 'client' ? 'Видит клиент' : 'Только команда'} · ${new Date(item.updated_at).toLocaleString('ru-RU')}`)); list.append(button); }
    if (!state.diagrams.length) list.append(node('p', '', 'Создайте первую схему или перенесите сюда процесс согласования.'));
    root.querySelectorAll('[data-diagram-action="create"],[data-diagram-action="approval-template"]').forEach((button) => button.classList.toggle('hidden', !editable(state.context)));
  }

  async function createDiagram(template = 'blank') {
    const created = await bridge.api(`/api/projects/${state.projectId}/diagrams`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: template === 'approval' ? 'Процесс согласования' : 'Новая блок-схема', diagram_type: 'flowchart', visibility: 'team', template }) });
    await loadDiagrams(created.id);
  }

  async function openDiagram(id) {
    if (state.dirty && !confirm('Открыть другую схему и отбросить несохранённые изменения?')) return;
    state.diagram = await bridge.api(`/api/diagrams/${id}`); state.selectedNodeKey = null; state.history = []; state.future = []; state.dirty = false; Object.assign(state.diagramTransform, { x: 20, y: 20, zoom: 1 }); applyTransform(diagramWorld, state.diagramTransform); renderDiagram(); renderDiagramList();
    const base = location.hash.split('?')[0] || '#/graph'; history.replaceState(history.state, '', `${location.pathname}${location.search}${base}?diagram=${id}`);
  }

  async function saveDiagram(silent = false) {
    if (!state.diagram || !editable(state.context)) return; clearTimeout(state.saveTimer);
    state.diagram.title = root.querySelector('#project-diagram-title').value.trim() || 'Без названия';
    state.diagram.visibility = root.querySelector('#project-diagram-visibility').value;
    const payload = { title: state.diagram.title, description: state.diagram.description, diagram_type: state.diagram.diagram_type, visibility: state.diagram.visibility, viewport: state.diagramTransform, nodes: state.diagram.nodes.map(({ id, ...item }) => item), edges: state.diagram.edges.map(({ id, ...item }) => item) };
    state.diagram = await bridge.api(`/api/diagrams/${state.diagram.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); state.dirty = false; renderDiagram(); await loadDiagrams(state.diagram.id); if (!silent) bridge.notify?.('Схема сохранена.');
  }

  function fitDiagram() {
    if (!state.diagram?.nodes.length) return; const bounds = diagramViewport.getBoundingClientRect();
    const minX = Math.min(...state.diagram.nodes.map((item) => item.x)), maxX = Math.max(...state.diagram.nodes.map((item) => item.x + item.width));
    const minY = Math.min(...state.diagram.nodes.map((item) => item.y)), maxY = Math.max(...state.diagram.nodes.map((item) => item.y + item.height));
    const zoom = Math.min(1.2, (bounds.width - 80) / Math.max(1, maxX - minX), (bounds.height - 80) / Math.max(1, maxY - minY)); Object.assign(state.diagramTransform, { x: 40 - minX * zoom, y: 40 - minY * zoom, zoom }); applyTransform(diagramWorld, state.diagramTransform);
  }

  function deleteSelectedNode() {
    if (!state.diagram || !state.selectedNodeKey) return; remember(); const selected = state.selectedNodeKey;
    state.diagram.nodes = state.diagram.nodes.filter((item) => item.key !== selected); state.diagram.edges = state.diagram.edges.filter((edge) => edge.source_key !== selected && edge.target_key !== selected); state.selectedNodeKey = null; markDirty(); renderDiagram();
  }
  function connectSelected() { if (!state.selectedNodeKey) return; state.connectSource = state.selectedNodeKey; root.classList.add('diagram-connecting'); renderDiagramInspector(); }
  function finishConnection(targetKey) { if (!state.connectSource || state.connectSource === targetKey) return; remember(); if (!state.diagram.edges.some((edge) => edge.source_key === state.connectSource && edge.target_key === targetKey)) state.diagram.edges.push({ source_key: state.connectSource, target_key: targetKey, label: '', edge_type: 'default', extra: {} }); state.connectSource = null; root.classList.remove('diagram-connecting'); markDirty(); renderDiagram(); }

  function exportDiagram() {
    if (!state.diagram) return; const blob = new Blob([JSON.stringify(state.diagram, null, 2)], { type: 'application/json' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${state.diagram.title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'diagram'}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  async function deleteDiagram() {
    if (!state.diagram || !editable(state.context) || !confirm(`Удалить схему «${state.diagram.title}»?`)) return;
    clearTimeout(state.saveTimer);
    await bridge.api(`/api/diagrams/${state.diagram.id}`, { method: 'DELETE' });
    state.diagram = null; state.dirty = false; state.selectedNodeKey = null; renderDiagram(); await loadDiagrams();
  }

  function setView(view) {
    mapView.classList.toggle('hidden', view !== 'map'); diagramView.classList.toggle('hidden', view !== 'diagrams'); root.querySelectorAll('[data-graph-view]').forEach((button) => button.classList.toggle('active', button.dataset.graphView === view));
    if (view === 'diagrams') loadDiagrams().catch(showError); else setTimeout(fitGraph);
  }

  root.addEventListener('click', async (event) => {
    const view = event.target.closest('[data-graph-view]')?.dataset.graphView; if (view) setView(view);
    const filter = event.target.closest('[data-graph-filter]')?.dataset.graphFilter; if (filter) { state.filters.has(filter) ? state.filters.delete(filter) : state.filters.add(filter); renderGraphFilters(); renderGraph(); }
    const graphAction = event.target.closest('[data-graph-action]')?.dataset.graphAction;
    if (graphAction === 'layout') { layoutGraph(false); saveGraphPositions(); } else if (graphAction === 'fit') fitGraph(); else if (graphAction === 'refresh') loadGraph().catch(showError); else if (graphAction === 'link') startLinking();
    const selectId = event.target.closest('[data-graph-select]')?.dataset.graphSelect; if (selectId) { state.selectedGraphId = selectId; renderGraph(); renderGraphInspector(); }
    const openId = event.target.closest('[data-graph-open]')?.dataset.graphOpen; if (openId) openGraphEntity(graphNode(openId));
    const sourceId = event.target.closest('[data-graph-link-source]')?.dataset.graphLinkSource; if (sourceId) startLinking(sourceId);
    const deleteLink = event.target.closest('[data-delete-link]')?.dataset.deleteLink; if (deleteLink) { event.stopPropagation(); try { await bridge.api(`/api/entity-links/${deleteLink}`, { method: 'DELETE' }); await loadGraph(); } catch (error) { showError(error); } }
    const graphCard = event.target.closest('[data-graph-node-id]');
    if (graphCard) {
      const id = graphCard.dataset.graphNodeId;
      if (root.classList.contains('graph-linking')) { if (!state.linkSource) state.linkSource = id; else if (id !== state.linkSource) { state.linkTarget = id; state.selectedGraphId = id; renderGraphInspector(); } }
      else { state.selectedGraphId = id; renderGraph(); renderGraphInspector(); }
    }
    const diagramId = event.target.closest('[data-diagram-id]')?.dataset.diagramId; if (diagramId) { root.classList.remove('show-diagram-library'); openDiagram(diagramId).catch(showError); }
    const kind = event.target.closest('[data-node-kind]')?.dataset.nodeKind; if (kind) addDiagramNode(kind);
    const diagramAction = event.target.closest('[data-diagram-action]')?.dataset.diagramAction;
    if (diagramAction === 'create') createDiagram().catch(showError); else if (diagramAction === 'approval-template') createDiagram('approval').catch(showError); else if (diagramAction === 'save') saveDiagram().catch(showError); else if (diagramAction === 'undo') undo(); else if (diagramAction === 'redo') redo(); else if (diagramAction === 'fit') fitDiagram(); else if (diagramAction === 'export') exportDiagram(); else if (diagramAction === 'delete') deleteDiagram().catch(showError); else if (diagramAction === 'library') root.classList.toggle('show-diagram-library'); else if (diagramAction === 'delete-node') deleteSelectedNode(); else if (diagramAction === 'connect') connectSelected();
    const diagramCard = event.target.closest('[data-diagram-node-key]');
    if (diagramCard) { const selected = diagramCard.dataset.diagramNodeKey; if (state.connectSource) finishConnection(selected); else { state.selectedNodeKey = selected; renderDiagram(); } }
  });

  root.querySelector('#project-graph-search').addEventListener('input', renderGraph);
  root.querySelector('#project-diagram-title').addEventListener('input', () => { if (state.diagram) { state.diagram.title = root.querySelector('#project-diagram-title').value; markDirty(); } });
  root.querySelector('#project-diagram-visibility').addEventListener('change', () => { if (state.diagram) { state.diagram.visibility = root.querySelector('#project-diagram-visibility').value; markDirty(); } });
  diagramForm.addEventListener('input', () => { const item = diagramNode(state.selectedNodeKey); if (!item) return; item.title = diagramForm.elements.title.value; item.description = diagramForm.elements.description.value; item.kind = diagramForm.elements.kind.value; item.color = diagramForm.elements.color.value; markDirty(); renderDiagram(); });

  let drag = null;
  graphNodes.addEventListener('pointerdown', (event) => { const card = event.target.closest('[data-graph-node-id]'); if (!card || root.classList.contains('graph-linking')) return; const item = graphNode(card.dataset.graphNodeId); drag = { type: 'graph-node', item, x: event.clientX, y: event.clientY, startX: item._x, startY: item._y }; card.setPointerCapture(event.pointerId); event.stopPropagation(); });
  diagramNodes.addEventListener('pointerdown', (event) => { const card = event.target.closest('[data-diagram-node-key]'); if (!card || !editable(state.context)) return; const item = diagramNode(card.dataset.diagramNodeKey); remember(); drag = { type: 'diagram-node', item, x: event.clientX, y: event.clientY, startX: item.x, startY: item.y }; card.setPointerCapture(event.pointerId); event.stopPropagation(); });
  for (const [viewport, transform, world, type] of [[graphViewport, state.mapTransform, graphWorld, 'graph-pan'], [diagramViewport, state.diagramTransform, diagramWorld, 'diagram-pan']]) {
    viewport.addEventListener('pointerdown', (event) => { if (event.target !== viewport && !event.target.classList.contains(type === 'graph-pan' ? 'project-graph-world' : 'project-diagram-world')) return; drag = { type, x: event.clientX, y: event.clientY, startX: transform.x, startY: transform.y }; viewport.setPointerCapture(event.pointerId); });
    viewport.addEventListener('wheel', (event) => { event.preventDefault(); const next = Math.max(.2, Math.min(2.5, transform.zoom * (event.deltaY > 0 ? .9 : 1.1))); const bounds = viewport.getBoundingClientRect(); const px = event.clientX - bounds.left, py = event.clientY - bounds.top; transform.x = px - ((px - transform.x) / transform.zoom) * next; transform.y = py - ((py - transform.y) / transform.zoom) * next; transform.zoom = next; applyTransform(world, transform); }, { passive: false });
  }
  window.addEventListener('pointermove', (event) => { if (!drag) return; const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (drag.type === 'graph-node') { drag.item._x = drag.startX + dx / state.mapTransform.zoom; drag.item._y = drag.startY + dy / state.mapTransform.zoom; renderGraph(); }
    else if (drag.type === 'diagram-node') { drag.item.x = Math.round((drag.startX + dx / state.diagramTransform.zoom) / 20) * 20; drag.item.y = Math.round((drag.startY + dy / state.diagramTransform.zoom) / 20) * 20; renderDiagram(); }
    else { const transform = drag.type === 'graph-pan' ? state.mapTransform : state.diagramTransform; const world = drag.type === 'graph-pan' ? graphWorld : diagramWorld; transform.x = drag.startX + dx; transform.y = drag.startY + dy; applyTransform(world, transform); }
  });
  window.addEventListener('pointerup', () => { if (drag?.type === 'diagram-node') markDirty(); else if (drag?.type === 'graph-node') saveGraphPositions(); drag = null; });
  window.addEventListener('keydown', (event) => {
    if (state.context?.page !== 'graph' || event.target.matches('input,textarea,select')) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveDiagram().catch(showError); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    else if (event.key === 'Delete' && state.selectedNodeKey) deleteSelectedNode();
    else if (event.key === 'Escape') { cancelLinking(); state.connectSource = null; root.classList.remove('diagram-connecting'); }
  });

  async function activate(context) {
    state.context = context || bridge.getContext?.() || {}; const projectId = state.context?.project?.id;
    if (!projectId) return;
    const projectChanged = projectId !== state.projectId;
    if (projectChanged) {
      state.projectId = projectId; state.diagram = null; state.dirty = false; connectRealtime();
      await Promise.all([loadGraph(), loadDiagrams()]);
    }
    if (state.context.page === 'graph') {
      requestAnimationFrame(() => {
        if (!fitGraph()) setTimeout(fitGraph, 80);
      });
    }
  }
  bus.on('context:change', (context) => { state.context = context; if (context.page === 'graph') activate(context).catch(showError); });
  bus.on('route:change', ({ page, params }) => { if (page === 'graph') { activate(bridge.getContext?.()).then(() => {
    if (params.diagram) { setView('diagrams'); openDiagram(params.diagram).catch(showError); }
    else if (params.insight && graphNode(`insight:${params.insight}`)) {
      setView('map'); state.selectedGraphId = `insight:${params.insight}`; renderGraph(); renderGraphInspector();
      graphNodes.querySelector(`[data-graph-node-id="insight:${params.insight}"]`)?.focus();
    } else setView('map');
  }).catch(showError); } });
  const initial = bridge.getContext?.(); if (initial?.project?.id) activate(initial).catch(showError);
  applyTransform(graphWorld, state.mapTransform); applyTransform(diagramWorld, state.diagramTransform); renderGraphFilters();
  return { destroy: () => { state.source?.close(); clearTimeout(state.saveTimer); clearTimeout(state.refreshTimer); } };
}
