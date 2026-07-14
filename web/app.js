const $ = (selector) => document.querySelector(selector);
const state = { importId: null, logoToken: null, logoFileKey: null };
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

$('#opacity').addEventListener('input', (e) => $('#opacity-value').textContent = `${e.target.value}%`);
$('#logo-width').addEventListener('input', (e) => $('#width-value').textContent = `${e.target.value}%`);
$('#logo-file').addEventListener('change', (e) => {
  const file = e.target.files[0]; $('#logo-name').textContent = file ? file.name : 'Без оверлея';
  state.logoToken = null; state.logoFileKey = null;
});

async function ensureLogoUploaded() {
  const file = $('#logo-file').files[0];
  if (!file) return null;
  const key = `${file.name}:${file.size}:${file.lastModified}`;
  if (state.logoToken && state.logoFileKey === key) return state.logoToken;
  const form = new FormData(); form.append('file', file);
  showToast('Загружаю оверлей…');
  const result = await api('/api/logos', { method: 'POST', body: form });
  state.logoToken = result.token; state.logoFileKey = key; return result.token;
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

function downloadPayload(url, logoToken) {
  return {
    url, logo_token: logoToken,
    opacity: Number($('#opacity').value), width_percent: Number($('#logo-width').value),
    max_height: Number($('#resolution').value)
  };
}

function showReadyDownload(job, oldButton, note) {
  const downloadButton = document.createElement('button');
  downloadButton.className = 'primary'; downloadButton.textContent = 'Скачать MP4';
  oldButton.replaceWith(downloadButton);
  note.textContent = 'Видео готово. Таймер удаления запустится при скачивании.';

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
      downloadButton.textContent = 'Скачать ещё раз'; downloadButton.disabled = false;
      startCountdown(ticket); return;
    }
    while (true) {
      const current = await api(`/api/jobs/${job.id}`);
      if (current.status === 'deleted') { markDeleted(current.message); return; }
      if (current.delete_at) {
        downloadButton.textContent = 'Скачать ещё раз'; downloadButton.disabled = false;
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
    const logoToken = await ensureLogoUploaded();
    const created = await api('/api/videos/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(downloadPayload(url, logoToken))
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
