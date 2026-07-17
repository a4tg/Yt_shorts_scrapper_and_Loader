const ZOOM_MIN = 0.25;
const ZOOM_MAX = 5;

function node(tag, className, text) {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}

function fileKind(asset) {
  if (asset.preview?.kind) return asset.preview.kind;
  const mime = asset.mime_type || '';
  const extension = (asset.name?.split('.').pop() || '').toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime === 'application/pdf' || extension === 'pdf') return 'pdf';
  if (['csv', 'tsv', 'xlsx', 'ods'].includes(extension)) return 'table';
  if (['txt', 'md', 'json', 'srt', 'vtt', 'rtf', 'docx', 'pptx', 'odt', 'odp'].includes(extension)) return 'text';
  return 'unsupported';
}

function completeAsset(asset) {
  const id = asset.id;
  return {
    ...asset,
    preview_url: asset.preview_url || `/api/content-attachments/${id}/preview`,
    preview_data_url: asset.preview_data_url || `/api/content-attachments/${id}/preview-data`,
    download_url: asset.download_url || `/api/content-attachments/${id}/download`,
  };
}

function sizeLabel(bytes = 0) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} Б`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} КБ`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} МБ`;
  return `${(value / 1024 ** 3).toFixed(1)} ГБ`;
}

function assetIcon(asset) {
  const kind = fileKind(asset);
  return { image: 'IMG', video: '▶', audio: '♫', pdf: 'PDF', table: '▦', text: 'TXT' }[kind]
    || (asset.name?.split('.').pop() || 'FILE').slice(0, 4).toUpperCase();
}

export function initAssetViewer({ bus, bridge }) {
  const state = {
    open: false, asset: null, assets: [], projectId: null, zoom: 1,
    panX: 0, panY: 0, fit: true, loadToken: 0, previousUrl: null,
  };

  const shell = node('section', 'asset-viewer hidden');
  shell.setAttribute('role', 'dialog'); shell.setAttribute('aria-modal', 'true');
  shell.setAttribute('aria-label', 'Просмотр файла');
  shell.innerHTML = `
    <header class="asset-viewer-header">
      <div class="asset-viewer-filemark">FILE</div>
      <div class="asset-viewer-title"><strong>Файл</strong><small></small></div>
      <div class="asset-viewer-position"></div>
      <a class="asset-viewer-download" href="#" download title="Скачать оригинал">↓ <span>Скачать</span></a>
      <button data-viewer-action="fullscreen" type="button" title="Полный экран" aria-label="Полный экран">⛶</button>
      <button data-viewer-action="close" type="button" title="Закрыть" aria-label="Закрыть">×</button>
    </header>
    <div class="asset-viewer-layout">
      <aside class="asset-viewer-strip" aria-label="Файлы проекта"></aside>
      <main class="asset-viewer-main">
        <div class="asset-viewer-stage" tabindex="0"></div>
        <nav class="asset-viewer-nav" aria-label="Переход между файлами">
          <button data-viewer-action="previous" type="button" aria-label="Предыдущий файл">‹</button>
          <button data-viewer-action="next" type="button" aria-label="Следующий файл">›</button>
        </nav>
        <footer class="asset-viewer-toolbar">
          <button data-viewer-action="zoom-out" type="button" aria-label="Уменьшить">−</button>
          <output>100%</output>
          <button data-viewer-action="zoom-in" type="button" aria-label="Увеличить">＋</button>
          <button data-viewer-action="fit" type="button">Вписать</button>
          <span></span>
          <button data-viewer-action="details" type="button">Сведения</button>
        </footer>
      </main>
      <aside class="asset-viewer-details">
        <header><strong>Сведения</strong><button data-viewer-action="details" type="button" aria-label="Закрыть сведения">×</button></header>
        <dl></dl>
        <div class="asset-viewer-text-search hidden"><label>Поиск в документе<input type="search" placeholder="Найти текст"></label><small></small></div>
      </aside>
    </div>`;
  document.body.append(shell);

  const stage = shell.querySelector('.asset-viewer-stage');
  const strip = shell.querySelector('.asset-viewer-strip');
  const title = shell.querySelector('.asset-viewer-title strong');
  const subtitle = shell.querySelector('.asset-viewer-title small');
  const position = shell.querySelector('.asset-viewer-position');
  const download = shell.querySelector('.asset-viewer-download');
  const output = shell.querySelector('.asset-viewer-toolbar output');
  const searchBox = shell.querySelector('.asset-viewer-text-search');
  const searchInput = searchBox.querySelector('input');
  const searchStatus = searchBox.querySelector('small');
  const imageActions = [...shell.querySelectorAll(
    '[data-viewer-action="zoom-out"],[data-viewer-action="zoom-in"],[data-viewer-action="fit"]',
  )];
  let focusBeforeOpen = null;

  function currentIndex() {
    return state.assets.findIndex((item) => item.id === state.asset?.id);
  }

  function updateDeepLink(assetId) {
    if (!location.hash.startsWith('#/')) return;
    const [path, query = ''] = location.hash.slice(1).split('?');
    const params = new URLSearchParams(query);
    if (assetId) params.set('asset', assetId); else params.delete('asset');
    const next = `${location.pathname}${location.search}#${path}${params.size ? `?${params}` : ''}`;
    history.replaceState(history.state, '', next);
  }

  function renderDetails() {
    const asset = state.asset; const list = shell.querySelector('.asset-viewer-details dl'); list.replaceChildren();
    const values = [
      ['Имя', asset.name], ['Формат', asset.mime_type || 'Не определён'], ['Размер', sizeLabel(asset.size_bytes)],
      ['Источник', asset.source_type === 'ai' ? 'Результат AI' : 'Загрузка'],
      ['Создан', asset.created_at ? new Date(asset.created_at).toLocaleString('ru-RU') : '—'],
      ['ID', asset.id],
    ];
    for (const [label, value] of values) { list.append(node('dt', '', label), node('dd', '', value || '—')); }
  }

  function renderStrip() {
    strip.replaceChildren();
    for (const asset of state.assets) {
      const button = node('button', `asset-viewer-strip-item${asset.id === state.asset.id ? ' active' : ''}`); button.type = 'button'; button.dataset.assetId = asset.id;
      const visual = node('span', 'asset-viewer-strip-visual');
      if (fileKind(asset) === 'image') {
        const image = node('img'); image.src = asset.preview_url; image.alt = ''; image.loading = 'lazy'; visual.append(image);
      } else visual.textContent = assetIcon(asset);
      const copy = node('span'); copy.append(node('strong', '', asset.name), node('small', '', sizeLabel(asset.size_bytes)));
      button.append(visual, copy); strip.append(button);
    }
  }

  function showLoading() {
    stage.replaceChildren(); const loading = node('div', 'asset-viewer-loading');
    loading.append(node('i'), node('strong', '', 'Готовим просмотр'), node('small', '', 'Файл остаётся внутри вашего проекта'));
    stage.append(loading);
  }

  function showFailure(error) {
    stage.replaceChildren(); const failure = node('div', 'asset-viewer-failure');
    failure.append(node('span', '', assetIcon(state.asset)), node('strong', '', 'Предпросмотр недоступен'), node('p', '', error?.message || 'Этот формат можно скачать и открыть на компьютере.'));
    const link = node('a', 'primary', 'Скачать оригинал'); link.href = state.asset.download_url; link.download = ''; failure.append(link); stage.append(failure);
  }

  function setImageControls(enabled) {
    for (const button of imageActions) button.disabled = !enabled;
    if (!enabled) {
      output.value = '—';
      output.textContent = output.value;
    }
  }

  function emitMediaReady(asset) {
    bus.emit('asset:media-ready', { asset, shell, stage });
  }

  function watchMedia(element, asset, token, errorMessage) {
    element.addEventListener('loadedmetadata', () => {
      if (token === state.loadToken) emitMediaReady(asset);
    }, { once: true });
    element.addEventListener('error', () => {
      if (token === state.loadToken) showFailure(new Error(errorMessage));
    }, { once: true });
  }

  function applyImageTransform() {
    const image = stage.querySelector('.asset-viewer-image'); if (!image) return;
    image.style.transform = `translate(${state.panX}px,${state.panY}px) scale(${state.zoom})`;
    output.value = `${Math.round(state.zoom * 100)}%`; output.textContent = output.value;
  }

  function setZoom(value, { fit = false } = {}) {
    state.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, value)); state.fit = fit;
    if (fit) { state.panX = 0; state.panY = 0; }
    applyImageTransform();
  }

  function renderText(payload) {
    const wrapper = node('div', 'asset-viewer-document');
    const label = node('header'); label.append(node('strong', '', payload.label || 'Документ'));
    if (payload.truncated) label.append(node('small', '', 'Показан фрагмент файла'));
    const pre = node('pre'); pre.textContent = payload.text || 'Документ пуст'; wrapper.append(label, pre); stage.append(wrapper);
    searchBox.classList.remove('hidden');
  }

  function renderTable(payload) {
    const wrapper = node('div', 'asset-viewer-table-wrap');
    const banner = node('header'); banner.append(node('strong', '', payload.label || 'Таблица'), node('small', '', `${payload.rows.length} строк${payload.truncated ? ' · показан фрагмент' : ''}`)); wrapper.append(banner);
    const table = node('table'); const head = node('thead'); const headRow = node('tr');
    for (const column of payload.columns || []) headRow.append(node('th', '', column)); head.append(headRow); table.append(head);
    const body = node('tbody');
    for (const row of payload.rows || []) { const tr = node('tr'); for (const cell of row) tr.append(node('td', '', cell)); body.append(tr); }
    table.append(body); wrapper.append(table); stage.append(wrapper); searchBox.classList.remove('hidden');
  }

  async function renderAsset() {
    const token = ++state.loadToken; const asset = state.asset; showLoading(); searchBox.classList.add('hidden'); searchInput.value = ''; searchStatus.textContent = '';
    title.textContent = asset.name; subtitle.textContent = `${assetIcon(asset)} · ${sizeLabel(asset.size_bytes)}`;
    shell.querySelector('.asset-viewer-filemark').textContent = assetIcon(asset);
    download.href = asset.download_url; position.textContent = `${currentIndex() + 1} / ${state.assets.length}`;
    renderDetails(); renderStrip(); setZoom(1, { fit: true });
    const kind = fileKind(asset);
    setImageControls(kind === 'image');
    try {
      if (kind === 'image') {
        const image = node('img', 'asset-viewer-image'); image.alt = asset.name; image.draggable = false;
        image.addEventListener('load', () => { if (token === state.loadToken) { stage.replaceChildren(image); applyImageTransform(); emitMediaReady(asset); } });
        image.addEventListener('error', () => { if (token === state.loadToken) showFailure(new Error('Изображение не удалось прочитать.')); }); image.src = asset.preview_url;
      } else if (kind === 'video') {
        const video = node('video', 'asset-viewer-video'); video.controls = true; video.playsInline = true; video.preload = 'metadata';
        watchMedia(video, asset, token, 'Браузер не смог воспроизвести это видео. Скачайте оригинал или используйте MP4 H.264.');
        video.src = asset.preview_url; stage.replaceChildren(video);
      } else if (kind === 'audio') {
        const player = node('div', 'asset-viewer-audio'); player.append(node('span', '', '♫'), node('strong', '', asset.name));
        const audio = node('audio'); audio.controls = true; audio.preload = 'metadata';
        watchMedia(audio, asset, token, 'Браузер не смог воспроизвести эту аудиодорожку. Оригинал доступен для скачивания.');
        audio.src = asset.preview_url; player.append(audio); stage.replaceChildren(player);
      } else if (kind === 'pdf') {
        const frame = node('iframe', 'asset-viewer-pdf'); frame.title = `PDF: ${asset.name}`;
        frame.addEventListener('load', () => { if (token === state.loadToken) emitMediaReady(asset); }, { once: true });
        frame.src = asset.preview_url; stage.replaceChildren(frame);
      } else if (kind === 'text' || kind === 'table') {
        const payload = await bridge.api(asset.preview_data_url); if (token !== state.loadToken) return;
        stage.replaceChildren(); if (payload.kind === 'table') renderTable(payload); else renderText(payload);
        emitMediaReady(asset);
      } else showFailure(new Error('Предварительный просмотр этого формата ещё не поддерживается.'));
    } catch (error) { if (token === state.loadToken) showFailure(error); }
    bus.emit('asset:change', { asset, assets: state.assets, projectId: state.projectId, shell, stage });
  }

  async function resolveAsset(payload) {
    const requested = payload?.asset || (payload?.id ? payload : null); const requestedId = requested?.id || payload?.assetId;
    let collection = (payload?.assets || []).map(completeAsset);
    const context = payload?.context || bridge.getContext?.() || {};
    let asset = requested ? completeAsset(requested) : collection.find((item) => item.id === requestedId);
    if (!asset && requestedId) asset = completeAsset(await bridge.api(`/api/content-attachments/${requestedId}`));
    const projectId = requested?.project_id || asset?.project_id || payload?.projectId || context.projectId || state.projectId;
    if ((!collection.length || !collection.some((item) => item.id === requestedId)) && projectId) {
      collection = (await bridge.api(`/api/projects/${projectId}/library`)).map(completeAsset);
    }
    if (asset && !collection.some((item) => item.id === asset.id)) collection.unshift(asset);
    if (!asset && requestedId) asset = collection.find((item) => item.id === requestedId);
    if (!asset) throw new Error('Файл не найден или больше недоступен.');
    return { asset, collection: collection.length ? collection : [asset], projectId: projectId || asset.project_id };
  }

  async function openViewer(payload) {
    try {
      const resolved = await resolveAsset(payload || {}); focusBeforeOpen = document.activeElement;
      state.asset = resolved.asset; state.assets = resolved.collection; state.projectId = resolved.projectId; state.open = true;
      shell.classList.remove('hidden'); document.body.classList.add('asset-viewer-open'); updateDeepLink(state.asset.id);
      await renderAsset(); shell.querySelector('[data-viewer-action="close"]').focus();
    } catch (error) { bridge.notify?.(error.message, 'error'); }
  }

  function closeViewer() {
    if (!state.open) return; state.open = false; state.loadToken += 1; shell.classList.add('hidden'); document.body.classList.remove('asset-viewer-open');
    stage.replaceChildren(); updateDeepLink(null); bus.emit('asset:close', { asset: state.asset }); focusBeforeOpen?.focus?.();
  }

  async function move(offset) {
    if (state.assets.length < 2) return; const index = currentIndex();
    state.asset = state.assets[(index + offset + state.assets.length) % state.assets.length]; updateDeepLink(state.asset.id); await renderAsset();
  }

  shell.addEventListener('click', (event) => {
    const item = event.target.closest('[data-asset-id]'); if (item) { state.asset = state.assets.find((asset) => asset.id === item.dataset.assetId); updateDeepLink(state.asset.id); renderAsset(); return; }
    const action = event.target.closest('[data-viewer-action]')?.dataset.viewerAction;
    if (action === 'close') closeViewer();
    else if (action === 'previous') move(-1);
    else if (action === 'next') move(1);
    else if (action === 'zoom-in') setZoom(state.zoom * 1.25);
    else if (action === 'zoom-out') setZoom(state.zoom / 1.25);
    else if (action === 'fit') setZoom(1, { fit: true });
    else if (action === 'details') shell.classList.toggle('show-details');
    else if (action === 'fullscreen') document.fullscreenElement ? document.exitFullscreen() : shell.requestFullscreen?.();
  });
  shell.addEventListener('click', (event) => { if (event.target === shell) closeViewer(); });
  strip.addEventListener('wheel', (event) => { if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) { event.preventDefault(); strip.scrollLeft += event.deltaY; } }, { passive: false });
  stage.addEventListener('wheel', (event) => { if (!stage.querySelector('.asset-viewer-image')) return; event.preventDefault(); setZoom(state.zoom * (event.deltaY > 0 ? .9 : 1.1)); }, { passive: false });
  let drag = null;
  stage.addEventListener('pointerdown', (event) => { if (!stage.querySelector('.asset-viewer-image') || state.zoom <= 1) return; drag = { x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY }; stage.setPointerCapture(event.pointerId); stage.classList.add('panning'); });
  stage.addEventListener('pointermove', (event) => { if (!drag) return; state.panX = drag.panX + event.clientX - drag.x; state.panY = drag.panY + event.clientY - drag.y; applyImageTransform(); });
  stage.addEventListener('pointerup', () => { drag = null; stage.classList.remove('panning'); });
  searchInput.addEventListener('input', () => {
    const query = searchInput.value.trim().toLocaleLowerCase('ru-RU'); let count = 0;
    for (const row of stage.querySelectorAll('tbody tr')) { const match = !query || row.textContent.toLocaleLowerCase('ru-RU').includes(query); row.classList.toggle('hidden', !match); if (match && query) count += 1; }
    const text = stage.querySelector('pre')?.textContent || ''; if (text && query) count = text.toLocaleLowerCase('ru-RU').split(query).length - 1;
    searchStatus.textContent = query ? `${count} совпадений` : '';
  });
  window.addEventListener('keydown', (event) => {
    if (!state.open) return;
    if (event.key === 'Escape') { event.preventDefault(); closeViewer(); return; }
    if (event.key === 'Tab') {
      const focusable = [...shell.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
        .filter((element) => !element.closest('.hidden') && element.getClientRects().length);
      if (!focusable.length) return;
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      return;
    }
    if (event.target.matches('input,textarea,select') || event.target.isContentEditable) return;
    if (event.key === 'ArrowLeft') move(-1);
    else if (event.key === 'ArrowRight') move(1);
    else if (event.key === '+' || event.key === '=') setZoom(state.zoom * 1.25);
    else if (event.key === '-') setZoom(state.zoom / 1.25);
    else if (event.key.toLowerCase() === 'f') document.fullscreenElement ? document.exitFullscreen() : shell.requestFullscreen?.();
  });
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-asset-id]'); if (!link) return;
    event.preventDefault(); openViewer({ assetId: link.dataset.assetId, projectId: link.dataset.projectId });
  }, true);
  bus.on('asset:open', openViewer);
  bus.on('route:change', ({ params }) => { if (params.asset && params.asset !== state.asset?.id) openViewer({ assetId: params.asset }); });
  return { open: openViewer, close: closeViewer, destroy: () => { closeViewer(); shell.remove(); } };
}
