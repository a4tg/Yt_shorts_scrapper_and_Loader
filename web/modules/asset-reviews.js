function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}

function timecode(seconds = 0) {
  const value = Math.max(0, Math.floor(Number(seconds)));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

function canEdit(context) {
  return ['owner', 'admin', 'editor'].includes(context?.workspace?.role);
}

function reviewLabel(review) {
  if (review.annotation_type === 'timestamp') return `Таймкод ${timecode(review.time_seconds)}`;
  if (review.annotation_type === 'page') return `Страница ${review.page_number}`;
  return { point: 'Точка', region: 'Область', drawing: 'Рисунок', general: 'Общее' }[review.annotation_type] || 'Замечание';
}

export function initAssetReviews({ bus, bridge }) {
  const shell = document.querySelector('.asset-viewer');
  if (!shell) return {};
  const layout = shell.querySelector('.asset-viewer-layout');
  const stage = shell.querySelector('.asset-viewer-stage');
  const header = shell.querySelector('.asset-viewer-header');
  const state = {
    asset: null, assets: [], versions: [], reviews: [], summary: null, members: [],
    context: bridge.getContext?.() || {}, tool: 'general', annotation: {}, parentId: null,
    compareVersion: null, draw: null, loadToken: 0, source: null,
  };

  const toggle = node('button', 'asset-review-toggle'); toggle.type = 'button'; toggle.title = 'Версии и ревью'; toggle.setAttribute('aria-label', 'Открыть версии и ревью'); toggle.innerHTML = '◉ <i>0</i>';
  header.insertBefore(toggle, shell.querySelector('.asset-viewer-download'));
  const panel = node('aside', 'asset-review-panel');
  panel.innerHTML = `
    <header><div><strong>Версии и ревью</strong><small>Обратная связь в контексте файла</small></div><button data-review-action="close" type="button" aria-label="Закрыть">×</button></header>
    <section class="asset-version-section">
      <div class="asset-review-section-head"><strong>Версии</strong><button data-review-action="upload-version" type="button">＋ Новая</button></div>
      <div class="asset-version-list"></div>
      <form class="asset-version-form hidden">
        <input name="file" type="file" required><input name="label" maxlength="120" placeholder="Название версии">
        <textarea name="notes" maxlength="10000" rows="2" placeholder="Что изменилось"></textarea>
        <footer><button data-review-action="cancel-version" type="button">Отмена</button><button class="primary" type="submit">Загрузить</button></footer><small></small>
      </form>
    </section>
    <section class="asset-review-section">
      <div class="asset-review-section-head"><strong>Замечания</strong><span class="asset-review-counts"></span></div>
      <div class="asset-review-tools" role="toolbar" aria-label="Тип замечания">
        <button data-review-tool="general" type="button">Комментарий</button><button data-review-tool="point" type="button">Точка</button>
        <button data-review-tool="region" type="button">Область</button><button data-review-tool="drawing" type="button">Рисунок</button>
        <button data-review-tool="timestamp" type="button">Таймкод</button><button data-review-tool="page" type="button">Страница</button>
      </div>
      <div class="asset-review-tool-hint hidden"></div>
      <form class="asset-review-form">
        <div class="asset-review-context hidden"><span></span><button data-review-action="cancel-annotation" type="button">×</button></div>
        <textarea name="body" rows="3" maxlength="10000" placeholder="Опишите, что нужно изменить" required></textarea>
        <div class="asset-review-form-options"><select name="visibility" aria-label="Видимость"><option value="team">Только команда</option><option value="client">Команда и клиент</option></select><select name="assignee" aria-label="Ответственный"><option value="">Без ответственного</option></select></div>
        <label class="asset-review-page hidden">Страница<input name="page" type="number" min="1" max="100000" value="1"></label>
        <footer><small></small><button class="primary" type="submit">Добавить</button></footer>
      </form>
      <div class="asset-review-list"></div>
    </section>
    <section class="asset-approval-section">
      <div><strong>Согласование</strong><span class="asset-approval-state">Ожидает решения</span></div>
      <textarea rows="2" maxlength="10000" placeholder="Комментарий к решению"></textarea>
      <footer><button data-decision="changes_requested" type="button">Нужны правки</button><button data-decision="approved" type="button">Согласовать</button></footer>
      <div class="asset-approval-list"></div>
    </section>`;
  layout.append(panel);
  const overlay = node('div', 'asset-review-overlay');
  const hint = panel.querySelector('.asset-review-tool-hint');
  const form = panel.querySelector('.asset-review-form');
  const versionForm = panel.querySelector('.asset-version-form');

  function notifyError(error) { bridge.notify?.(error?.message || 'Не удалось выполнить действие.', 'error'); }

  function positionOverlay() {
    const target = stage.querySelector('.asset-viewer-image,.asset-viewer-video,.asset-viewer-pdf,.asset-viewer-document,.asset-viewer-table-wrap');
    if (!target || !state.asset) { overlay.remove(); return; }
    if (!overlay.isConnected) stage.append(overlay);
    const targetRect = target.getBoundingClientRect(); const stageRect = stage.getBoundingClientRect();
    overlay.style.left = `${targetRect.left - stageRect.left}px`; overlay.style.top = `${targetRect.top - stageRect.top}px`;
    overlay.style.width = `${targetRect.width}px`; overlay.style.height = `${targetRect.height}px`;
  }

  function renderMarkers() {
    positionOverlay(); if (!overlay.isConnected) return; overlay.replaceChildren();
    const roots = state.reviews.filter((review) => !review.parent_review_id);
    roots.forEach((review, index) => {
      if (!['point', 'region', 'drawing'].includes(review.annotation_type)) return;
      let marker;
      if (review.annotation_type === 'point') {
        marker = node('button', `asset-review-marker point ${review.status}` , String(index + 1)); marker.type = 'button';
        marker.style.left = `${review.x * 100}%`; marker.style.top = `${review.y * 100}%`;
      } else if (review.annotation_type === 'region') {
        marker = node('button', `asset-review-marker region ${review.status}`, String(index + 1)); marker.type = 'button';
        marker.style.left = `${review.x * 100}%`; marker.style.top = `${review.y * 100}%`;
        marker.style.width = `${review.width * 100}%`; marker.style.height = `${review.height * 100}%`;
      } else {
        marker = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); marker.setAttribute('class', `asset-review-drawing ${review.status}`); marker.setAttribute('viewBox', '0 0 1000 1000');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        path.setAttribute('points', (review.annotation_data?.points || []).map((point) => `${point[0] * 1000},${point[1] * 1000}`).join(' ')); marker.append(path);
      }
      marker.dataset.reviewId = review.id; marker.addEventListener('click', () => panel.querySelector(`[data-review-id="${review.id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })); overlay.append(marker);
    });
  }

  function renderVersions() {
    const list = panel.querySelector('.asset-version-list'); list.replaceChildren();
    for (const version of state.versions) {
      const row = node('article', `asset-version-row${version.id === state.asset.id ? ' active' : ''}`);
      const button = node('button'); button.type = 'button'; button.dataset.versionId = version.id;
      const badge = node('b', '', `v${version.version_number}`); const copy = node('span');
      copy.append(node('strong', '', version.version_label || version.name), node('small', '', `${version.open_count || 0} открытых · ${version.approval_state === 'approved' ? 'согласовано' : version.approval_state === 'changes_requested' ? 'нужны правки' : 'без решения'}`)); button.append(badge, copy);
      const compare = node('button', 'asset-version-compare', 'Сравнить'); compare.type = 'button'; compare.dataset.compareId = version.id; compare.disabled = version.id === state.asset.id;
      row.append(button, compare); list.append(row);
    }
    panel.querySelector('[data-review-action="upload-version"]').classList.toggle('hidden', !canEdit(state.context));
  }

  function renderReviews() {
    const list = panel.querySelector('.asset-review-list'); list.replaceChildren();
    const roots = state.reviews.filter((review) => !review.parent_review_id);
    roots.forEach((review, index) => {
      const card = node('article', `asset-review-card ${review.status}`); card.dataset.reviewId = review.id;
      const head = node('header'); const identity = node('div');
      identity.append(node('b', '', review.author?.name || 'Участник'), node('small', '', `${reviewLabel(review)} · ${new Date(review.created_at).toLocaleString('ru-RU')}`));
      const status = node('select'); status.dataset.reviewStatus = review.id;
      for (const [value, label] of [['open', 'Открыто'], ['in_progress', 'В работе'], ['resolved', 'Решено'], ['wont_fix', 'Не исправляем']]) { const option = new Option(label, value); status.append(option); }
      status.value = review.status;
      status.disabled = !(canEdit(state.context) || review.is_own || review.assignee?.id === state.context?.user?.id);
      head.append(identity, status); card.append(head);
      const content = node('div', 'asset-review-card-body');
      if (['point', 'region', 'drawing'].includes(review.annotation_type)) content.append(node('i', '', String(index + 1)));
      content.append(node('p', '', review.body)); card.append(content);
      const meta = node('footer'); meta.append(node('span', '', review.assignee ? `→ ${review.assignee.name}` : review.visibility === 'client' ? 'Видит клиент' : 'Только команда'));
      const reply = node('button', '', 'Ответить'); reply.type = 'button'; reply.dataset.replyId = review.id; meta.append(reply); card.append(meta);
      for (const child of state.reviews.filter((item) => item.parent_review_id === review.id)) {
        const response = node('div', 'asset-review-reply'); response.append(node('strong', '', child.author?.name || 'Участник'), node('p', '', child.body)); card.append(response);
      }
      list.append(card);
    });
    if (!roots.length) list.append(node('div', 'asset-review-empty', 'Замечаний пока нет. Можно отметить точку, область, таймкод или оставить общий комментарий.'));
    const counts = state.summary?.review_counts || {}; panel.querySelector('.asset-review-counts').textContent = `${(counts.open || 0) + (counts.in_progress || 0)} открытых`;
    toggle.querySelector('i').textContent = String((counts.open || 0) + (counts.in_progress || 0));
    renderMarkers();
  }

  function renderApprovals() {
    const summary = state.summary || {}; const list = panel.querySelector('.asset-approval-list'); list.replaceChildren();
    const label = { approved: 'Согласовано', changes_requested: 'Нужны правки', pending: 'Ожидает решения' }[summary.approval_state] || 'Ожидает решения';
    const status = panel.querySelector('.asset-approval-state'); status.textContent = label; status.dataset.state = summary.approval_state || 'pending';
    for (const approval of summary.approvals || []) {
      const row = node('div'); row.append(node('b', '', approval.decision === 'approved' ? '✓' : '!'), node('span', '', `${approval.user?.name || 'Участник'} · ${approval.comment || label}`)); list.append(row);
    }
  }

  function renderMembers() {
    const select = form.elements.assignee; select.replaceChildren(new Option('Без ответственного', ''));
    for (const member of state.members) select.append(new Option(member.display_name || member.email, member.user_id));
    form.elements.visibility.value = state.context?.workspace?.role === 'client' ? 'client' : 'team';
    form.elements.visibility.disabled = state.context?.workspace?.role === 'client';
  }

  async function loadReviewData(asset) {
    const token = ++state.loadToken; state.asset = asset; state.context = bridge.getContext?.() || state.context;
    resetAnnotation();
    try {
      const [versions, reviews, members] = await Promise.all([
        bridge.api(`/api/content-attachments/${asset.id}/versions`),
        bridge.api(`/api/content-attachments/${asset.id}/reviews`),
        state.context?.workspace?.id ? bridge.api(`/api/workspaces/${state.context.workspace.id}/members`) : Promise.resolve([]),
      ]);
      if (token !== state.loadToken) return;
      state.versions = versions.versions; state.reviews = reviews.reviews; state.summary = reviews; state.members = members;
      renderVersions(); renderMembers(); renderReviews(); renderApprovals();
    } catch (error) { notifyError(error); }
  }

  function resetAnnotation() {
    state.tool = 'general'; state.annotation = {}; state.parentId = null; state.draw = null;
    hint.classList.add('hidden'); hint.textContent = ''; overlay.classList.remove('capturing'); form.querySelector('.asset-review-context').classList.add('hidden');
    form.querySelector('.asset-review-page').classList.add('hidden'); panel.querySelectorAll('[data-review-tool]').forEach((button) => button.classList.toggle('active', button.dataset.reviewTool === 'general'));
  }

  function selectTool(tool) {
    resetAnnotation(); state.tool = tool; panel.querySelectorAll('[data-review-tool]').forEach((button) => button.classList.toggle('active', button.dataset.reviewTool === tool));
    const context = form.querySelector('.asset-review-context'); const contextText = context.querySelector('span');
    if (tool === 'general') { form.elements.body.focus(); return; }
    if (tool === 'timestamp') {
      const video = stage.querySelector('video');
      if (!video) { notifyError(new Error('Таймкод доступен при просмотре видео.')); resetAnnotation(); return; }
      state.annotation.time_seconds = video.currentTime; contextText.textContent = `Таймкод ${timecode(video.currentTime)}`; context.classList.remove('hidden'); form.elements.body.focus(); return;
    }
    if (tool === 'page') { form.querySelector('.asset-review-page').classList.remove('hidden'); contextText.textContent = 'Комментарий к странице'; context.classList.remove('hidden'); form.elements.body.focus(); return; }
    hint.textContent = { point: 'Нажмите на нужную точку файла', region: 'Протяните рамку вокруг области', drawing: 'Нарисуйте линию поверх файла' }[tool]; hint.classList.remove('hidden');
    positionOverlay(); overlay.classList.add('capturing');
  }

  function normalizedPoint(event) {
    const rect = overlay.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
  }

  function completeSpatialAnnotation(annotation) {
    state.annotation = annotation; overlay.classList.remove('capturing'); hint.classList.add('hidden');
    const context = form.querySelector('.asset-review-context'); context.querySelector('span').textContent = { point: 'Точка выбрана', region: 'Область выбрана', drawing: 'Рисунок добавлен' }[state.tool]; context.classList.remove('hidden'); form.elements.body.focus();
  }

  overlay.addEventListener('pointerdown', (event) => {
    if (!overlay.classList.contains('capturing')) return; event.preventDefault(); const point = normalizedPoint(event);
    if (state.tool === 'point') { completeSpatialAnnotation(point); return; }
    state.draw = { start: point, points: [point], pointerId: event.pointerId }; overlay.setPointerCapture(event.pointerId);
  });
  overlay.addEventListener('pointermove', (event) => { if (!state.draw) return; state.draw.points.push(normalizedPoint(event)); });
  overlay.addEventListener('pointerup', (event) => {
    if (!state.draw) return; const end = normalizedPoint(event); const draw = state.draw; state.draw = null;
    if (state.tool === 'region') completeSpatialAnnotation({ x: Math.min(draw.start.x, end.x), y: Math.min(draw.start.y, end.y), width: Math.max(.005, Math.abs(end.x - draw.start.x)), height: Math.max(.005, Math.abs(end.y - draw.start.y)) });
    else completeSpatialAnnotation({ annotation_data: { points: draw.points.map((point) => [point.x, point.y]) } });
  });

  async function submitReview(event) {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true; const status = form.querySelector('footer small'); status.textContent = 'Сохраняем…';
    try {
      const payload = {
        body: form.elements.body.value.trim(), annotation_type: state.parentId ? 'general' : state.tool,
        visibility: form.elements.visibility.value, assignee_user_id: form.elements.assignee.value || null,
        parent_review_id: state.parentId, ...state.annotation,
      };
      if (state.tool === 'page' && !state.parentId) payload.page_number = Number(form.elements.page.value);
      await bridge.api(`/api/content-attachments/${state.asset.id}/reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      form.elements.body.value = ''; resetAnnotation(); await loadReviewData(state.asset); status.textContent = '';
    } catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { submit.disabled = false; }
  }

  async function decide(decision) {
    try {
      await bridge.api(`/api/content-attachments/${state.asset.id}/approval`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, comment: panel.querySelector('.asset-approval-section textarea').value.trim() || null }) });
      panel.querySelector('.asset-approval-section textarea').value = ''; await loadReviewData(state.asset);
    } catch (error) { notifyError(error); }
  }

  async function uploadVersion(event) {
    event.preventDefault(); const submit = event.submitter; submit.disabled = true; const status = versionForm.querySelector('small'); status.textContent = 'Загружаем новую версию…';
    try {
      const data = new FormData(versionForm); const created = await bridge.api(`/api/content-attachments/${state.asset.id}/versions`, { method: 'POST', body: data });
      versionForm.reset(); versionForm.classList.add('hidden'); bus.emit('asset:open', { asset: created, projectId: created.project_id });
    } catch (error) { status.textContent = error.message; status.classList.add('error'); }
    finally { submit.disabled = false; }
  }

  async function compare(version) {
    closeCompare(); state.compareVersion = version;
    const compareShell = node('section', 'asset-compare'); compareShell.innerHTML = `<header><div><strong>Сравнение версий</strong><small>v${version.version_number} ↔ v${state.asset.version_number}</small></div><div><button data-compare-mode="side" class="active" type="button">Рядом</button><button data-compare-mode="slider" type="button">Слайдер</button><button data-compare-action="close" type="button">×</button></div></header><div class="asset-compare-body"></div>`;
    shell.append(compareShell); const body = compareShell.querySelector('.asset-compare-body');
    const kind = state.asset.preview?.kind || '';
    if (kind === 'image' && (version.preview?.kind || '') === 'image') {
      const side = node('div', 'asset-compare-side'); for (const item of [version, state.asset]) { const pane = node('figure'); const image = node('img'); image.src = item.preview_url; image.alt = item.name; pane.append(image, node('figcaption', '', `v${item.version_number} · ${item.version_label || item.name}`)); side.append(pane); } body.append(side);
    } else if (kind === 'video' && (version.preview?.kind || '') === 'video') {
      const side = node('div', 'asset-compare-side video'); const players = [];
      for (const item of [version, state.asset]) { const pane = node('figure'); const video = node('video'); video.src = item.preview_url; video.controls = true; video.playsInline = true; players.push(video); pane.append(video, node('figcaption', '', `v${item.version_number}`)); side.append(pane); } body.append(side);
      players[0].addEventListener('play', () => players[1].play()); players[0].addEventListener('pause', () => players[1].pause()); players[0].addEventListener('seeked', () => { players[1].currentTime = players[0].currentTime; });
    } else if (kind === 'pdf' && (version.preview?.kind || '') === 'pdf') {
      const side = node('div', 'asset-compare-side pdf'); for (const item of [version, state.asset]) { const pane = node('figure'); const frame = node('iframe'); frame.src = item.preview_url; frame.title = `Версия ${item.version_number}`; pane.append(frame, node('figcaption', '', `v${item.version_number}`)); side.append(pane); } body.append(side);
    } else {
      const side = node('div', 'asset-compare-side documents');
      for (const item of [version, state.asset]) { const pane = node('figure'); const pre = node('pre', '', 'Загрузка…'); pane.append(pre, node('figcaption', '', `v${item.version_number}`)); side.append(pane); bridge.api(item.preview_data_url).then((data) => { pre.textContent = data.text || [data.columns, ...data.rows].map((row) => row.join('\t')).join('\n'); }).catch((error) => { pre.textContent = error.message; }); }
      body.append(side);
    }
    compareShell.addEventListener('click', (event) => {
      if (event.target.closest('[data-compare-action="close"]')) closeCompare();
      const mode = event.target.closest('[data-compare-mode]')?.dataset.compareMode;
      if (mode === 'slider' && kind === 'image') enableSlider(compareShell); else if (mode === 'side') disableSlider(compareShell);
    });
  }

  function enableSlider(compareShell) {
    const side = compareShell.querySelector('.asset-compare-side'); if (!side || side.children.length !== 2) return; side.classList.add('slider');
    let range = compareShell.querySelector('input[type=range]'); if (!range) { range = node('input'); range.type = 'range'; range.min = '0'; range.max = '100'; range.value = '50'; compareShell.querySelector('.asset-compare-body').append(range); }
    const update = () => side.children[1].style.clipPath = `inset(0 0 0 ${range.value}%)`; range.addEventListener('input', update); update();
    compareShell.querySelectorAll('[data-compare-mode]').forEach((button) => button.classList.toggle('active', button.dataset.compareMode === 'slider'));
  }
  function disableSlider(compareShell) { const side = compareShell.querySelector('.asset-compare-side'); side?.classList.remove('slider'); if (side?.children[1]) side.children[1].style.clipPath = ''; compareShell.querySelector('input[type=range]')?.remove(); compareShell.querySelectorAll('[data-compare-mode]').forEach((button) => button.classList.toggle('active', button.dataset.compareMode === 'side')); }
  function closeCompare() { shell.querySelector('.asset-compare')?.remove(); state.compareVersion = null; }

  function connectRealtime(projectId) {
    state.source?.close(); state.source = null;
    if (!projectId || typeof EventSource === 'undefined') return;
    const source = new EventSource(`/api/projects/${projectId}/message-events`); state.source = source;
    for (const eventType of ['asset.version.created', 'asset.review.created', 'asset.review.updated', 'asset.review.deleted', 'asset.approval.updated', 'asset.approval.cleared']) {
      source.addEventListener(eventType, (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (!payload.attachment_id || payload.attachment_id === state.asset?.id || payload.asset_key === state.asset?.asset_key) loadReviewData(state.asset);
        } catch (_) { /* EventSource reconnects after malformed events. */ }
      });
    }
  }

  toggle.addEventListener('click', () => { shell.classList.toggle('show-reviews'); if (shell.classList.contains('show-reviews')) setTimeout(positionOverlay); });
  panel.addEventListener('click', async (event) => {
    const action = event.target.closest('[data-review-action]')?.dataset.reviewAction;
    if (action === 'close') shell.classList.remove('show-reviews');
    else if (action === 'upload-version') versionForm.classList.remove('hidden');
    else if (action === 'cancel-version') versionForm.classList.add('hidden');
    else if (action === 'cancel-annotation') resetAnnotation();
    const tool = event.target.closest('[data-review-tool]')?.dataset.reviewTool; if (tool) selectTool(tool);
    const versionId = event.target.closest('[data-version-id]')?.dataset.versionId;
    if (versionId) bus.emit('asset:open', { asset: state.versions.find((item) => item.id === versionId), assets: state.versions, projectId: state.asset.project_id });
    const compareId = event.target.closest('[data-compare-id]')?.dataset.compareId; if (compareId) compare(state.versions.find((item) => item.id === compareId));
    const replyId = event.target.closest('[data-reply-id]')?.dataset.replyId;
    if (replyId) { resetAnnotation(); state.parentId = replyId; const context = form.querySelector('.asset-review-context'); context.querySelector('span').textContent = 'Ответ на замечание'; context.classList.remove('hidden'); form.elements.body.focus(); }
    const decision = event.target.closest('[data-decision]')?.dataset.decision; if (decision) decide(decision);
  });
  panel.addEventListener('change', async (event) => {
    const id = event.target.dataset.reviewStatus; if (!id) return;
    try { await bridge.api(`/api/asset-reviews/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: event.target.value }) }); await loadReviewData(state.asset); } catch (error) { notifyError(error); }
  });
  form.addEventListener('submit', submitReview); versionForm.addEventListener('submit', uploadVersion);
  bus.on('asset:change', ({ asset, assets, projectId }) => { state.assets = assets || []; connectRealtime(projectId || asset.project_id); loadReviewData(asset); });
  bus.on('asset:media-ready', () => { renderMarkers(); });
  bus.on('asset:close', () => { state.loadToken += 1; state.source?.close(); state.source = null; overlay.remove(); closeCompare(); shell.classList.remove('show-reviews'); });
  bus.on('context:change', (context) => { state.context = context; });
  window.addEventListener('resize', positionOverlay);
  return { destroy: () => { state.source?.close(); toggle.remove(); panel.remove(); overlay.remove(); closeCompare(); } };
}
