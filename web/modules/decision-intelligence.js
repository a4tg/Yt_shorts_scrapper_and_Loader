const KIND = {
  decision: { label: 'Решение', icon: '◆' }, commitment: { label: 'Обязательство', icon: '✓' },
  action: { label: 'Действие', icon: '→' }, risk: { label: 'Риск', icon: '!' }, question: { label: 'Вопрос', icon: '?' },
};
const PRIORITY = { low: 'Низкий', normal: 'Обычный', high: 'Высокий', urgent: 'Срочный' };
const STATS = [
  ['open_insights', 'Сигналы', '◆', ''], ['urgent', 'Срочные', '!', 'urgent'],
  ['open_reviews', 'Замечания', '◉', ''], ['overdue', 'Просрочки', '⌛', 'warning'],
  ['changes_requested', 'Нужны правки', '↻', 'warning'], ['unread_messages', 'Непрочитано', '◌', ''],
];

function node(tag, className, text) {
  const value = document.createElement(tag); if (className) value.className = className;
  if (text !== undefined) value.textContent = text; return value;
}

function canAnalyze(context) { return ['owner', 'admin', 'editor'].includes(context?.workspace?.role); }
function canContribute(context) { return canAnalyze(context) || context?.workspace?.role === 'client'; }
function dateText(value) { return value ? new Intl.DateTimeFormat('ru', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) : null; }
function scoreLabel(score) { return score >= 70 ? 'Нужна немедленная реакция' : score >= 40 ? 'Есть узкие места' : score >= 15 ? 'Стоит проверить' : 'Всё спокойно'; }

export function initDecisionIntelligence({ bus, bridge, router }) {
  const root = document.querySelector('.decision-page'); if (!root) return {};
  document.querySelector('#attention-nav-button')?.classList.remove('hidden');
  const dialog = root.querySelector('#decision-create-dialog');
  const state = { context: bridge.getContext?.() || {}, projectId: null, data: null, filter: 'all', members: [], source: null, timer: null, loading: false };

  function errorMessage(error) { bridge.notify?.(error?.message || 'Не удалось обновить центр внимания.', 'error'); }
  function setBusy(value) { state.loading = value; root.classList.toggle('decision-loading', value); root.querySelectorAll('[data-intel-action]').forEach((button) => { button.disabled = value; }); }

  function renderScore() {
    const score = Number(state.data?.score || 0); const ring = root.querySelector('.attention-score-ring');
    root.querySelector('#attention-score').textContent = score; root.querySelector('#attention-label').textContent = scoreLabel(score);
    ring.style.setProperty('--score', score); ring.style.setProperty('--score-color', score >= 70 ? '#e25d78' : score >= 40 ? '#e5a348' : '#55c39a');
    const stats = root.querySelector('#attention-stats'); stats.replaceChildren();
    for (const [key, label, icon, tone] of STATS) {
      const card = node('article', `attention-stat ${tone}`); card.append(node('i', '', icon));
      const copy = node('div'); copy.append(node('strong', '', String(state.data?.stats?.[key] || 0)), node('small', '', label)); card.append(copy); stats.append(card);
    }
    const count = (state.data?.stats?.urgent || 0) + (state.data?.stats?.overdue || 0) + (state.data?.stats?.changes_requested || 0);
    const badge = document.querySelector('#attention-nav-badge'); badge.textContent = String(count); badge.classList.toggle('hidden', !count);
  }

  function renderBriefing() {
    const host = root.querySelector('#project-briefing'); const briefing = state.data?.latest_briefing;
    if (!briefing) { host.className = 'project-briefing-empty'; host.textContent = 'Сводка появится после первого анализа проекта.'; return; }
    host.className = 'project-briefing'; host.replaceChildren();
    host.append(node('div', 'briefing-summary', briefing.summary));
    for (const [key, title] of [['highlights', 'Решения'], ['risks', 'Риски'], ['next_actions', 'Следующие шаги']]) {
      const column = node('div', 'briefing-column'); column.append(node('strong', '', title));
      const items = briefing[key] || []; if (!items.length) column.append(node('span', '', 'Нет активных пунктов'));
      for (const item of items.slice(0, 5)) column.append(node('span', '', item.title || item.detail || 'Сигнал'));
      host.append(column);
    }
    host.append(node('small', 'briefing-meta', `${briefing.provider === 'openai' ? 'AI-сводка' : 'Сводка по правилам'} · ${dateText(briefing.generated_at) || ''}`));
  }

  async function patchInsight(id, payload) {
    await bridge.api(`/api/insights/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    await load();
  }

  function renderSignals() {
    const host = root.querySelector('#decision-signal-list'); host.replaceChildren();
    const items = (state.data?.insights || []).filter((item) => state.filter === 'all' || item.kind === state.filter);
    root.querySelector('#decision-result-count').textContent = `${items.length} активных`;
    if (!items.length) { host.append(node('div', 'decision-empty', 'В этом разделе пока нет активных сигналов. Запустите анализ или добавьте запись вручную.')); return; }
    for (const item of items) {
      const card = node('article', `decision-signal ${item.kind}`); card.dataset.insightId = item.id;
      card.append(node('span', 'decision-kind', KIND[item.kind]?.icon || '◆'));
      const copy = node('div'); copy.append(node('h4', '', item.title)); if (item.description && item.description !== item.title) copy.append(node('p', '', item.description));
      const meta = node('div', 'decision-signal-meta'); meta.append(node('span', item.priority, PRIORITY[item.priority] || item.priority), node('span', '', `Влияние ${item.impact_score}`));
      if (item.assignee) meta.append(node('span', '', item.assignee.name)); if (item.due_at) meta.append(node('span', '', `до ${dateText(item.due_at)}`));
      meta.append(node('span', '', KIND[item.kind]?.label || item.kind)); copy.append(meta); card.append(copy);
      const actions = node('div', 'decision-signal-actions'); const status = node('select');
      for (const [value, label] of [['open', 'Открыт'], ['in_progress', 'В работе'], ['done', 'Готово'], ['dismissed', 'Скрыть']]) { const option = node('option', '', label); option.value = value; option.selected = item.status === value; status.append(option); }
      status.dataset.insightStatus = item.id;
      status.disabled = !(canAnalyze(state.context) || item.is_own || item.assignee?.id === state.context?.user?.id);
      actions.append(status); const graph = node('button', '', 'На карте'); graph.type = 'button'; graph.dataset.insightGraph = item.id; actions.append(graph); card.append(actions); host.append(card);
    }
  }

  function renderQueue() {
    const host = root.querySelector('#attention-queue'); host.replaceChildren(); const seen = new Set();
    const items = (state.data?.items || []).filter((item) => { const key = `${item.type}:${item.id}`; if (seen.has(key)) return false; seen.add(key); return true; }).slice(0, 30);
    if (!items.length) { host.append(node('div', 'decision-empty', 'Очередь пуста — срочных точек внимания нет.')); return; }
    for (const item of items) {
      const card = node('article', `attention-queue-item ${item.priority || ''}`); card.dataset.queueType = item.type; card.dataset.queueId = item.id; if (item.attachment_id) card.dataset.attachmentId = item.attachment_id;
      card.append(node('i')); const copy = node('div'); copy.append(node('strong', '', item.title), node('small', '', item.detail || ({ review: 'Открытое замечание', overdue: 'Просроченный материал', insight: 'Сигнал проекта' }[item.type] || 'Требует реакции'))); card.append(copy, node('b', '', item.due_at ? dateText(item.due_at) : `+${item.impact_score || 0}`)); host.append(card);
    }
  }

  function render() { if (!state.data) return; renderScore(); renderBriefing(); renderSignals(); renderQueue(); }

  async function load() {
    if (!state.projectId || state.loading) return; setBusy(true);
    try { state.data = await bridge.api(`/api/projects/${state.projectId}/attention`); render(); }
    finally { setBusy(false); }
  }

  function renderAssignees() {
    const select = dialog.querySelector('[name="assignee_user_id"]');
    const current = select.value;
    const visibility = dialog.querySelector('[name="visibility"]').value;
    select.replaceChildren(node('option', '', 'Без ответственного')); select.firstElementChild.value = '';
    for (const member of state.members) {
      if (visibility === 'team' && member.role === 'client') continue;
      const option = node('option', '', member.display_name || member.email); option.value = member.user_id; select.append(option);
    }
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  async function loadMembers() {
    const workspaceId = state.context?.workspace?.id; if (!workspaceId) return;
    state.members = await bridge.api(`/api/workspaces/${workspaceId}/members`);
    renderAssignees();
  }

  async function extract(useAI = false) {
    if (!canAnalyze(state.context)) { bridge.notify?.('Автоматический анализ доступен редактору проекта.', 'error'); return; }
    setBusy(true);
    try {
      const result = await bridge.api(`/api/projects/${state.projectId}/insights/extract`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_ai: useAI }) });
      bridge.notify?.(`Анализ завершён: новых сигналов ${result.inserted + Number(result.ai?.inserted || 0)}.`); state.data = await bridge.api(`/api/projects/${state.projectId}/attention`); render();
    } finally { setBusy(false); }
  }

  async function briefing(useAI) {
    setBusy(true);
    try { await bridge.api(`/api/projects/${state.projectId}/briefings`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ use_ai: useAI, visibility: state.context?.workspace?.role === 'client' ? 'client' : 'team' }) }); state.data = await bridge.api(`/api/projects/${state.projectId}/attention`); render(); bridge.notify?.('Сводка проекта обновлена.'); }
    finally { setBusy(false); }
  }

  async function createInsight(event) {
    event.preventDefault(); const submit = event.submitter; if (!submit?.matches('[data-intel-submit]')) { dialog.close(); return; }
    const form = event.currentTarget; const values = new FormData(form); const due = values.get('due_at');
    const payload = { kind: values.get('kind'), priority: values.get('priority'), title: values.get('title').trim(), description: values.get('description').trim() || null, assignee_user_id: values.get('assignee_user_id') || null, due_at: due ? new Date(due).toISOString() : null, visibility: state.context?.workspace?.role === 'client' ? 'client' : values.get('visibility') };
    submit.disabled = true;
    try { await bridge.api(`/api/projects/${state.projectId}/insights`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); dialog.close(); form.reset(); await load(); bridge.notify?.('Сигнал добавлен в проект.'); }
    catch (error) { errorMessage(error); } finally { submit.disabled = false; }
  }

  function connectRealtime() {
    state.source?.close(); if (!state.projectId || typeof EventSource === 'undefined') return;
    state.source = new EventSource(`/api/projects/${state.projectId}/message-events`);
    for (const type of ['insight.created', 'insight.updated', 'insight.dismissed', 'insights.extracted', 'briefing.generated', 'asset.review.created', 'asset.review.updated', 'message.created']) {
      state.source.addEventListener(type, () => { clearTimeout(state.timer); state.timer = setTimeout(() => load().catch(errorMessage), 300); });
    }
  }

  async function activate(context, force = false) {
    state.context = context || bridge.getContext?.() || {}; const projectId = state.context?.project?.id; if (!projectId) return;
    const changed = projectId !== state.projectId; state.projectId = projectId;
    root.querySelector('[data-intel-action="extract"]').classList.toggle('hidden', !canAnalyze(state.context));
    root.querySelector('[data-intel-action="new"]').classList.toggle('hidden', !canContribute(state.context));
    root.querySelector('[data-intel-action="briefing"]').classList.toggle('hidden', !canContribute(state.context));
    root.querySelector('[data-intel-action="briefing-ai"]').classList.toggle('hidden', !canAnalyze(state.context));
    const visibility = dialog.querySelector('[name="visibility"]');
    visibility.value = state.context?.workspace?.role === 'client' ? 'client' : visibility.value;
    visibility.disabled = state.context?.workspace?.role === 'client';
    if (changed) { state.data = null; connectRealtime(); await Promise.all([loadMembers(), load()]); }
    else if (force) await load();
  }

  root.addEventListener('click', (event) => {
    const action = event.target.closest('[data-intel-action]')?.dataset.intelAction;
    if (action === 'extract') extract(false).catch(errorMessage); else if (action === 'new') dialog.showModal();
    else if (action === 'briefing') briefing(false).catch(errorMessage); else if (action === 'briefing-ai') briefing(true).catch(errorMessage);
    else if (action === 'graph') router.open('graph');
    const filter = event.target.closest('[data-intel-filter]')?.dataset.intelFilter; if (filter) { state.filter = filter; root.querySelectorAll('[data-intel-filter]').forEach((button) => button.classList.toggle('active', button.dataset.intelFilter === filter)); renderSignals(); }
    const insightGraph = event.target.closest('[data-insight-graph]')?.dataset.insightGraph; if (insightGraph) router.open('graph', { insight: insightGraph });
    const queue = event.target.closest('[data-queue-type]');
    if (queue?.dataset.queueType === 'review' && queue.dataset.attachmentId) {
      bus.emit('asset:open', { assetId: queue.dataset.attachmentId, projectId: state.projectId });
      bus.emit('review:focus', { reviewId: queue.dataset.queueId, attachmentId: queue.dataset.attachmentId });
    } else if (queue?.dataset.queueType === 'overdue') router.open('content');
  });
  root.addEventListener('change', (event) => { const id = event.target.dataset.insightStatus; if (id) patchInsight(id, { status: event.target.value }).catch(errorMessage); });
  dialog.querySelector('form').addEventListener('submit', createInsight);
  dialog.querySelector('[name="visibility"]').addEventListener('change', renderAssignees);
  bus.on('context:change', (context) => { state.context = context; if (context.page === 'attention') activate(context, true).catch(errorMessage); });
  bus.on('route:change', ({ page }) => { if (page === 'attention') activate(bridge.getContext?.(), true).catch(errorMessage); });
  const initial = bridge.getContext?.(); if (initial?.project?.id) activate(initial).catch(errorMessage);
  return { refresh: load, destroy: () => { state.source?.close(); clearTimeout(state.timer); } };
}
