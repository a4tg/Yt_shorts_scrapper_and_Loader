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
    if (job.status === 'error') throw new Error(job.message || 'Задание завершилось ошибкой');
    await sleep(1500);
  }
}

$('#opacity').addEventListener('input', (e) => $('#opacity-value').textContent = `${e.target.value}%`);
$('#logo-width').addEventListener('input', (e) => $('#width-value').textContent = `${e.target.value}%`);
$('#logo-file').addEventListener('change', (e) => {
  const file = e.target.files[0]; $('#logo-name').textContent = file ? file.name : 'Без логотипа';
  state.logoToken = null; state.logoFileKey = null;
});

async function ensureLogoUploaded() {
  const file = $('#logo-file').files[0];
  if (!file) return null;
  const key = `${file.name}:${file.size}:${file.lastModified}`;
  if (state.logoToken && state.logoFileKey === key) return state.logoToken;
  const form = new FormData(); form.append('file', file);
  showToast('Загружаю логотип…');
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
    videoButton.addEventListener('click', () => startDownload(item, videoButton, note));
    actions.append(videoButton, metadata, note); card.append(image, info, actions); container.append(card);
  }
  $('#results-section').classList.remove('hidden');
}

async function startDownload(item, button, note) {
  button.disabled = true; note.textContent = 'Подготовка задания…';
  try {
    const logoToken = await ensureLogoUploaded();
    const created = await api('/api/videos/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: item.url, logo_token: logoToken,
        opacity: Number($('#opacity').value), width_percent: Number($('#logo-width').value),
        max_height: Number($('#resolution').value)
      })
    });
    const job = await pollJob(created.id, (current) => { note.textContent = current.message || current.status; });
    const link = document.createElement('a'); link.className = 'primary'; link.textContent = 'Скачать MP4'; link.href = job.video_url;
    button.replaceWith(link); note.textContent = 'Видео готово';
  } catch (error) { note.textContent = error.message; button.disabled = false; }
}

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
