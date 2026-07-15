const $ = (selector) => document.querySelector(selector);
const state = {
  importId: null, overlayFiles: [], logoTokens: new Map(), activeOverlayIndex: 0,
  previewUrl: null, positionX: 50, positionY: 96
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function showToast(text) {
  const toast = $('#toast'); toast.textContent = text; toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), 3500);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.headers.get('content-type')?.includes('json') ? response.json() : response;
}

async function pollJob(id, onUpdate) {
  while (true) {
    const job = await api(`/api/jobs/${id}`); onUpdate(job);
    if (job.status === 'done') return job;
    if (job.status === 'deleted') throw new Error(job.message || 'Видео удалено');
    if (job.status === 'error') throw new Error(job.message || 'Задание завершилось ошибкой');
    await sleep(1500);
  }
}

function clamp(value, minimum, maximum) { return Math.min(maximum, Math.max(minimum, value)); }

function updateOverlayPreview() {
  const stage = $('#video-stage'); const overlay = $('#overlay-object');
  if (overlay.classList.contains('hidden')) return;
  const width = Number($('#logo-width').value);
  overlay.style.width = `${width}%`;
  overlay.style.opacity = Number($('#opacity').value) / 100;
  requestAnimationFrame(() => {
    const maxLeft = Math.max(0, stage.clientWidth - overlay.offsetWidth);
    const maxTop = Math.max(0, stage.clientHeight - overlay.offsetHeight);
    overlay.style.left = `${maxLeft * state.positionX / 100}px`;
    overlay.style.top = `${maxTop * state.positionY / 100}px`;
  });
  $('#position-x-value').textContent = `${Math.round(state.positionX)}%`;
  $('#position-y-value').textContent = `${Math.round(state.positionY)}%`;
  $('#editor-width-value').textContent = `${width}%`;
}

function showOverlayFile(file) {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  const extension = file.name.split('.').pop()?.toLowerCase();
  const videoExtensions = new Set(['mov', 'mp4', 'm4v', 'webm', 'mkv', 'avi', 'mpeg', 'mpg']);
  const media = file.type.startsWith('video/') || videoExtensions.has(extension)
    ? document.createElement('video') : document.createElement('img');
  media.src = state.previewUrl;
  if (media instanceof HTMLVideoElement) {
    media.muted = true; media.autoplay = true; media.loop = true; media.playsInline = true;
    media.addEventListener('loadeddata', () => { media.play().catch(() => {}); updateOverlayPreview(); });
  } else {
    media.alt = ''; media.addEventListener('load', updateOverlayPreview);
  }
  $('#overlay-media').replaceChildren(media);
  $('#overlay-object').classList.remove('hidden');
  $('#stage-placeholder').classList.add('hidden');
  updateOverlayPreview();
}

function renderOverlayFileList() {
  const container = $('#overlay-file-list'); container.replaceChildren();
  container.classList.toggle('hidden', !state.overlayFiles.length);
  state.overlayFiles.forEach((file, index) => {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = file.name; button.title = file.name;
    button.classList.toggle('active', index === state.activeOverlayIndex);
    button.addEventListener('click', () => {
      state.activeOverlayIndex = index; showOverlayFile(file); renderOverlayFileList();
    });
    container.append(button);
  });
}

function clearOverlayPreview() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null; $('#overlay-media').replaceChildren();
  $('#overlay-object').classList.add('hidden'); $('#stage-placeholder').classList.remove('hidden');
}

$('#opacity').addEventListener('input', (e) => {
  $('#opacity-value').textContent = `${e.target.value}%`; updateOverlayPreview();
});
$('#logo-width').addEventListener('input', (e) => {
  $('#width-value').textContent = `${e.target.value}%`; updateOverlayPreview();
});
$('#logo-file').addEventListener('change', (e) => {
  const files = Array.from(e.target.files);
  if (files.length > 10) {
    showToast('Можно выбрать не более 10 оверлеев'); e.target.value = '';
    state.overlayFiles = []; state.logoTokens.clear(); renderOverlayFileList(); clearOverlayPreview();
    $('#logo-name').textContent = 'Без оверлея'; return;
  }
  state.overlayFiles = files; state.logoTokens.clear(); state.activeOverlayIndex = 0;
  $('#logo-name').textContent = files.length ? `Выбрано: ${files.length}` : 'Без оверлея';
  renderOverlayFileList();
  if (files[0]) showOverlayFile(files[0]);
  else clearOverlayPreview();
});

$('#reset-overlay').addEventListener('click', () => {
  state.positionX = 50; state.positionY = 96; $('#logo-width').value = 22;
  $('#width-value').textContent = '22%'; updateOverlayPreview();
});

const overlayObject = $('#overlay-object'); const videoStage = $('#video-stage');
let editorGesture = null;
overlayObject.addEventListener('pointerdown', (event) => {
  const overlayRect = overlayObject.getBoundingClientRect();
  editorGesture = {
    pointerId: event.pointerId,
    mode: event.target.classList.contains('resize-handle') ? 'resize' : 'move',
    offsetX: event.clientX - overlayRect.left,
    offsetY: event.clientY - overlayRect.top,
    startX: event.clientX,
    startWidth: Number($('#logo-width').value)
  };
  overlayObject.setPointerCapture(event.pointerId); event.preventDefault();
});
overlayObject.addEventListener('pointermove', (event) => {
  if (!editorGesture || editorGesture.pointerId !== event.pointerId) return;
  const stageRect = videoStage.getBoundingClientRect();
  if (editorGesture.mode === 'resize') {
    const width = clamp(
      editorGesture.startWidth + (event.clientX - editorGesture.startX) / stageRect.width * 100,
      5, 80
    );
    $('#logo-width').value = Math.round(width); $('#width-value').textContent = `${Math.round(width)}%`;
  } else {
    const maxLeft = Math.max(0, stageRect.width - overlayObject.offsetWidth);
    const maxTop = Math.max(0, stageRect.height - overlayObject.offsetHeight);
    const left = clamp(event.clientX - stageRect.left - editorGesture.offsetX, 0, maxLeft);
    const top = clamp(event.clientY - stageRect.top - editorGesture.offsetY, 0, maxTop);
    state.positionX = maxLeft ? left / maxLeft * 100 : 0;
    state.positionY = maxTop ? top / maxTop * 100 : 0;
  }
  updateOverlayPreview();
});
const finishEditorGesture = (event) => {
  if (editorGesture?.pointerId === event.pointerId) editorGesture = null;
};
overlayObject.addEventListener('pointerup', finishEditorGesture);
overlayObject.addEventListener('pointercancel', finishEditorGesture);
overlayObject.addEventListener('keydown', (event) => {
  const step = event.shiftKey ? 5 : 1;
  if (event.key === 'ArrowLeft') state.positionX = clamp(state.positionX - step, 0, 100);
  else if (event.key === 'ArrowRight') state.positionX = clamp(state.positionX + step, 0, 100);
  else if (event.key === 'ArrowUp') state.positionY = clamp(state.positionY - step, 0, 100);
  else if (event.key === 'ArrowDown') state.positionY = clamp(state.positionY + step, 0, 100);
  else return;
  event.preventDefault(); updateOverlayPreview();
});
new ResizeObserver(updateOverlayPreview).observe(videoStage);

async function ensureOverlaysUploaded() {
  const tokens = [];
  for (let index = 0; index < state.overlayFiles.length; index += 1) {
    const file = state.overlayFiles[index];
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    let token = state.logoTokens.get(key);
    if (!token) {
      const form = new FormData(); form.append('file', file);
      showToast(`Загружаю оверлей ${index + 1}/${state.overlayFiles.length}…`);
      const result = await api('/api/logos', { method: 'POST', body: form });
      token = result.token; state.logoTokens.set(key, token);
    }
    tokens.push(token);
  }
  return tokens;
}

$('#channel-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter; const status = $('#import-status');
  button.disabled = true; status.className = 'status'; status.textContent = 'Задание добавлено в очередь…';
  $('#results-section').classList.add('hidden');
  try {
    const created = await api('/api/channels/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel_url: $('#channel-url').value,
        limit: Number($('#shorts-limit').value)
      })
    });
    state.importId = created.id;
    localStorage.setItem('ytLoaderImportJob', created.id);
    const job = await pollJob(created.id, (current) => { status.textContent = current.message || current.status; });
    const items = await api(job.items_url); renderItems(items, created.id);
    $('#csv-link').href = job.csv_url; status.textContent = `Готово: найдено ${items.length} Shorts`;
  } catch (error) {
    status.className = 'status error'; status.textContent = error.message;
  } finally { button.disabled = false; }
});

function renderItems(items, importId) {
  const container = $('#items'); container.replaceChildren();
  $('#result-count').textContent = `${items.length} роликов · видео обрабатываются по одному`;
  for (const item of items) {
    const card = document.createElement('article'); card.className = 'item';
    const image = document.createElement('img'); image.className = 'thumb'; image.loading = 'lazy'; image.alt = ''; image.src = item.thumbnail || '';
    const info = document.createElement('div');
    const title = document.createElement('h3'); title.textContent = item.title;
    const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = [item.uploader, item.upload_date].filter(Boolean).join(' · ');
    const description = document.createElement('p'); description.className = 'description'; description.textContent = item.description || 'Описание отсутствует';
    const tags = document.createElement('div'); tags.className = 'tags'; tags.textContent = item.tags.length ? item.tags.map((tag) => `#${tag}`).join(' ') : 'Теги отсутствуют';
    info.append(title, meta, description, tags);
    const actions = document.createElement('div'); actions.className = 'actions';
    const videoButton = document.createElement('button'); videoButton.className = 'primary'; videoButton.textContent = 'Подготовить видео';
    const metadata = document.createElement('a'); metadata.className = 'ghost'; metadata.textContent = 'Теги и описание'; metadata.href = `/api/imports/${importId}/${item.id}/metadata.txt`;
    const note = document.createElement('div'); note.className = 'job-note';
    videoButton.addEventListener('click', () => startDownloadUrl(item.url, videoButton, note));
    actions.append(videoButton, metadata, note); card.append(image, info, actions); container.append(card);
  }
  $('#results-section').classList.remove('hidden');
}

function downloadPayload(url, logoTokens) {
  return {
    url, logo_tokens: logoTokens,
    opacity: Number($('#opacity').value), width_percent: Number($('#logo-width').value),
    position_x: Math.round(state.positionX), position_y: Math.round(state.positionY),
    max_height: Number($('#resolution').value)
  };
}

function showReadyDownload(job, oldButton, note) {
  const overlayCount = Number(job.result?.overlay_count || 0);
  const readyLabel = overlayCount > 1 ? `Скачать ZIP · ${overlayCount} вариантов` : 'Скачать MP4';
  const repeatLabel = overlayCount > 1 ? 'Скачать ZIP ещё раз' : 'Скачать ещё раз';
  const downloadButton = document.createElement('button');
  downloadButton.className = 'primary'; downloadButton.textContent = readyLabel;
  oldButton.replaceWith(downloadButton);
  note.textContent = overlayCount > 1
    ? `Готово ${overlayCount} вариантов. В ZIP каждый оверлей лежит в своей папке.`
    : 'Видео готово. Таймер удаления запустится при скачивании.';

  const markDeleted = (message = 'Видео удалено') => {
    downloadButton.disabled = true; downloadButton.textContent = 'Файл удалён';
    const deleteButton = downloadButton.parentElement?.querySelector(`[data-delete-job="${job.id}"]`);
    if (deleteButton) deleteButton.remove();
    note.textContent = message;
  };

  const startCountdown = (ticket) => {
    let deleteButton = downloadButton.parentElement?.querySelector(`[data-delete-job="${job.id}"]`);
    if (!deleteButton) {
      deleteButton = document.createElement('button'); deleteButton.className = 'danger';
      deleteButton.dataset.deleteJob = job.id;
      downloadButton.insertAdjacentElement('afterend', deleteButton);
      deleteButton.addEventListener('click', async () => {
        deleteButton.disabled = true;
        try {
          await api(ticket.delete_url, { method: 'DELETE' });
          markDeleted('Видео удалено вручную');
        } catch (error) { note.textContent = error.message; deleteButton.disabled = false; }
      });
    }
    const renderTimer = () => {
      const seconds = Math.max(0, Math.ceil((new Date(ticket.delete_at).getTime() - Date.now()) / 1000));
      const minutes = String(Math.floor(seconds / 60)).padStart(2, '0');
      const rest = String(seconds % 60).padStart(2, '0');
      deleteButton.textContent = `Удалить сейчас · ${minutes}:${rest}`;
      if (seconds <= 0) { clearInterval(timer); markDeleted('Таймер истёк, видео удалено'); }
    };
    const timer = setInterval(renderTimer, 1000); renderTimer();
  };

  const waitForCompletedTransfer = async (ticket) => {
    if (ticket.delete_at) {
      downloadButton.textContent = repeatLabel; downloadButton.disabled = false;
      startCountdown(ticket); return;
    }
    while (true) {
      const current = await api(`/api/jobs/${job.id}`);
      if (current.status === 'deleted') { markDeleted(current.message); return; }
      if (current.delete_at) {
        downloadButton.textContent = repeatLabel; downloadButton.disabled = false;
        startCountdown({ ...ticket, delete_at: current.delete_at }); return;
      }
      await sleep(1000);
    }
  };

  downloadButton.addEventListener('click', async () => {
    downloadButton.disabled = true;
    try {
      const ticket = await api(job.download_ticket_url, { method: 'POST' });
      const link = document.createElement('a'); link.href = ticket.download_url; link.download = '';
      document.body.append(link); link.click(); link.remove();
      downloadButton.textContent = 'Передаётся файл…';
      note.textContent = 'Скачивание начато. Таймер появится после передачи файла.';
      waitForCompletedTransfer(ticket).catch((error) => {
        note.textContent = error.message; downloadButton.disabled = false; downloadButton.textContent = 'Повторить скачивание';
      });
    } catch (error) { note.textContent = error.message; downloadButton.disabled = false; }
  });
}

async function startDownloadUrl(url, button, note) {
  button.disabled = true; note.textContent = 'Подготовка задания…';
  try {
    const logoTokens = await ensureOverlaysUploaded();
    const created = await api('/api/videos/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(downloadPayload(url, logoTokens))
    });
    const job = await pollJob(created.id, (current) => { note.textContent = current.message || current.status; });
    showReadyDownload(job, button, note);
  } catch (error) { note.textContent = error.message; button.disabled = false; }
}

$('#direct-video-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitButton = event.submitter; const box = $('#direct-video-actions');
  const buttons = $('#direct-video-buttons'); const note = $('#direct-video-note');
  box.classList.remove('hidden'); buttons.replaceChildren();
  const workButton = document.createElement('button'); workButton.className = 'primary';
  workButton.textContent = 'Подготовка…'; buttons.append(workButton);
  submitButton.disabled = true;
  await startDownloadUrl($('#direct-video-url').value, workButton, note);
  submitButton.disabled = false;
});

api('/api/health').catch(() => { $('#health').innerHTML = '<i style="background:#ff6b7d"></i> Сервер недоступен'; });

async function resumeImport() {
  const queryJob = new URLSearchParams(location.search).get('job');
  const id = queryJob || localStorage.getItem('ytLoaderImportJob');
  if (!id) return;
  const status = $('#import-status'); status.className = 'status'; status.textContent = 'Восстанавливаю последнее задание…';
  try {
    const job = await pollJob(id, (current) => { status.textContent = current.message || current.status; });
    if (job.kind !== 'import') return;
    const items = await api(job.items_url); state.importId = id; renderItems(items, id);
    $('#csv-link').href = job.csv_url; status.textContent = `Готово: найдено ${items.length} Shorts`;
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
}

resumeImport();
