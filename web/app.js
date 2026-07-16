const $ = (selector) => document.querySelector(selector);
const state = {
  importId: null, overlayFiles: [], logoTokens: new Map(), activeOverlayIndex: 0,
  previewUrl: null, overlayPreviewUrls: new Map(), logoUploads: new Map(),
  sourceVideoId: null, positionX: 50, positionY: 96,
  itemCards: new Map(), batchRunning: false, currentUser: null, importResumed: false,
  paymentResumed: false, authConfig: {}, accountToken: null, currentPage: 'dashboard',
  workspaces: [], currentWorkspaceId: null, projects: [], currentProjectId: null,
  workspaceMembers: [], approvalWorkflow: null, contentItems: [], contentView: 'board',
  libraryItems: [], editingContentId: null, aiConfig: null, adminLoaded: false
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const workspacePageTitles = {
  dashboard: 'Обзор', content: 'Контент-план', documents: 'Документы',
  library: 'Медиатека', video: 'Видео', approvals: 'Согласования',
  ai: 'AI-помощник', billing: 'Тариф и кредиты', admin: 'Управление SaaS'
};

function workspacePageFromHash() {
  if (!location.hash.startsWith('#/')) return null;
  return location.hash.slice(2).split(/[/?]/, 1)[0] || null;
}

function showWorkspacePage(page, syncUrl = false) {
  if (page === 'admin' && !state.currentUser?.is_admin) page = 'dashboard';
  const target = document.querySelector(`[data-page="${page}"]`);
  if (!target || !workspacePageTitles[page]) page = 'dashboard';
  state.currentPage = page;
  document.querySelectorAll('[data-page]').forEach((element) => {
    element.classList.toggle('hidden', element.dataset.page !== page);
  });
  document.querySelectorAll('.workspace-nav-item[data-navigate]').forEach((element) => {
    const active = element.dataset.navigate === page;
    element.classList.toggle('active', active);
    if (active) element.setAttribute('aria-current', 'page');
    else element.removeAttribute('aria-current');
  });
  const title = $('#workspace-page-title');
  if (title) title.textContent = workspacePageTitles[page];
  window.AAPAppMotion?.pageEntered(target, page, title);
  document.title = `${workspacePageTitles[page]} · All As Planned`;
  if (syncUrl && workspacePageFromHash() !== page) {
    history.pushState({ page }, '', `${location.pathname}${location.search}#/${page}`);
  }
  if (page === 'approvals' && state.currentProjectId) loadApprovalWorkflow().catch(showWorkspaceError);
  if (['content', 'documents'].includes(page) && state.currentProjectId) loadContent().catch(showWorkspaceError);
  if (page === 'library' && state.currentProjectId) loadLibrary().catch(showWorkspaceError);
  if (page === 'ai' && state.currentProjectId) loadAIStudio().catch(showWorkspaceError);
  if (page === 'dashboard' && state.currentWorkspaceId) loadOnboarding().catch(() => {});
  if (page === 'admin' && state.currentUser?.is_admin) loadAdmin().catch(showWorkspaceError);
  window.scrollTo({ top: 0, behavior: 'instant' });
}

let toastTimer = null;
function showToast(text, tone = 'neutral') {
  const toast = $('#toast'); toast.textContent = text; toast.classList.remove('hidden');
  window.AAPAppMotion?.toastIn(toast, tone);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    if (!window.AAPAppMotion?.toastOut(toast)) toast.classList.add('hidden');
  }, 3500);
}

async function api(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = readCookie('yt_loader_csrf');
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  options = { ...options, headers, credentials: 'same-origin' };
  const motionRequest = window.AAPAppMotion?.networkStart(method, url);
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      let message = `Ошибка ${response.status}`;
      try { message = (await response.json()).detail || message; } catch (_) {}
      const error = new Error(message); error.status = response.status; throw error;
    }
    return response.headers.get('content-type')?.includes('json') ? await response.json() : response;
  } finally {
    window.AAPAppMotion?.networkEnd(motionRequest);
  }
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

function sourceThumbnailUrl(value) {
  if (!value || value.startsWith('/') || value.startsWith('blob:') || value.startsWith('data:')) return value;
  return `/api/sources/thumbnail?url=${encodeURIComponent(value)}`;
}

function createBrandedEmptyState(title, detail) {
  const empty = document.createElement('div'); empty.className = 'empty-brand-state';
  const image = document.createElement('img'); image.src = '/assets/brand-empty-state.svg';
  image.alt = ''; image.width = 360; image.height = 210; image.loading = 'lazy';
  const heading = document.createElement('h3'); heading.textContent = title;
  const description = document.createElement('p'); description.textContent = detail;
  empty.append(image, heading, description); return empty;
}

function showSourceVideo(url, thumbnail = '', title = '') {
  const videoId = youtubeVideoId(url);
  if ((!videoId || videoId.length !== 11) && !thumbnail) return;
  state.sourceVideoId = videoId || url;
  const preview = $('#stage-video-preview');
  delete preview.dataset.fallbackApplied;
  preview.onerror = videoId ? () => {
    const fallback = sourceThumbnailUrl(`https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`);
    if (!preview.dataset.fallbackApplied) {
      preview.dataset.fallbackApplied = 'true'; preview.src = fallback;
    }
  } : null;
  preview.src = sourceThumbnailUrl(thumbnail || (videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : ''));
  preview.classList.remove('hidden');
  const player = $('#stage-video-player');
  if (videoId) {
    player.src = `https://www.youtube-nocookie.com/embed/${videoId}?playsinline=1&rel=0`;
    player.classList.remove('hidden');
  } else {
    player.src = ''; player.classList.add('hidden');
  }
  const label = $('#stage-video-label');
  label.textContent = title || (videoId ? `YouTube · ${videoId}` : 'Предпросмотр видео');
  label.classList.remove('hidden');
  $('#stage-placeholder').classList.add('hidden');
}

async function showExternalSourcePreview(url) {
  if (!url) return;
  if (youtubeVideoId(url)) { showSourceVideo(url); return; }
  const preview = await api(`/api/sources/preview?url=${encodeURIComponent(url)}`);
  showSourceVideo(
    preview.url, preview.thumbnail,
    `${preview.platform.toUpperCase()} · ${preview.title}`
  );
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
  window.AAPAppMotion?.appEntered();
  $('#admin-nav-button').classList.toggle('hidden', !user.is_admin);
  showWorkspacePage(workspacePageFromHash() || state.currentPage);
  const needsVerification = state.authConfig.email_verification_required && !user.email_verified;
  $('#verification-banner').classList.toggle('hidden', !needsVerification);
  if (needsVerification) return;
  loadWorkspaces().catch(showWorkspaceError);
  loadBilling().catch(() => {});
  if (!state.paymentResumed) { state.paymentResumed = true; resumePayment().catch(() => {}); }
  if (!state.importResumed) { state.importResumed = true; resumeImport(); }
}

const workspaceRoleLabels = {
  owner: 'Владелец', admin: 'Администратор', editor: 'Редактор',
  viewer: 'Наблюдатель', client: 'Клиент'
};

function showWorkspaceError(error) {
  showToast(error?.message || 'Не удалось загрузить рабочее пространство.', 'error');
}

function currentWorkspace() {
  return state.workspaces.find((workspace) => workspace.id === state.currentWorkspaceId) || null;
}

function currentProject() {
  return state.projects.find((project) => project.id === state.currentProjectId) || null;
}

async function loadWorkspaces(preferredId = null) {
  state.workspaces = await api('/api/workspaces');
  const select = $('#workspace-select'); select.replaceChildren();
  for (const workspace of state.workspaces) {
    const option = document.createElement('option');
    option.value = workspace.id; option.textContent = workspace.name; select.append(option);
  }
  const stored = preferredId || localStorage.getItem('allAsPlannedWorkspace');
  const active = state.workspaces.find((workspace) => workspace.id === stored) || state.workspaces[0];
  if (!active) return;
  select.value = active.id;
  await activateWorkspace(active.id);
}

async function activateWorkspace(workspaceId) {
  const workspace = state.workspaces.find((item) => item.id === workspaceId);
  if (!workspace) return;
  state.currentWorkspaceId = workspace.id;
  localStorage.setItem('allAsPlannedWorkspace', workspace.id);
  $('#workspace-select').value = workspace.id;
  $('#current-workspace-name').textContent = workspace.name;
  $('#current-workspace-role').textContent = workspaceRoleLabels[workspace.role] || workspace.role;
  const [projects, members] = await Promise.all([
    api(`/api/workspaces/${workspace.id}/projects`),
    api(`/api/workspaces/${workspace.id}/members`)
  ]);
  state.projects = projects; state.workspaceMembers = members;
  const storedProject = localStorage.getItem(`allAsPlannedProject:${workspace.id}`);
  state.currentProjectId = projects.some((project) => project.id === storedProject)
    ? storedProject : projects.find((project) => project.status === 'active')?.id || projects[0]?.id || null;
  renderWorkspaceProjects(); renderWorkspaceMembers();
  if (state.currentPage === 'approvals' && state.currentProjectId) await loadApprovalWorkflow();
  if (['content', 'documents'].includes(state.currentPage) && state.currentProjectId) await loadContent();
  if (state.currentPage === 'library' && state.currentProjectId) await loadLibrary();
  if (state.currentPage === 'ai' && state.currentProjectId) await loadAIStudio();
  if (state.currentPage === 'dashboard') await loadOnboarding();
}

function selectProject(projectId) {
  if (!state.projects.some((project) => project.id === projectId)) return;
  state.currentProjectId = projectId;
  localStorage.setItem(`allAsPlannedProject:${state.currentWorkspaceId}`, projectId);
  renderWorkspaceProjects();
  if (state.currentPage === 'approvals') loadApprovalWorkflow().catch(showWorkspaceError);
  if (['content', 'documents'].includes(state.currentPage)) loadContent().catch(showWorkspaceError);
  if (state.currentPage === 'library') loadLibrary().catch(showWorkspaceError);
  if (state.currentPage === 'ai') loadAIStudio().catch(showWorkspaceError);
  if (state.currentPage === 'dashboard') loadOnboarding().catch(() => {});
}

const onboardingStorageKey = 'allAsPlannedOnboardingV1';

function renderOnboarding(steps) {
  const panel = $('#onboarding-panel');
  if (localStorage.getItem(onboardingStorageKey) === 'dismissed') {
    panel.classList.add('hidden'); return;
  }
  panel.classList.remove('hidden');
  const container = $('#onboarding-steps'); container.replaceChildren();
  for (const [index, step] of steps.entries()) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = `onboarding-step${step.done ? ' done' : ''}`;
    button.dataset.navigate = step.page; button.disabled = step.done;
    const marker = document.createElement('span'); marker.textContent = step.done ? '✓' : String(index + 1);
    const title = document.createElement('strong'); title.textContent = step.title;
    const detail = document.createElement('small'); detail.textContent = step.detail;
    button.append(marker, title, detail); container.append(button);
  }
  if (steps.every((step) => step.done)) {
    $('#dismiss-onboarding').textContent = 'Готово';
  }
}

async function loadOnboarding() {
  if (!state.currentWorkspaceId) return;
  let content = []; let library = [];
  if (state.currentProjectId) {
    [content, library] = await Promise.all([
      api(`/api/projects/${state.currentProjectId}/content`),
      api(`/api/projects/${state.currentProjectId}/library`)
    ]);
  }
  renderOnboarding([
    { done: Boolean(state.currentProjectId), page: 'dashboard', title: 'Создайте проект', detail: 'Разделите работу по брендам или направлениям.' },
    { done: content.length > 0, page: 'content', title: 'Добавьте материал', detail: 'Запланируйте первый пост, ролик или баннер.' },
    { done: library.length > 0, page: 'library', title: 'Соберите медиатеку', detail: 'Прикрепите исходник к карточке контента.' },
    { done: state.workspaceMembers.length > 1, page: 'dashboard', title: 'Пригласите команду', detail: 'Назначьте редактора, клиента или наблюдателя.' }
  ]);
}

function adminDate(value) {
  return value ? new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(new Date(value)) : '—';
}

function adminMoney(amountMinor, currency = 'RUB') {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency, maximumFractionDigits: 0 }).format((amountMinor || 0) / 100);
}

function adminCell(text, className = '') {
  const cell = document.createElement('td'); if (className) cell.className = className;
  cell.textContent = text == null ? '—' : String(text); return cell;
}

function renderAdminOverview(overview) {
  const cards = [
    ['Пользователи', overview.users], ['Подтвердили email', overview.verified_users],
    ['Рабочие пространства', overview.workspaces], ['Активные подписки', overview.active_subscriptions],
    ['MRR', adminMoney(overview.mrr_minor)], ['Файлы', humanFileSize(overview.storage_bytes)]
  ];
  const container = $('#admin-stats'); container.replaceChildren();
  for (const [label, value] of cards) {
    const card = document.createElement('article'); card.className = 'admin-stat';
    const caption = document.createElement('span'); caption.textContent = label;
    const number = document.createElement('strong'); number.textContent = value;
    card.append(caption, number); container.append(card);
  }
}

function renderAdminUsers(users) {
  $('#admin-users-count').textContent = `${users.length} последних`;
  const body = $('#admin-users-body'); body.replaceChildren();
  for (const user of users) {
    const row = document.createElement('tr');
    const identityCell = document.createElement('td'); const identity = document.createElement('div'); identity.className = 'admin-user';
    const name = document.createElement('strong'); name.textContent = user.display_name || user.email;
    const email = document.createElement('small'); email.textContent = user.email; identity.append(name, email); identityCell.append(identity);
    const status = user.is_admin ? 'Администратор' : (user.email_verified ? user.subscription_status : 'Email не подтверждён');
    row.append(identityCell, adminCell(user.plan_id), adminCell(user.credits), adminCell(status, 'admin-status'), adminCell(adminDate(user.created_at)));
    body.append(row);
  }
}

function renderAdminPayments(payments) {
  $('#admin-payments-count').textContent = `${payments.length} последних`;
  const body = $('#admin-payments-body'); body.replaceChildren();
  if (!payments.length) {
    const row = document.createElement('tr'); const empty = adminCell('Платежей пока нет.'); empty.colSpan = 5; row.append(empty); body.append(row); return;
  }
  for (const payment of payments) {
    const row = document.createElement('tr');
    row.append(adminCell(payment.email), adminCell(payment.plan_id), adminCell(adminMoney(payment.amount_minor, payment.currency)), adminCell(payment.status, 'admin-status'), adminCell(adminDate(payment.created_at)));
    body.append(row);
  }
}

async function loadAdmin(force = false) {
  if (!state.currentUser?.is_admin || (state.adminLoaded && !force)) return;
  const [overview, users, payments] = await Promise.all([
    api('/api/admin/overview'), api('/api/admin/users?limit=100'), api('/api/admin/payments?limit=100')
  ]);
  renderAdminOverview(overview); renderAdminUsers(users); renderAdminPayments(payments);
  state.adminLoaded = true;
}

function renderWorkspaceProjects() {
  const container = $('#workspace-projects'); container.replaceChildren();
  if (!state.projects.length) {
    container.append(createBrandedEmptyState(
      'Здесь появится первый проект',
      'Создайте проект для бренда, кампании или отдельного направления.'
    )); return;
  }
  for (const project of state.projects) {
    const card = document.createElement('article');
    card.className = `project-card${project.id === state.currentProjectId ? ' active' : ''}`;
    card.style.setProperty('--project-color', project.color);
    card.tabIndex = 0; card.dataset.projectId = project.id;
    const status = document.createElement('small');
    status.textContent = project.status === 'active' ? 'Активный проект' : 'Архив';
    const title = document.createElement('h3'); title.textContent = project.name;
    const description = document.createElement('p');
    description.textContent = project.description || 'Контент, документы и материалы проекта.';
    const footer = document.createElement('footer');
    const slug = document.createElement('span'); slug.textContent = project.slug;
    const selected = document.createElement('span');
    selected.textContent = project.id === state.currentProjectId ? 'Выбран' : 'Открыть';
    footer.append(slug, selected); card.append(status, title, description, footer);
    card.addEventListener('click', () => selectProject(project.id));
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectProject(project.id); }
    });
    container.append(card);
  }
}

function renderWorkspaceMembers() {
  const container = $('#workspace-members'); container.replaceChildren();
  const workspace = currentWorkspace();
  const canManage = ['owner', 'admin'].includes(workspace?.role);
  $('#add-member-form').classList.toggle('hidden', !canManage);
  for (const member of state.workspaceMembers) {
    const row = document.createElement('div');
    row.className = `member-row${member.role === 'owner' ? ' owner' : ''}`;
    const identity = document.createElement('div');
    const name = document.createElement('strong'); name.textContent = member.display_name || member.email;
    const email = document.createElement('small'); email.textContent = member.email;
    identity.append(name, email);
    const role = document.createElement('select');
    role.disabled = !canManage || member.role === 'owner';
    for (const value of ['admin', 'editor', 'viewer', 'client']) {
      const option = document.createElement('option'); option.value = value;
      option.textContent = workspaceRoleLabels[value]; option.selected = member.role === value; role.append(option);
    }
    role.addEventListener('change', async () => {
      try {
        await api(`/api/workspaces/${state.currentWorkspaceId}/members/${member.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ role: role.value })
        });
        member.role = role.value; showToast('Роль участника обновлена.');
      } catch (error) { role.value = member.role; showWorkspaceError(error); }
    });
    const remove = document.createElement('button'); remove.className = 'ghost';
    remove.type = 'button'; remove.textContent = 'Удалить';
    remove.disabled = !canManage || member.role === 'owner';
    remove.addEventListener('click', async () => {
      if (!confirm(`Удалить ${member.email} из рабочего пространства?`)) return;
      try {
        await api(`/api/workspaces/${state.currentWorkspaceId}/members/${member.id}`, { method: 'DELETE' });
        state.workspaceMembers = state.workspaceMembers.filter((item) => item.id !== member.id);
        renderWorkspaceMembers(); showToast('Участник удалён.');
      } catch (error) { showWorkspaceError(error); }
    });
    row.append(identity, role, remove); container.append(row);
  }
}

function approvalStageRow(stage = {}) {
  const row = document.createElement('div'); row.className = 'approval-stage-row'; row.draggable = true;
  const grip = document.createElement('span'); grip.className = 'stage-grip'; grip.textContent = '⋮⋮';
  const color = document.createElement('input'); color.type = 'color'; color.value = stage.color || '#7c6cff'; color.className = 'stage-color';
  const name = document.createElement('input'); name.type = 'text'; name.maxLength = 120;
  name.value = stage.name || ''; name.placeholder = 'Название этапа'; name.className = 'stage-name';
  const role = document.createElement('select'); role.className = 'stage-role';
  for (const [value, label] of [['', 'Любая роль'], ['editor', 'Редактор'], ['admin', 'Администратор'], ['client', 'Клиент'], ['viewer', 'Наблюдатель']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = label;
    option.selected = (stage.required_role || '') === value; role.append(option);
  }
  const terminalLabel = document.createElement('label'); terminalLabel.className = 'stage-terminal';
  const terminal = document.createElement('input'); terminal.type = 'checkbox'; terminal.checked = Boolean(stage.is_terminal); terminal.className = 'stage-is-terminal';
  terminalLabel.append(terminal, document.createTextNode('Финальный'));
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-stage'; remove.textContent = 'Удалить';
  remove.addEventListener('click', () => { if ($('#approval-stages').children.length > 2) row.remove(); else showToast('В процессе должно остаться минимум два этапа.'); });
  row.addEventListener('dragstart', () => row.classList.add('dragging'));
  row.addEventListener('dragend', () => row.classList.remove('dragging'));
  row.addEventListener('dragover', (event) => {
    event.preventDefault();
    const dragging = $('#approval-stages').querySelector('.dragging');
    if (dragging && dragging !== row) {
      const box = row.getBoundingClientRect();
      row.parentElement.insertBefore(dragging, event.clientY < box.top + box.height / 2 ? row : row.nextSibling);
    }
  });
  row.append(grip, color, name, role, terminalLabel, remove); return row;
}

async function loadApprovalWorkflow() {
  if (!state.currentProjectId) return;
  const workflow = await api(`/api/projects/${state.currentProjectId}/approval-workflow`);
  state.approvalWorkflow = workflow; $('#workflow-name').value = workflow.name;
  const container = $('#approval-stages'); container.replaceChildren();
  workflow.stages.forEach((stage) => container.append(approvalStageRow(stage)));
  const workspace = currentWorkspace();
  const canEdit = ['owner', 'admin', 'editor'].includes(workspace?.role);
  $('#save-workflow-button').disabled = !canEdit; $('#add-approval-stage').disabled = !canEdit;
  container.querySelectorAll('input,select,button').forEach((control) => { control.disabled = !canEdit; });
}

function workflowPayload() {
  return {
    name: $('#workflow-name').value.trim(),
    stages: [...$('#approval-stages').children].map((row) => ({
      name: row.querySelector('.stage-name').value.trim(),
      color: row.querySelector('.stage-color').value,
      required_role: row.querySelector('.stage-role').value || null,
      is_terminal: row.querySelector('.stage-is-terminal').checked
    }))
  };
}

const contentTypeLabels = {
  post: 'Пост', video: 'Видео', banner: 'Баннер', document: 'Документ',
  campaign: 'Кампания', note: 'Заметка'
};

function canEditContent() {
  return ['owner', 'admin', 'editor'].includes(currentWorkspace()?.role);
}

function humanFileSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function contentDate(value, withTime = false) {
  if (!value) return 'Без даты';
  return new Intl.DateTimeFormat('ru-RU', withTime
    ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }
    : { day: '2-digit', month: 'short' }).format(new Date(value));
}

function filteredContentItems() {
  const query = ($('#content-search').value || '').trim().toLocaleLowerCase('ru');
  const type = $('#content-type-filter').value;
  return state.contentItems.filter((item) => {
    if (type && item.item_type !== type) return false;
    if (!query) return true;
    return `${item.title} ${(item.tags || []).join(' ')} ${item.channel || ''}`
      .toLocaleLowerCase('ru').includes(query);
  });
}

function contentCard(item) {
  const card = document.createElement('article'); card.className = 'content-card';
  card.tabIndex = 0; card.draggable = canEditContent(); card.dataset.contentId = item.id;
  const top = document.createElement('div'); top.className = 'content-card-top';
  const kind = document.createElement('span'); kind.className = 'content-kind';
  kind.textContent = contentTypeLabels[item.item_type] || item.item_type;
  const priority = document.createElement('span'); priority.className = `priority-dot ${item.priority}`;
  priority.title = `Приоритет: ${item.priority}`; top.append(kind, priority);
  const title = document.createElement('h4'); title.textContent = item.title;
  const tags = document.createElement('div'); tags.className = 'content-card-tags';
  tags.textContent = (item.tags || []).map((tag) => `#${tag}`).join(' ') || item.channel || 'Без тегов';
  const footer = document.createElement('footer'); footer.className = 'content-card-footer';
  const date = document.createElement('span'); date.textContent = contentDate(item.planned_at, true);
  const owner = document.createElement('span'); owner.textContent = item.assignee?.name || 'Не назначен';
  footer.append(date, owner); card.append(top, title, tags, footer);
  const open = () => openContentEditor(item.id).catch(showWorkspaceError);
  card.addEventListener('click', open);
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') open();
  });
  card.addEventListener('dragstart', (event) => {
    event.dataTransfer.setData('text/content-id', item.id); card.classList.add('dragging');
  });
  card.addEventListener('dragend', () => card.classList.remove('dragging'));
  return card;
}

function renderContentStats() {
  const container = $('#content-stats'); container.replaceChildren();
  const now = Date.now();
  const numbers = [
    ['Всего материалов', state.contentItems.length],
    ['Запланировано', state.contentItems.filter((item) => item.planned_at).length],
    ['Без ответственного', state.contentItems.filter((item) => !item.assignee).length],
    ['Просрочено', state.contentItems.filter((item) => item.due_at && new Date(item.due_at).getTime() < now).length]
  ];
  for (const [label, value] of numbers) {
    const card = document.createElement('div'); card.className = 'content-stat';
    const caption = document.createElement('span'); caption.textContent = label;
    const number = document.createElement('strong'); number.textContent = value;
    card.append(caption, number); container.append(card);
  }
}

async function moveContentToStage(itemId, stageId) {
  try {
    await api(`/api/content/${itemId}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage_id: stageId })
    });
    await loadContent(); showToast('Этап материала обновлён.');
  } catch (error) { showWorkspaceError(error); }
}

function renderContentBoard() {
  const container = $('#content-board'); container.replaceChildren();
  const stages = state.approvalWorkflow?.stages || [];
  const columns = [...stages, { id: '', name: 'Без этапа', color: '#64748b', position: 999 }];
  const items = filteredContentItems();
  for (const stage of columns) {
    const stageItems = items.filter((item) => (item.stage?.id || '') === stage.id);
    const column = document.createElement('section'); column.className = 'content-column';
    column.dataset.stageId = stage.id;
    const heading = document.createElement('div'); heading.className = 'content-column-head';
    const title = document.createElement('h3');
    const dot = document.createElement('span'); dot.className = 'stage-dot';
    dot.style.setProperty('--stage-color', stage.color);
    title.append(dot, document.createTextNode(stage.name));
    const count = document.createElement('span'); count.textContent = stageItems.length;
    heading.append(title, count);
    const body = document.createElement('div'); body.className = 'content-column-body';
    stageItems.forEach((item) => body.append(contentCard(item)));
    if (!stageItems.length) {
      const empty = document.createElement('div'); empty.className = 'empty-column';
      empty.textContent = 'Перетащите материал сюда'; body.append(empty);
    }
    column.addEventListener('dragover', (event) => {
      if (canEditContent()) event.preventDefault();
    });
    column.addEventListener('drop', (event) => {
      event.preventDefault(); const itemId = event.dataTransfer.getData('text/content-id');
      if (itemId) moveContentToStage(itemId, stage.id || null);
    });
    column.append(heading, body); container.append(column);
  }
}

function renderContentCalendar() {
  const container = $('#content-calendar'); container.replaceChildren();
  const today = new Date(); const year = today.getFullYear(); const month = today.getMonth();
  const first = new Date(year, month, 1); const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - mondayOffset);
  const items = filteredContentItems().filter((item) => item.planned_at);
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(start); date.setDate(start.getDate() + index);
    const day = document.createElement('div'); day.className = 'calendar-day';
    if (date.getMonth() !== month) day.classList.add('outside');
    if (date.toDateString() === today.toDateString()) day.classList.add('today');
    const label = document.createElement('time'); label.dateTime = date.toISOString().slice(0, 10);
    label.textContent = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(date);
    day.append(label);
    for (const item of items.filter((entry) => new Date(entry.planned_at).toDateString() === date.toDateString())) {
      const button = document.createElement('button'); button.className = 'calendar-entry';
      button.type = 'button'; button.textContent = item.title;
      button.style.setProperty('--entry-color', item.stage?.color || '#64748b');
      button.addEventListener('click', () => openContentEditor(item.id).catch(showWorkspaceError));
      day.append(button);
    }
    container.append(day);
  }
}

function renderContent() {
  $('#content-project-name').textContent = currentProject()?.name || 'Контент-план';
  $('#create-content-button').disabled = !canEditContent();
  $('#create-document-button').disabled = !canEditContent();
  renderContentStats(); renderContentBoard(); renderContentCalendar(); renderDocuments();
  $('#content-board').classList.toggle('hidden', state.contentView !== 'board');
  $('#content-calendar').classList.toggle('hidden', state.contentView !== 'calendar');
}

function renderDocuments() {
  const container = $('#documents-list'); container.replaceChildren();
  const documents = state.contentItems.filter((item) => ['document', 'note'].includes(item.item_type));
  if (!documents.length) {
    container.append(createBrandedEmptyState(
      'База знаний пока пуста',
      'Создайте первый документ, бриф или рабочую заметку проекта.'
    )); return;
  }
  for (const item of documents) {
    const card = document.createElement('article'); card.className = 'document-card'; card.tabIndex = 0;
    const icon = document.createElement('span'); icon.className = 'document-icon';
    icon.textContent = item.item_type === 'note' ? '◇' : '▤';
    const title = document.createElement('h3'); title.textContent = item.title;
    const description = document.createElement('p');
    description.textContent = (item.tags || []).map((tag) => `#${tag}`).join(' ') || 'Откройте документ, чтобы продолжить работу.';
    const footer = document.createElement('footer');
    const stage = document.createElement('span'); stage.textContent = item.stage?.name || 'Без этапа';
    const updated = document.createElement('span'); updated.textContent = `Изменён ${contentDate(item.updated_at)}`;
    footer.append(stage, updated); card.append(icon, title, description, footer);
    card.addEventListener('click', () => openContentEditor(item.id).catch(showWorkspaceError));
    container.append(card);
  }
}

async function loadContent() {
  if (!state.currentProjectId) return;
  const [items, workflow] = await Promise.all([
    api(`/api/projects/${state.currentProjectId}/content`),
    api(`/api/projects/${state.currentProjectId}/approval-workflow`)
  ]);
  state.contentItems = items; state.approvalWorkflow = workflow; renderContent();
}

function renderLibrary() {
  const container = $('#library-grid'); container.replaceChildren();
  const total = state.libraryItems.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0);
  $('#library-summary').textContent = `${state.libraryItems.length} файлов · ${humanFileSize(total)}`;
  if (!state.libraryItems.length) {
    container.append(createBrandedEmptyState(
      'Медиатека ждёт материалы',
      'Прикрепите файл к карточке контента — он автоматически появится здесь.'
    )); return;
  }
  for (const item of state.libraryItems) {
    const card = document.createElement('article'); card.className = 'library-card';
    const icon = document.createElement('span'); icon.className = 'library-file-icon';
    icon.textContent = (item.name.split('.').pop() || 'FILE').slice(0, 4).toUpperCase();
    const title = document.createElement('h3'); title.textContent = item.name;
    const context = document.createElement('p'); context.textContent = item.content_title;
    const footer = document.createElement('footer');
    const size = document.createElement('span'); size.textContent = humanFileSize(item.size_bytes);
    const link = document.createElement('a'); link.className = 'ghost'; link.href = item.download_url;
    link.textContent = 'Скачать'; footer.append(size, link); card.append(icon, title, context, footer);
    container.append(card);
  }
}

async function loadLibrary() {
  if (!state.currentProjectId) return;
  state.libraryItems = await api(`/api/projects/${state.currentProjectId}/library`);
  renderLibrary();
}

function setAIStatus(selector, message, isError = false) {
  const element = $(selector); element.textContent = message;
  element.className = `status${isError ? ' error' : ''}`;
}

async function downloadableJobUrl(job) {
  const ticket = await api(job.download_ticket_url, { method: 'POST' });
  return ticket.download_url;
}

function renderAIVideoOptions() {
  const select = $('#ai-video-attachment'); select.replaceChildren();
  const videos = state.libraryItems.filter((item) => (item.mime_type || '').startsWith('video/'));
  const empty = document.createElement('option'); empty.value = '';
  empty.textContent = videos.length ? 'Выберите видео' : 'Сначала добавьте видео в карточку контента';
  select.append(empty);
  for (const video of videos) {
    const option = document.createElement('option'); option.value = video.id;
    option.textContent = `${video.content_title} · ${video.name}`; select.append(option);
  }
}

async function loadAIStudio() {
  if (!state.currentProjectId) return;
  const [config, library] = await Promise.all([
    api('/api/ai/config'), api(`/api/projects/${state.currentProjectId}/library`)
  ]);
  state.aiConfig = config; state.libraryItems = library; renderAIVideoOptions();
  const badge = $('#ai-provider-status');
  badge.textContent = config.enabled ? `OpenAI подключён · ${config.text_model}` : 'AI не настроен на сервере';
  badge.classList.toggle('ready', config.enabled);
  document.querySelectorAll('#ai-text-form button[type=submit],#ai-image-form button[type=submit],#ai-clips-form button[type=submit]').forEach((button) => {
    button.disabled = !config.enabled;
  });
}

function localDateTimeValue(value) {
  if (!value) return '';
  const date = new Date(value); const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function populateContentOptions(item = null) {
  const stages = $('#content-stage-input'); stages.replaceChildren();
  const none = document.createElement('option'); none.value = ''; none.textContent = 'Без этапа'; stages.append(none);
  for (const stage of state.approvalWorkflow?.stages || []) {
    const option = document.createElement('option'); option.value = stage.id;
    option.textContent = stage.name; stages.append(option);
  }
  stages.value = item?.stage?.id || '';
  const assignees = $('#content-assignee-input'); assignees.replaceChildren();
  const unassigned = document.createElement('option'); unassigned.value = ''; unassigned.textContent = 'Не назначен'; assignees.append(unassigned);
  for (const member of state.workspaceMembers) {
    const option = document.createElement('option'); option.value = member.user_id;
    option.textContent = member.display_name || member.email; assignees.append(option);
  }
  assignees.value = item?.assignee?.id || '';
}

function renderAttachments(attachments = []) {
  const container = $('#content-attachments'); container.replaceChildren();
  if (!attachments.length) {
    const empty = document.createElement('small'); empty.textContent = 'Файлов пока нет.'; container.append(empty); return;
  }
  for (const attachment of attachments) {
    const row = document.createElement('div'); row.className = 'attachment-row';
    const identity = document.createElement('div');
    const name = document.createElement('strong'); name.textContent = attachment.name;
    const size = document.createElement('small'); size.textContent = humanFileSize(attachment.size_bytes);
    identity.append(name, size);
    const link = document.createElement('a'); link.className = 'ghost'; link.href = attachment.download_url; link.textContent = 'Скачать';
    const remove = document.createElement('button'); remove.className = 'danger'; remove.type = 'button';
    remove.textContent = 'Удалить'; remove.disabled = !canEditContent();
    remove.addEventListener('click', async () => {
      try {
        await api(`/api/content-attachments/${attachment.id}`, { method: 'DELETE' });
        await refreshOpenContent(); showToast('Файл удалён.');
      } catch (error) { showWorkspaceError(error); }
    });
    row.append(identity, link, remove); container.append(row);
  }
}

async function renderRevisions(itemId) {
  const revisions = await api(`/api/content/${itemId}/revisions`);
  const container = $('#content-revisions'); container.replaceChildren();
  for (const revision of revisions) {
    const row = document.createElement('div'); row.className = 'revision-row';
    const label = document.createElement('span'); label.textContent = `Версия ${revision.version} · ${revision.author}`;
    const date = document.createElement('time'); date.textContent = contentDate(revision.created_at, true);
    row.append(label, date); container.append(row);
  }
}

function setContentFormEditable(editable) {
  $('#content-form').querySelectorAll('input:not([type=hidden]),select,textarea').forEach((control) => {
    if (control.id !== 'content-file-input') control.disabled = !editable;
  });
  $('#content-form').querySelector('button[type=submit]').disabled = !editable;
  $('#archive-content-button').disabled = !editable;
  $('#content-file-input').disabled = !editable;
}

async function openContentEditor(itemId = null, defaultType = 'post') {
  if (!state.currentProjectId) return;
  if (!state.approvalWorkflow) await loadApprovalWorkflow();
  state.editingContentId = itemId;
  const item = itemId ? await api(`/api/content/${itemId}`) : null;
  $('#content-form').reset(); $('#content-id').value = itemId || '';
  $('#content-dialog-title').textContent = item ? item.title : 'Новый материал';
  $('#content-title-input').value = item?.title || '';
  $('#content-type-input').value = item?.item_type || defaultType;
  $('#content-channel-input').value = item?.channel || '';
  $('#content-planned-input').value = localDateTimeValue(item?.planned_at);
  $('#content-priority-input').value = item?.priority || 'normal';
  $('#content-tags-input').value = (item?.tags || []).join(', ');
  $('#content-body-input').value = item?.body || '';
  $('#content-body-preview').classList.add('hidden'); $('#content-body-input').classList.remove('hidden');
  populateContentOptions(item); renderAttachments(item?.attachments || []);
  $('#content-files-section').classList.toggle('hidden', !item);
  $('#content-history-section').classList.toggle('hidden', !item);
  $('#archive-content-button').classList.toggle('hidden', !item);
  $('#content-form-status').textContent = '';
  setContentFormEditable(canEditContent());
  if (item) renderRevisions(item.id).catch(() => {});
  $('#content-dialog').showModal();
}

async function refreshOpenContent() {
  if (!state.editingContentId) return;
  const item = await api(`/api/content/${state.editingContentId}`);
  renderAttachments(item.attachments || []); renderRevisions(item.id).catch(() => {});
}

function contentFormPayload() {
  const planned = $('#content-planned-input').value;
  return {
    title: $('#content-title-input').value.trim(), item_type: $('#content-type-input').value,
    stage_id: $('#content-stage-input').value || null,
    channel: $('#content-channel-input').value.trim() || null,
    planned_at: planned ? new Date(planned).toISOString() : null,
    assignee_user_id: $('#content-assignee-input').value || null,
    priority: $('#content-priority-input').value,
    tags: $('#content-tags-input').value.split(',').map((tag) => tag.trim()).filter(Boolean),
    body: $('#content-body-input').value
  };
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

  const entitlement = $('#billing-entitlement');
  entitlement.classList.toggle('expired', summary.subscription_status === 'expired');
  if (summary.subscription_status === 'active') {
    entitlement.textContent = summary.current_period_end ? `Подписка активна до ${new Date(summary.current_period_end).toLocaleDateString('ru-RU')}.` : 'Подписка активна.';
  } else if (summary.subscription_status === 'expired') {
    entitlement.textContent = 'Пробный период завершён. Материалы доступны для просмотра, но новые операции требуют подписку.';
  } else {
    entitlement.textContent = summary.trial_expires_at ? `Пробный период до ${new Date(summary.trial_expires_at).toLocaleDateString('ru-RU')}.` : 'Пробный период активен.';
  }
  const limitLabels = { workspaces: 'Пространства', projects: 'Проекты', members: 'Участники', storage_mb: 'Хранилище, МБ', active_jobs: 'Задания в очереди' };
  const limitContainer = $('#billing-limits'); limitContainer.replaceChildren();
  for (const key of Object.keys(limitLabels)) {
    const card = document.createElement('div'); card.className = 'billing-limit';
    const label = document.createElement('span'); label.textContent = limitLabels[key];
    const value = document.createElement('b'); value.textContent = `${summary.usage?.[key] ?? 0} / ${summary.limits?.[key] ?? '∞'}`;
    card.append(label, value); limitContainer.append(card);
  }

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
    const limitLine = document.createElement('small');
    const planLimits = plan.limits || {};
    limitLine.textContent = `${planLimits.projects || '∞'} проектов · ${planLimits.members || '∞'} участников · ${planLimits.storage_mb || '∞'} МБ`;
    footer.append(credits, price); card.append(title, description, limitLine, footer);
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
  window.AAPAppMotion?.authEntered();
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
    const created = await api('/api/sources/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_url: $('#channel-url').value,
        platform: $('#source-platform').value,
        limit: Number($('#shorts-limit').value),
        project_id: state.currentProjectId
      })
    });
    state.importId = created.id;
    localStorage.setItem('ytLoaderImportJob', created.id);
    const job = await pollJob(created.id, (current) => { status.textContent = current.message || current.status; });
    const items = await api(job.items_url); renderItems(items, created.id);
    $('#csv-link').href = job.csv_url; status.textContent = `Готово: найдено ${items.length} видео`;
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
    const image = document.createElement('img'); image.className = 'thumb'; image.loading = 'lazy'; image.alt = ''; image.src = sourceThumbnailUrl(item.thumbnail || '');
    const info = document.createElement('div');
    const title = document.createElement('h3'); title.textContent = item.title;
    const meta = document.createElement('div'); meta.className = 'meta';
    meta.textContent = [item.platform?.toUpperCase(), item.uploader, item.upload_date].filter(Boolean).join(' · ');
    const description = document.createElement('p'); description.className = 'description'; description.textContent = item.description || 'Описание отсутствует';
    const tags = document.createElement('div'); tags.className = 'tags'; tags.textContent = item.tags.length ? item.tags.map((tag) => `#${tag}`).join(' ') : 'Теги отсутствуют';
    info.append(title, meta, description, tags);
    const actions = document.createElement('div'); actions.className = 'actions';
    const videoButton = document.createElement('button'); videoButton.className = 'primary'; videoButton.textContent = 'Подготовить видео';
    const saveContent = document.createElement('button'); saveContent.className = 'ghost';
    saveContent.type = 'button'; saveContent.textContent = 'В контент-план';
    saveContent.addEventListener('click', async () => {
      saveContent.disabled = true;
      try {
        await api(`/api/projects/${state.currentProjectId}/content`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: item.title, item_type: 'video', body: item.description || '',
            channel: item.platform || null, tags: item.tags || [],
            source_platform: item.platform, source_id: item.id, source_url: item.url
          })
        });
        saveContent.textContent = 'Добавлено'; showToast('Видео сохранено в контент-плане.');
      } catch (error) {
        if (error.status === 409) saveContent.textContent = 'Уже добавлено';
        else { saveContent.disabled = false; showWorkspaceError(error); }
      }
    });
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
    actions.append(videoButton, saveContent, metadata, note); card.append(selector, image, info, actions); container.append(card);
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
  return {
    url, logo_tokens: logoTokens, project_id: state.currentProjectId,
    ...(settings || currentDownloadSettings())
  };
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
  try { await showExternalSourcePreview($('#direct-video-url').value); } catch (_) {}
  await startDownloadUrl($('#direct-video-url').value, workButton, note);
  submitButton.disabled = false;
});

$('#direct-video-url').addEventListener('change', (event) => {
  showExternalSourcePreview(event.target.value).catch((error) => showToast(error.message));
});

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
    $('#csv-link').href = job.csv_url; status.textContent = `Готово: найдено ${items.length} видео`;
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

document.addEventListener('click', (event) => {
  const navigationButton = event.target.closest('[data-navigate]');
  if (!navigationButton || !state.currentUser) return;
  event.preventDefault();
  showWorkspacePage(navigationButton.dataset.navigate, true);
});

window.addEventListener('popstate', () => {
  const page = workspacePageFromHash();
  if (page && state.currentUser) showWorkspacePage(page);
});

window.addEventListener('hashchange', () => {
  const page = workspacePageFromHash();
  if (page && state.currentUser) showWorkspacePage(page);
});

$('#workspace-select').addEventListener('change', (event) => {
  activateWorkspace(event.target.value).catch(showWorkspaceError);
});

$('#dismiss-onboarding').addEventListener('click', () => {
  localStorage.setItem(onboardingStorageKey, 'dismissed');
  $('#onboarding-panel').classList.add('hidden');
});

$('#refresh-admin').addEventListener('click', () => {
  state.adminLoaded = false;
  loadAdmin(true).then(() => showToast('Данные панели обновлены.')).catch(showWorkspaceError);
});

$('#create-workspace-button').addEventListener('click', () => {
  $('#workspace-form').reset(); $('#workspace-dialog-status').textContent = '';
  $('#workspace-dialog').showModal();
});

$('#create-project-button').addEventListener('click', () => {
  $('#project-form').reset(); $('#project-color-input').value = '#7c6cff';
  $('#project-dialog-status').textContent = ''; $('#project-dialog').showModal();
});

document.querySelectorAll('.workspace-dialog .dialog-cancel').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog').close());
});

$('#workspace-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const status = $('#workspace-dialog-status');
  try {
    const workspace = await api('/api/workspaces', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('#workspace-name-input').value.trim() })
    });
    $('#workspace-dialog').close(); await loadWorkspaces(workspace.id);
    showToast('Рабочее пространство создано.');
  } catch (error) { status.className = 'auth-status error'; status.textContent = error.message; }
});

$('#project-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const status = $('#project-dialog-status');
  if (!state.currentWorkspaceId) return;
  try {
    const project = await api(`/api/workspaces/${state.currentWorkspaceId}/projects`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: $('#project-name-input').value.trim(),
        description: $('#project-description-input').value.trim() || null,
        color: $('#project-color-input').value
      })
    });
    $('#project-dialog').close(); state.projects.push(project); selectProject(project.id);
    showToast('Проект создан.');
  } catch (error) { status.className = 'auth-status error'; status.textContent = error.message; }
});

$('#add-member-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const member = await api(`/api/workspaces/${state.currentWorkspaceId}/members`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: $('#member-email').value.trim(), role: $('#member-role').value })
    });
    state.workspaceMembers.push(member); renderWorkspaceMembers(); event.target.reset();
    showToast('Участник добавлен в команду.');
  } catch (error) { showWorkspaceError(error); }
  finally { button.disabled = false; }
});

$('#add-approval-stage').addEventListener('click', () => {
  $('#approval-stages').append(approvalStageRow({ name: 'Новый этап' }));
});

$('#save-workflow-button').addEventListener('click', async (event) => {
  const status = $('#workflow-status'); event.currentTarget.disabled = true;
  status.className = 'status'; status.textContent = 'Сохраняю процесс…';
  try {
    const payload = workflowPayload();
    if (!payload.name || payload.stages.some((stage) => !stage.name)) throw new Error('Заполните названия процесса и всех этапов.');
    state.approvalWorkflow = await api(`/api/projects/${state.currentProjectId}/approval-workflow`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    });
    status.textContent = 'Процесс согласования сохранён.';
    await loadApprovalWorkflow();
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
  finally { event.currentTarget.disabled = false; }
});

$('#create-content-button').addEventListener('click', () => openContentEditor().catch(showWorkspaceError));
$('#create-document-button').addEventListener('click', () => openContentEditor(null, 'document').catch(showWorkspaceError));
$('#refresh-library-button').addEventListener('click', () => loadLibrary().catch(showWorkspaceError));

$('#content-search').addEventListener('input', renderContent);
$('#content-type-filter').addEventListener('change', renderContent);
document.querySelectorAll('[data-content-view]').forEach((button) => {
  button.addEventListener('click', () => {
    state.contentView = button.dataset.contentView;
    document.querySelectorAll('[data-content-view]').forEach((item) => {
      item.classList.toggle('active', item === button);
    });
    renderContent();
  });
});

$('#content-dialog-close').addEventListener('click', () => $('#content-dialog').close());
$('#content-dialog-cancel').addEventListener('click', () => $('#content-dialog').close());

document.querySelectorAll('[data-markdown]').forEach((button) => {
  button.addEventListener('click', () => {
    const editor = $('#content-body-input'); const template = button.dataset.markdown;
    const separator = template.indexOf('|');
    const before = template.slice(0, separator); const after = template.slice(separator + 1);
    const start = editor.selectionStart; const end = editor.selectionEnd;
    const selection = editor.value.slice(start, end);
    editor.setRangeText(`${before}${selection}${after}`, start, end, 'end'); editor.focus();
  });
});

$('#toggle-content-preview').addEventListener('click', () => {
  const editor = $('#content-body-input'); const preview = $('#content-body-preview');
  const showing = !preview.classList.contains('hidden');
  if (!showing) preview.textContent = editor.value || 'Предпросмотр пустого документа.';
  editor.classList.toggle('hidden', !showing); preview.classList.toggle('hidden', showing);
  $('#toggle-content-preview').textContent = showing ? 'Предпросмотр' : 'Редактор';
});

$('#content-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const status = $('#content-form-status');
  const submit = event.submitter; submit.disabled = true;
  status.className = 'auth-status'; status.textContent = 'Сохраняю материал…';
  try {
    const id = $('#content-id').value; const payload = contentFormPayload();
    const saved = await api(id ? `/api/content/${id}` : `/api/projects/${state.currentProjectId}/content`, {
      method: id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    state.editingContentId = saved.id; $('#content-dialog').close();
    await loadContent(); showToast(id ? 'Материал обновлён.' : 'Материал добавлен в контент-план.');
  } catch (error) {
    status.className = 'auth-status error'; status.textContent = error.message;
  } finally { submit.disabled = false; }
});

$('#archive-content-button').addEventListener('click', async () => {
  const id = $('#content-id').value;
  if (!id || !confirm('Переместить материал в архив?')) return;
  try {
    await api(`/api/content/${id}`, { method: 'DELETE' });
    $('#content-dialog').close(); await loadContent(); showToast('Материал перемещён в архив.');
  } catch (error) { showWorkspaceError(error); }
});

$('#content-file-input').addEventListener('change', async (event) => {
  const file = event.target.files[0]; const itemId = $('#content-id').value;
  if (!file || !itemId) return;
  const form = new FormData(); form.append('file', file);
  event.target.disabled = true;
  try {
    await api(`/api/content/${itemId}/attachments`, { method: 'POST', body: form });
    await refreshOpenContent(); await loadLibrary(); showToast('Файл прикреплён к материалу.');
  } catch (error) { showWorkspaceError(error); }
  finally { event.target.value = ''; event.target.disabled = !canEditContent(); }
});

$('#ai-text-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  setAIStatus('#ai-text-status', 'Ставлю генерацию в очередь…');
  $('#ai-text-result').classList.add('hidden');
  try {
    const job = await api('/api/ai/text', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: state.currentProjectId, action: $('#ai-text-action').value,
        prompt: $('#ai-text-prompt').value.trim(), context: $('#ai-text-context').value.trim() || null
      })
    });
    const done = await pollJob(job.id, (current) => setAIStatus('#ai-text-status', current.message || 'Генерирую…'));
    $('#ai-text-result textarea').value = done.result.text;
    $('#ai-text-result').classList.remove('hidden'); setAIStatus('#ai-text-status', 'Текст готов.');
  } catch (error) { setAIStatus('#ai-text-status', error.message, true); }
  finally { button.disabled = !state.aiConfig?.enabled; }
});

$('#ai-copy-text').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('#ai-text-result textarea').value);
  showToast('Текст скопирован.');
});

$('#ai-image-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  setAIStatus('#ai-image-status', 'Ставлю изображение в очередь…');
  $('#ai-image-result').classList.add('hidden');
  try {
    const job = await api('/api/ai/images', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: state.currentProjectId, prompt: $('#ai-image-prompt').value.trim(), size: $('#ai-image-size').value })
    });
    const done = await pollJob(job.id, (current) => setAIStatus('#ai-image-status', current.message || 'Генерирую…'));
    const url = await downloadableJobUrl(done);
    $('#ai-image-result img').src = url; $('#ai-image-result a').href = url;
    $('#ai-image-result').classList.remove('hidden'); setAIStatus('#ai-image-status', 'Изображение готово.');
  } catch (error) { setAIStatus('#ai-image-status', error.message, true); }
  finally { button.disabled = !state.aiConfig?.enabled; }
});

$('#ai-clips-form').addEventListener('submit', async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  setAIStatus('#ai-clips-status', 'Ставлю обработку в очередь…');
  $('#ai-clips-download').classList.add('hidden');
  try {
    const job = await api('/api/ai/clips', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: state.currentProjectId, attachment_id: $('#ai-video-attachment').value,
        count: Number($('#ai-clip-count').value), min_seconds: Number($('#ai-clip-min').value),
        max_seconds: Number($('#ai-clip-max').value)
      })
    });
    const done = await pollJob(job.id, (current) => setAIStatus('#ai-clips-status', current.message || 'Обрабатываю…'));
    const url = await downloadableJobUrl(done); const link = $('#ai-clips-download');
    link.href = url; link.classList.remove('hidden');
    setAIStatus('#ai-clips-status', `Готово клипов: ${done.result.count}.`);
  } catch (error) { setAIStatus('#ai-clips-status', error.message, true); }
  finally { button.disabled = !state.aiConfig?.enabled; }
});

bootstrapAuth();
