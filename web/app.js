const $ = (selector) => document.querySelector(selector);
const state = {
  importId: null, overlayFiles: [], logoTokens: new Map(), activeOverlayIndex: 0,
  previewUrl: null, overlayPreviewUrls: new Map(), logoUploads: new Map(),
  sourceVideoId: null, positionX: 50, positionY: 96,
  itemCards: new Map(), batchRunning: false, currentUser: null, importResumed: false,
  paymentResumed: false, authConfig: {}, accountToken: null
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function showToast(text) {
  const toast = $('#toast'); toast.textContent = text; toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), 3500);
}

async function api(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = readCookie('yt_loader_csrf');
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  options = { ...options, headers, credentials: 'same-origin' };
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response.headers.get('content-type')?.includes('json') ? response.json() : response;
}

async function pollJob(id, onUpdate) {
  while (true) {
    const job = await api(`/api/jobs/${id}`); onUpdate(job);
    if (job.status === 'done') { loadBilling().catch(() => {}); return job; }
    if (job.status === 'deleted') throw new Error(job.message || 'Видео удалено');
    if (job.status === 'error') {
      loadBilling().catch(() => {});
      throw new Error(job.message || 'Задание завершилось ошибкой');
    }
    await sleep(1500);
  }
}

function clamp(value, minimum, maximum) { return Math.min(maximum, Math.max(minimum, value)); }

function youtubeVideoId(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.replace(/^www\./, '');
    if (host === 'youtu.be') return url.pathname.split('/').filter(Boolean)[0] || null;
    if (!['youtube.com', 'm.youtube.com'].includes(host)) return null;
    const parts = url.pathname.split('/').filter(Boolean);
    if (parts[0] === 'shorts' && parts[1]) return parts[1];
    if (parts[0] === 'watch') return url.searchParams.get('v');
  } catch (_) {}
  return null;
}

function showSourceVideo(url, thumbnail = '', title = '') {
  const videoId = youtubeVideoId(url);
  if (!videoId || videoId.length !== 11) return;
  state.sourceVideoId = videoId;
  const preview = $('#stage-video-preview');
  preview.onerror = () => {
    const fallback = `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
    if (preview.src !== fallback) preview.src = fallback;
  };
  preview.src = thumbnail || `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;
  preview.classList.remove('hidden');
  const player = $('#stage-video-player');
  player.src = `https://www.youtube-nocookie.com/embed/${videoId}?playsinline=1&rel=0`;
  player.classList.remove('hidden');
  const label = $('#stage-video-label');
  label.textContent = title || `YouTube · ${videoId}`; label.classList.remove('hidden');
  $('#stage-placeholder').classList.add('hidden');
}

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

function readCookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const part = document.cookie.split('; ').find((value) => value.startsWith(prefix));
  return part ? decodeURIComponent(part.slice(prefix.length)) : '';
}

function showAuthenticated(user) {
  state.currentUser = user;
  $('#account-email').textContent = user.display_name || user.email;
  $('#account-email').title = user.email;
  $('#account-credits').textContent = `${user.credit_balance} кредитов`;
  $('#auth-screen').classList.add('hidden');
  $('#app-shell').classList.remove('hidden');
  const needsVerification = state.authConfig.email_verification_required && !user.email_verified;
  $('#verification-banner').classList.toggle('hidden', !needsVerification);
  if (needsVerification) return;
  loadBilling().catch(() => {});
  if (!state.paymentResumed) { state.paymentResumed = true; resumePayment().catch(() => {}); }
  if (!state.importResumed) { state.importResumed = true; resumeImport(); }
}

function formatPlanPrice(plan) {
  if (!plan.price_minor) return 'Бесплатно';
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency', currency: plan.currency, maximumFractionDigits: 0
  }).format(plan.price_minor / 100);
}

async function loadBilling() {
  if (!state.currentUser) return;
  const [summary, plans, ledger, paymentConfig] = await Promise.all([
    api('/api/billing/summary'),
    api('/api/billing/plans'),
    api('/api/billing/ledger?limit=8'),
    api('/api/payments/config')
  ]);
  $('#billing-available').textContent = summary.available;
  $('#billing-reserved').textContent = summary.reserved;
  $('#billing-balance').textContent = summary.balance;
  $('#billing-plan-name').textContent = summary.plan
    ? `Текущий тариф: ${summary.plan.name}` : 'Тариф не назначен';
  $('#account-credits').textContent = `${summary.available} кредитов`;
  $('#account-credits').title = summary.reserved
    ? `Ещё ${summary.reserved} кредитов зарезервировано заданиями` : '';

  const planContainer = $('#billing-plans'); planContainer.replaceChildren();
  for (const plan of plans) {
    const card = document.createElement('article');
    card.className = `plan-card${plan.id === summary.plan?.id ? ' current' : ''}`;
    const title = document.createElement('h3'); title.textContent = plan.name;
    const description = document.createElement('p'); description.textContent = plan.description || '';
    const footer = document.createElement('footer');
    const credits = document.createElement('b'); credits.textContent = `${plan.monthly_credits} кредитов`;
    const price = document.createElement('small');
    price.textContent = plan.id === summary.plan?.id ? 'Текущий тариф' : formatPlanPrice(plan);
    footer.append(credits, price); card.append(title, description, footer);
    if (plan.price_minor > 0 && plan.id !== summary.plan?.id) {
      const action = document.createElement('button');
      action.className = 'secondary plan-action'; action.type = 'button';
      action.disabled = !paymentConfig.enabled;
      action.textContent = paymentConfig.enabled ? 'Выбрать тариф' : 'Оплата пока не настроена';
      action.addEventListener('click', () => beginCheckout(plan.id, action));
      card.append(action);
    }
    planContainer.append(card);
  }

  const subscriptionAction = $('#billing-subscription-action');
  const canManage = summary.subscription_status === 'active' && summary.plan?.id !== 'free';
  subscriptionAction.classList.toggle('hidden', !canManage);
  subscriptionAction.dataset.action = summary.cancel_at_period_end ? 'resume' : 'cancel';
  subscriptionAction.textContent = summary.cancel_at_period_end
    ? 'Возобновить автопродление' : 'Отключить автопродление';

  const ledgerContainer = $('#billing-ledger'); ledgerContainer.replaceChildren();
  if (!ledger.length) ledgerContainer.textContent = 'Операций пока нет.';
  for (const operation of ledger) {
    const row = document.createElement('div'); row.className = 'ledger-row';
    const label = document.createElement('span'); label.textContent = operation.description || operation.operation_type;
    const amount = document.createElement('b');
    amount.className = operation.amount > 0 ? 'positive' : 'negative';
    amount.textContent = `${operation.amount > 0 ? '+' : ''}${operation.amount}`;
    row.append(label, amount); ledgerContainer.append(row);
  }
}

async function beginCheckout(planId, button) {
  const oldText = button.textContent; button.disabled = true; button.textContent = 'Создаю платёж…';
  try {
    const payment = await api('/api/payments/checkout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: planId })
    });
    if (payment.status === 'succeeded') {
      showToast('Оплата подтверждена, кредиты начислены.'); await loadBilling(); return;
    }
    if (!payment.confirmation_url) throw new Error('ЮKassa не вернула ссылку подтверждения.');
    location.assign(payment.confirmation_url);
  } catch (error) {
    showToast(error.message); button.disabled = false; button.textContent = oldText;
  }
}

async function resumePayment() {
  const paymentId = new URLSearchParams(location.search).get('payment');
  if (!paymentId) return;
  showToast('Проверяю результат оплаты…');
  let payment;
  try {
    payment = await api(`/api/payments/${encodeURIComponent(paymentId)}/sync`, { method: 'POST' });
  } catch (error) {
    if (![409, 502, 503].includes(error.status)) throw error;
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    payment = payment || await api(`/api/payments/${encodeURIComponent(paymentId)}`);
    if (payment.status === 'succeeded') {
      const cleanUrl = new URL(location.href); cleanUrl.searchParams.delete('payment');
      history.replaceState({}, '', cleanUrl);
      showToast('Оплата подтверждена, кредиты начислены.'); await loadBilling(); return;
    }
    if (['canceled', 'error'].includes(payment.status)) {
      showToast(payment.failure_reason || 'Платёж не завершён.'); return;
    }
    payment = null; await sleep(3000);
  }
  showToast('Платёж ещё обрабатывается. Статус сохранён в аккаунте.');
}

$('#billing-subscription-action').addEventListener('click', async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try {
    const action = button.dataset.action;
    await api(`/api/billing/subscription/${action}`, { method: 'POST' });
    await loadBilling();
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
});

function showAuthentication() {
  state.currentUser = null; state.importResumed = false; state.paymentResumed = false;
  $('#app-shell').classList.add('hidden');
  $('#verification-banner').classList.add('hidden');
  $('#auth-screen').classList.remove('hidden');
}

async function submitAuthForm(form, endpoint, statusElement, payload) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true; statusElement.className = 'auth-status'; statusElement.textContent = 'Проверяю…';
  try {
    const user = await api(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    form.reset(); statusElement.classList.add('success'); statusElement.textContent = 'Готово';
    showAuthenticated(user);
  } catch (error) {
    statusElement.classList.add('error'); statusElement.textContent = error.message;
  } finally { button.disabled = false; }
}

$('#login-form').addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuthForm(event.currentTarget, '/api/auth/login', $('#login-status'), {
    email: $('#login-email').value, password: $('#login-password').value
  });
});

$('#register-form').addEventListener('submit', (event) => {
  event.preventDefault();
  submitAuthForm(event.currentTarget, '/api/auth/register', $('#register-status'), {
    display_name: $('#register-name').value, email: $('#register-email').value,
    password: $('#register-password').value
  });
});

function showRecoveryForm(form) {
  $('#login-form').classList.toggle('hidden', Boolean(form));
  $('#register-form').classList.toggle('hidden', Boolean(form) || !state.authConfig.registration_enabled);
  $('#forgot-form').classList.toggle('hidden', form !== 'forgot');
  $('#reset-form').classList.toggle('hidden', form !== 'reset');
}

$('#forgot-toggle').addEventListener('click', () => showRecoveryForm('forgot'));
document.querySelectorAll('.auth-back').forEach((button) => {
  button.addEventListener('click', () => showRecoveryForm(null));
});

$('#forgot-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const status = $('#forgot-status'); button.disabled = true;
  status.className = 'auth-status'; status.textContent = 'Отправляю…';
  try {
    await api('/api/auth/password/forgot', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: $('#forgot-email').value })
    });
    status.classList.add('success');
    status.textContent = 'Если аккаунт существует, письмо уже отправлено.';
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
  finally { button.disabled = false; }
});

$('#reset-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const status = $('#reset-status'); button.disabled = true;
  try {
    await api('/api/auth/password/reset', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: state.accountToken, password: $('#reset-password').value })
    });
    state.accountToken = null; history.replaceState({}, '', `${location.pathname}${location.search}`);
    event.currentTarget.reset(); showRecoveryForm(null);
    $('#login-status').className = 'auth-status success';
    $('#login-status').textContent = 'Пароль изменён. Войдите заново.';
  } catch (error) { status.className = 'auth-status error'; status.textContent = error.message; }
  finally { button.disabled = false; }
});

$('#resend-verification').addEventListener('click', async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try {
    await api('/api/auth/verification/request', { method: 'POST' });
    showToast('Письмо отправлено. Проверьте также папку «Спам».');
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
});

$('#change-password-button').addEventListener('click', () => $('#password-dialog').showModal());
$('#password-dialog-cancel').addEventListener('click', () => $('#password-dialog').close());
$('#change-password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const status = $('#change-password-status');
  try {
    await api('/api/auth/password/change', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: $('#current-password').value,
        new_password: $('#new-password').value
      })
    });
    event.currentTarget.reset(); $('#password-dialog').close(); showToast('Пароль изменён.');
  } catch (error) { status.className = 'auth-status error'; status.textContent = error.message; }
});

$('#logout-button').addEventListener('click', async () => {
  const button = $('#logout-button'); button.disabled = true;
  try { await api('/api/auth/logout', { method: 'POST' }); }
  finally { localStorage.removeItem('ytLoaderImportJob'); showAuthentication(); button.disabled = false; }
});

function overlayFileKey(file) { return `${file.name}:${file.size}:${file.lastModified}`; }

async function uploadOverlayFile(file) {
  const key = overlayFileKey(file);
  const cachedToken = state.logoTokens.get(key);
  if (cachedToken) return { token: cachedToken, preview_url: state.overlayPreviewUrls.get(key) };
  let upload = state.logoUploads.get(key);
  if (!upload) {
    upload = (async () => {
      const form = new FormData(); form.append('file', file);
      const result = await api('/api/logos', { method: 'POST', body: form });
      state.logoTokens.set(key, result.token);
      state.overlayPreviewUrls.set(key, result.preview_url);
      return result;
    })();
    state.logoUploads.set(key, upload);
  }
  try { return await upload; }
  finally { state.logoUploads.delete(key); }
}

async function showGeneratedOverlayPreview(file, expectedIndex) {
  if (state.overlayFiles[expectedIndex] !== file) return;
  const container = $('#overlay-media');
  const pending = document.createElement('span'); pending.className = 'overlay-preview-error';
  pending.textContent = 'Готовлю кадр для браузера…'; container.replaceChildren(pending);
  try {
    const result = await uploadOverlayFile(file);
    if (state.overlayFiles[expectedIndex] !== file) return;
    const image = document.createElement('img'); image.alt = ''; image.src = result.preview_url;
    image.addEventListener('load', updateOverlayPreview);
    image.addEventListener('error', () => { pending.textContent = 'Предпросмотр недоступен'; container.replaceChildren(pending); });
    container.replaceChildren(image);
  } catch (error) {
    pending.textContent = error.message; container.replaceChildren(pending); showToast(error.message);
  }
}

function showOverlayFile(file) {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  const expectedIndex = state.activeOverlayIndex;
  const extension = file.name.split('.').pop()?.toLowerCase();
  const videoExtensions = new Set(['mov', 'mp4', 'm4v', 'webm', 'mkv', 'avi', 'mpeg', 'mpg']);
  const media = file.type.startsWith('video/') || videoExtensions.has(extension)
    ? document.createElement('video') : document.createElement('img');
  media.src = state.previewUrl;
  if (media instanceof HTMLVideoElement) {
    media.muted = true; media.autoplay = true; media.loop = true; media.playsInline = true;
    media.addEventListener('loadeddata', () => { media.play().catch(() => {}); updateOverlayPreview(); });
    media.addEventListener('error', () => showGeneratedOverlayPreview(file, expectedIndex), { once: true });
  } else {
    media.alt = ''; media.addEventListener('load', updateOverlayPreview);
    media.addEventListener('error', () => showGeneratedOverlayPreview(file, expectedIndex), { once: true });
  }
  $('#overlay-media').replaceChildren(media);
  $('#overlay-object').classList.remove('hidden');
  if (state.sourceVideoId) $('#stage-placeholder').classList.add('hidden');
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
  $('#overlay-object').classList.add('hidden');
  $('#stage-placeholder').classList.toggle('hidden', Boolean(state.sourceVideoId));
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
    state.overlayFiles = []; state.logoTokens.clear(); state.overlayPreviewUrls.clear(); renderOverlayFileList(); clearOverlayPreview();
    $('#logo-name').textContent = 'Без оверлея'; return;
  }
  const oversized = files.find((file) => file.size > 256 * 1024 * 1024);
  if (oversized) {
    showToast(`${oversized.name}: файл больше 256 МБ`); e.target.value = ''; return;
  }
  state.overlayFiles = files; state.logoTokens.clear(); state.overlayPreviewUrls.clear();
  state.logoUploads.clear(); state.activeOverlayIndex = 0;
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
      5, 100
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
    const key = overlayFileKey(file);
    let token = state.logoTokens.get(key);
    if (!token) {
      showToast(`Загружаю оверлей ${index + 1}/${state.overlayFiles.length}…`);
      const result = await uploadOverlayFile(file); token = result.token;
    }
    tokens.push(token);
  }
  return tokens;
}

$('#channel-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.batchRunning) { showToast('Дождитесь завершения пакетной обработки.'); return; }
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
  state.itemCards = new Map(); state.batchRunning = false;
  $('#result-count').textContent = `${items.length} роликов · видео обрабатываются по одному`;
  for (const item of items) {
    const card = document.createElement('article'); card.className = 'item';
    const selector = document.createElement('label'); selector.className = 'video-selector';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'video-select';
    checkbox.setAttribute('aria-label', `Выбрать ролик ${item.title}`); selector.append(checkbox);
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
    const record = { item, card, checkbox, videoButton, note, completed: false };
    checkbox.addEventListener('change', () => {
      card.classList.toggle('selected', checkbox.checked);
      if (checkbox.checked) showSourceVideo(item.url, item.thumbnail, item.title);
      updateBatchSelection();
    });
    image.addEventListener('click', () => showSourceVideo(item.url, item.thumbnail, item.title));
    image.title = 'Показать этот ролик в конструкторе';
    videoButton.addEventListener('click', async () => {
      showSourceVideo(item.url, item.thumbnail, item.title);
      if (await startDownloadUrl(item.url, videoButton, note)) markVideoCompleted(record);
    });
    loadBilling().catch(() => {});
    actions.append(videoButton, metadata, note); card.append(selector, image, info, actions); container.append(card);
    state.itemCards.set(item.id, record);
  }
  $('#batch-toolbar').classList.toggle('hidden', items.length === 0);
  $('#batch-status').className = 'status hidden';
  updateBatchSelection();
  $('#results-section').classList.remove('hidden');
}

function selectableRecords() {
  return [...state.itemCards.values()].filter((record) => !record.completed);
}

function selectedRecords() {
  return selectableRecords().filter((record) => record.checkbox.checked);
}

function updateBatchSelection() {
  const count = selectedRecords().length;
  $('#selected-count').textContent = `Выбрано: ${count}`;
  const button = $('#prepare-selected');
  button.textContent = count ? `Подготовить выбранные · ${count}` : 'Подготовить выбранные';
  button.disabled = state.batchRunning || count === 0;
  $('#select-all-videos').disabled = state.batchRunning || selectableRecords().length === 0;
  $('#clear-video-selection').disabled = state.batchRunning || count === 0;
}

function markVideoCompleted(record) {
  record.completed = true; record.checkbox.checked = false; record.checkbox.disabled = true;
  record.card.classList.remove('selected', 'processing'); record.card.classList.add('ready');
  updateBatchSelection();
}

$('#select-all-videos').addEventListener('click', () => {
  for (const record of selectableRecords()) { record.checkbox.checked = true; record.card.classList.add('selected'); }
  updateBatchSelection();
});

$('#clear-video-selection').addEventListener('click', () => {
  for (const record of selectableRecords()) { record.checkbox.checked = false; record.card.classList.remove('selected'); }
  updateBatchSelection();
});

$('#prepare-selected').addEventListener('click', async () => {
  const records = selectedRecords(); if (!records.length || state.batchRunning) return;
  const status = $('#batch-status'); state.batchRunning = true; status.className = 'status';
  for (const record of selectableRecords()) { record.checkbox.disabled = true; record.videoButton.disabled = true; }
  updateBatchSelection();
  let completed = 0; let failed = 0;
  try {
    const batchSettings = currentDownloadSettings();
    status.textContent = `Загружаю оверлеи и готовлю очередь из ${records.length} роликов…`;
    const logoTokens = await ensureOverlaysUploaded();
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index]; record.card.classList.add('processing');
      showSourceVideo(record.item.url, record.item.thumbnail, record.item.title);
      status.textContent = `Обрабатывается ${index + 1} из ${records.length}: ${record.item.title}`;
      const success = await startDownloadUrl(
        record.item.url, record.videoButton, record.note, logoTokens, batchSettings
      );
      record.card.classList.remove('processing');
      if (success) { completed += 1; markVideoCompleted(record); }
      else { failed += 1; }
    }
    status.classList.toggle('error', failed > 0);
    status.textContent = failed
      ? `Очередь завершена: готово ${completed}, с ошибкой ${failed}. Ошибочные ролики можно выбрать повторно.`
      : `Очередь завершена: готово ${completed} из ${records.length}. Скачайте файлы кнопками в карточках.`;
  } catch (error) {
    status.classList.add('error'); status.textContent = error.message;
  } finally {
    state.batchRunning = false;
    for (const record of selectableRecords()) {
      record.checkbox.disabled = false; record.videoButton.disabled = false;
    }
    updateBatchSelection();
  }
});

function currentDownloadSettings() {
  return {
    opacity: Number($('#opacity').value), width_percent: Number($('#logo-width').value),
    position_x: Math.round(state.positionX), position_y: Math.round(state.positionY),
    max_height: Number($('#resolution').value), metadata_mode: $('#metadata-mode').value
  };
}

function downloadPayload(url, logoTokens, settings = null) {
  return { url, logo_tokens: logoTokens, ...(settings || currentDownloadSettings()) };
}

$('#metadata-mode').addEventListener('change', (event) => {
  const help = {
    none: 'Сохраняет метаданные, которые попадут в итоговый MP4.',
    strip: 'Удаляет исходные поля без подмены устройства.',
    synthetic: 'Удаляет исходные поля и записывает синтетический профиль Apple и дату. Без GPS и изменения кадров.'
  };
  $('#metadata-mode-help').textContent = help[event.target.value] || help.strip;
});

function showReadyDownload(job, oldButton, note) {
  const overlayCount = Number(job.result?.overlay_count || 0);
  const readyLabel = overlayCount > 1 ? `Скачать ZIP · ${overlayCount} вариантов` : 'Скачать MP4';
  const repeatLabel = overlayCount > 1 ? 'Скачать ZIP ещё раз' : 'Скачать ещё раз';
  const downloadButton = document.createElement('button');
  downloadButton.className = 'primary'; downloadButton.textContent = readyLabel;
  oldButton.replaceWith(downloadButton);
  const modeLabel = {
    none: 'метаданные сохранены', strip: 'метаданные очищены',
    synthetic: 'метаданные заменены синтетическим профилем'
  }[job.result?.metadata_mode] || 'метаданные обработаны';
  note.textContent = overlayCount > 1
    ? `Готово ${overlayCount} вариантов. В ZIP каждый оверлей лежит в своей папке.`
    : `Видео готово, ${modeLabel}. Таймер удаления запустится при скачивании.`;

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

async function startDownloadUrl(url, button, note, uploadedLogoTokens = null, downloadSettings = null) {
  button.disabled = true; note.textContent = 'Подготовка задания…';
  try {
    const logoTokens = uploadedLogoTokens || await ensureOverlaysUploaded();
    const created = await api('/api/videos/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(downloadPayload(url, logoTokens, downloadSettings))
    });
    loadBilling().catch(() => {});
    const job = await pollJob(created.id, (current) => { note.textContent = current.message || current.status; });
    showReadyDownload(job, button, note);
    return true;
  } catch (error) { note.textContent = error.message; button.disabled = false; return false; }
}

$('#direct-video-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.batchRunning) { showToast('Дождитесь завершения пакетной обработки.'); return; }
  const submitButton = event.submitter; const box = $('#direct-video-actions');
  const buttons = $('#direct-video-buttons'); const note = $('#direct-video-note');
  box.classList.remove('hidden'); buttons.replaceChildren();
  const workButton = document.createElement('button'); workButton.className = 'primary';
  workButton.textContent = 'Подготовка…'; buttons.append(workButton);
  submitButton.disabled = true;
  showSourceVideo($('#direct-video-url').value);
  await startDownloadUrl($('#direct-video-url').value, workButton, note);
  submitButton.disabled = false;
});

$('#direct-video-url').addEventListener('input', (event) => showSourceVideo(event.target.value));

api('/api/health').then((health) => {
  if (health.status !== 'ok') $('#health').innerHTML = '<i style="background:#e0a93b"></i> База данных недоступна';
}).catch(() => { $('#health').innerHTML = '<i style="background:#ff6b7d"></i> Сервер недоступен'; });

async function resumeImport() {
  const queryJob = new URLSearchParams(location.search).get('job');
  let id = queryJob || localStorage.getItem('ytLoaderImportJob');
  if (!id) {
    const jobs = await api('/api/jobs?kind=import&limit=1');
    id = jobs[0]?.id || null;
    if (id) localStorage.setItem('ytLoaderImportJob', id);
  }
  if (!id) return;
  const status = $('#import-status'); status.className = 'status'; status.textContent = 'Восстанавливаю последнее задание…';
  try {
    const job = await pollJob(id, (current) => { status.textContent = current.message || current.status; });
    if (job.kind !== 'import') return;
    const items = await api(job.items_url); state.importId = id; renderItems(items, id);
    $('#csv-link').href = job.csv_url; status.textContent = `Готово: найдено ${items.length} Shorts`;
  } catch (error) {
    if (error.status === 404 && !queryJob) {
      localStorage.removeItem('ytLoaderImportJob');
      const jobs = await api('/api/jobs?kind=import&limit=1');
      if (jobs[0]?.id && jobs[0].id !== id) {
        localStorage.setItem('ytLoaderImportJob', jobs[0].id);
        return resumeImport();
      }
    }
    status.classList.add('error'); status.textContent = error.message;
  }
}

async function bootstrapAuth() {
  try {
    const config = await api('/api/auth/config');
    state.authConfig = config;
    $('#register-form').classList.toggle('hidden', !config.registration_enabled);
    $('#forgot-toggle').classList.toggle('hidden', !config.password_reset_enabled);
  } catch (_) {}
  const fragment = new URLSearchParams(location.hash.slice(1));
  const verificationToken = fragment.get('verify');
  const resetToken = fragment.get('reset');
  if (verificationToken) {
    try {
      await api('/api/auth/verification/confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: verificationToken })
      });
      history.replaceState({}, '', `${location.pathname}${location.search}`);
      showToast('Email подтверждён.');
    } catch (error) {
      showAuthentication(); $('#login-status').className = 'auth-status error';
      $('#login-status').textContent = error.message; return;
    }
  } else if (resetToken) {
    state.accountToken = resetToken; showAuthentication(); showRecoveryForm('reset'); return;
  }
  try {
    showAuthenticated(await api('/api/auth/me'));
  } catch (error) {
    showAuthentication();
    if (error.status && error.status !== 401) {
      $('#login-status').className = 'auth-status error';
      $('#login-status').textContent = error.message;
    }
  }
}

bootstrapAuth();
