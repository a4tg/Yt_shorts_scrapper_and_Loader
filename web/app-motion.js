(() => {
  const root = document.documentElement;
  const appShell = document.querySelector('#app-shell');
  const authScreen = document.querySelector('#auth-screen');
  const reduced = () => window.AAPMotion?.reduced?.() ?? window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const activeRequests = new Set();
  let requestSequence = 0;
  let networkHideTimer = 0;
  let navigationFrame = 0;

  const networkBar = document.createElement('div');
  networkBar.className = 'app-network-progress';
  networkBar.setAttribute('role', 'progressbar');
  networkBar.setAttribute('aria-label', 'Загрузка данных');
  networkBar.setAttribute('aria-valuetext', 'Выполняется запрос');
  networkBar.setAttribute('aria-hidden', 'true');
  networkBar.innerHTML = '<i></i>';
  document.body.append(networkBar);

  const dynamicItemSelector = [
    '.dashboard-card', '.project-card', '.member-row', '.content-stat', '.content-column',
    '.content-card', '.calendar-day', '.document-card', '.library-card', '.approval-stage-row',
    '.plan-card', '.ledger-row', '.admin-stat', '.onboarding-step', '.item', '.attachment-row',
    '.revision-row', '.ai-result', '.billing-limit', '.credit-summary > div', 'tbody > tr',
    '.empty-brand-state',
  ].join(',');

  const surfaceSelector = [
    '.dashboard-card', '.project-card', '.content-card', '.document-card', '.library-card',
    '.plan-card', '.admin-stat', '.onboarding-step', '.ai-card', '.item', '.billing-limit',
  ].join(',');

  function foregroundRequest(method, url) {
    if (method === 'GET' && (/\/api\/jobs\//.test(url) || /\/api\/health/.test(url))) return false;
    if (/\/api\/auth\/config/.test(url)) return false;
    return true;
  }

  function networkStart(method, url) {
    if (!foregroundRequest(method, String(url))) return null;
    const token = `request-${++requestSequence}`;
    activeRequests.add(token);
    clearTimeout(networkHideTimer);
    networkBar.classList.remove('is-complete');
    networkBar.classList.add('is-active');
    networkBar.setAttribute('aria-hidden', 'false');
    appShell?.setAttribute('aria-busy', 'true');
    return token;
  }

  function networkEnd(token) {
    if (!token || !activeRequests.delete(token)) return;
    if (activeRequests.size) return;
    networkBar.classList.add('is-complete');
    appShell?.removeAttribute('aria-busy');
    networkHideTimer = window.setTimeout(() => {
      if (activeRequests.size) return;
      networkBar.classList.remove('is-active', 'is-complete');
      networkBar.setAttribute('aria-hidden', 'true');
    }, reduced() ? 0 : 360);
  }

  function replayClass(element, className, duration = 700) {
    if (!element || reduced()) return;
    element.classList.remove(className);
    requestAnimationFrame(() => {
      element.classList.add(className);
      window.setTimeout(() => element.classList.remove(className), duration);
    });
  }

  function syncNavigationIndicator() {
    if (navigationFrame) cancelAnimationFrame(navigationFrame);
    navigationFrame = requestAnimationFrame(() => {
      navigationFrame = 0;
      const sidebar = document.querySelector('.workspace-sidebar');
      const active = sidebar?.querySelector('.workspace-nav-item.active:not(.hidden)');
      if (!sidebar || !active || appShell?.classList.contains('hidden')) {
        sidebar?.style.setProperty('--nav-indicator-opacity', '0');
        return;
      }
      const sidebarRect = sidebar.getBoundingClientRect();
      const activeRect = active.getBoundingClientRect();
      sidebar.style.setProperty('--nav-indicator-x', `${activeRect.left - sidebarRect.left}px`);
      sidebar.style.setProperty('--nav-indicator-y', `${activeRect.top - sidebarRect.top}px`);
      sidebar.style.setProperty('--nav-indicator-width', `${activeRect.width}px`);
      sidebar.style.setProperty('--nav-indicator-height', `${activeRect.height}px`);
      sidebar.style.setProperty('--nav-indicator-opacity', '1');
    });
  }

  function captureLayout(container) {
    if (!container || reduced()) return new Map();
    return new Map([...container.children].map((element) => [element, element.getBoundingClientRect()]));
  }

  function animateLayout(previousLayout) {
    if (reduced()) return;
    previousLayout.forEach((previous, element) => {
      if (!element.isConnected || typeof element.animate !== 'function') return;
      const current = element.getBoundingClientRect();
      const x = previous.left - current.left;
      const y = previous.top - current.top;
      if (Math.abs(x) < 1 && Math.abs(y) < 1) return;
      element.animate([
        { transform: `translate(${x}px, ${y}px)`, zIndex: 2 },
        { transform: 'translate(0, 0)', zIndex: 2 },
      ], { duration: 360, easing: 'cubic-bezier(.2,.75,.25,1)' });
    });
  }

  function contentCardMoved(card, previousRect, columns = []) {
    if (!card) return;
    card.classList.remove('dragging');
    card.classList.add('card-saving');
    columns.filter(Boolean).forEach((column) => replayClass(column, 'count-updated', 480));
    if (!reduced() && previousRect && typeof card.animate === 'function') {
      const current = card.getBoundingClientRect();
      card.animate([
        { transform: `translate(${previousRect.left - current.left}px, ${previousRect.top - current.top}px) scale(.975)`, boxShadow: '0 26px 55px rgba(0,0,0,.38)' },
        { transform: 'translate(0, 0) scale(1)', boxShadow: '0 8px 20px rgba(0,0,0,.12)' },
      ], { duration: 520, easing: 'cubic-bezier(.16,1,.3,1)' });
    }
  }

  function contentCardSaved(card) {
    if (!card) return;
    card.classList.remove('card-saving');
    replayClass(card, 'card-settling', 560);
  }

  function clearContentDragState() {
    document.documentElement.classList.remove('content-is-dragging');
    document.querySelectorAll('.content-column').forEach((column) => {
      column.classList.remove('drop-ready', 'drop-target');
    });
  }

  function dialogFromSource(dialog, sourceRect) {
    if (!dialog || !sourceRect || reduced() || typeof dialog.animate !== 'function') return;
    const current = dialog.getBoundingClientRect();
    const x = sourceRect.left + sourceRect.width / 2 - (current.left + current.width / 2);
    const y = sourceRect.top + sourceRect.height / 2 - (current.top + current.height / 2);
    dialog.style.setProperty('--dialog-origin-x', `${sourceRect.left + sourceRect.width / 2 - current.left}px`);
    dialog.style.setProperty('--dialog-origin-y', `${sourceRect.top + sourceRect.height / 2 - current.top}px`);
    dialog.animate([
      { opacity: .24, transform: `translate(${x}px, ${y}px) scale(.72)`, filter: 'blur(4px)' },
      { opacity: 1, transform: 'translate(0, 0) scale(1)', filter: 'blur(0)' },
    ], { duration: 520, easing: 'cubic-bezier(.16,1,.3,1)' });
  }

  function transitionContentView(update) {
    if (reduced() || typeof document.startViewTransition !== 'function') {
      update();
      return null;
    }
    root.dataset.contentTransition = 'true';
    const transition = document.startViewTransition(update);
    transition.finished.finally(() => delete root.dataset.contentTransition);
    return transition;
  }

  function videoPhase(phase) {
    const page = document.querySelector('.workspace-page[data-page="video"]');
    if (!page) return;
    const panels = [
      page.querySelector('.import-panel'),
      page.querySelector('.settings-panel'),
      page.querySelector('.results-panel'),
    ];
    const phaseIndex = { idle: 0, importing: 0, editing: 1, results: 2, processing: 2, ready: 2, error: 2 }[phase] ?? 0;
    page.dataset.videoPhase = phase;
    panels.forEach((panel, index) => {
      panel?.classList.toggle('video-step-current', index === phaseIndex);
      panel?.classList.toggle('video-step-complete', index < phaseIndex || (phase === 'ready' && index === phaseIndex));
    });
    replayClass(panels[phaseIndex], 'video-phase-updated', 620);
  }

  function videoPreviewUpdated(stage) {
    if (!stage) return;
    stage.classList.add('has-source');
    replayClass(stage, 'stage-media-enter', 680);
    videoPhase('editing');
  }

  function ensureJobProgress(note) {
    const host = note?.closest('.item') || note?.closest('.direct-result');
    if (!host) return null;
    let progress = host.querySelector('.item-job-progress');
    if (progress) return progress;
    progress = document.createElement('div');
    progress.className = 'item-job-progress hidden';
    progress.setAttribute('role', 'status');
    const label = document.createElement('span');
    const state = document.createElement('b');
    const track = document.createElement('i'); track.setAttribute('aria-hidden', 'true');
    track.append(document.createElement('span'));
    progress.append(label, state, track); host.append(progress);
    return progress;
  }

  function videoJobUpdated(note, job) {
    const progress = ensureJobProgress(note);
    if (!progress) return;
    const status = job?.status || 'queued';
    const values = { queued: 16, running: 64, done: 100, error: 100, deleted: 100 };
    const labels = { queued: 'В очереди', running: 'Обработка', done: 'Готово', error: 'Ошибка', deleted: 'Удалено' };
    progress.classList.remove('hidden', 'is-ready', 'is-error');
    progress.classList.toggle('is-running', ['queued', 'running'].includes(status));
    progress.classList.toggle('is-ready', status === 'done');
    progress.classList.toggle('is-error', ['error', 'deleted'].includes(status));
    progress.style.setProperty('--job-progress', `${values[status] ?? 16}%`);
    progress.querySelector('span').textContent = job?.message || labels[status] || status;
    progress.querySelector('b').textContent = labels[status] || status;
  }

  function batchProgress(current, total, label, tone = 'running') {
    const progress = document.querySelector('#batch-progress');
    if (!progress) return;
    const safeTotal = Math.max(1, Number(total) || 1);
    const value = Math.min(100, Math.max(0, Math.round(Number(current) / safeTotal * 100)));
    progress.classList.remove('hidden', 'is-complete', 'is-error');
    progress.classList.toggle('is-complete', tone === 'complete');
    progress.classList.toggle('is-error', tone === 'error');
    progress.style.setProperty('--batch-progress', `${value}%`);
    progress.setAttribute('aria-valuenow', String(value));
    const labelElement = progress.querySelector('#batch-progress-label');
    const countElement = progress.querySelector('#batch-progress-count');
    if (labelElement) labelElement.textContent = label;
    if (countElement) countElement.textContent = `${Math.min(Number(current) || 0, safeTotal)} / ${safeTotal}`;
  }

  function prepareSurface(element) {
    if (!(element instanceof Element)) return;
    const surfaces = element.matches(surfaceSelector)
      ? [element]
      : [...element.querySelectorAll(surfaceSelector)];
    surfaces.forEach((surface) => surface.classList.add('app-motion-surface'));
    window.AAPMotion?.registerSurfaces?.(element.matches('*') ? element : document);
    window.AAPMotion?.registerAnimatedRegions?.(element);
  }

  function animateDynamicNodes(nodes) {
    if (reduced()) {
      nodes.forEach(prepareSurface);
      return;
    }
    let order = 0;
    nodes.forEach((node) => {
      if (!(node instanceof Element)) return;
      prepareSurface(node);
      const items = node.matches(dynamicItemSelector)
        ? [node]
        : [...node.querySelectorAll(dynamicItemSelector)];
      items.forEach((item) => {
        if (item.classList.contains('app-item-enter')) return;
        item.style.setProperty('--app-enter-delay', `${Math.min(order * 38, 230)}ms`);
        item.classList.add('app-item-enter');
        item.addEventListener('animationend', () => item.classList.remove('app-item-enter'), { once: true });
        order += 1;
      });
    });
  }

  function pageEntered(page, pageName, title, options = {}) {
    if (!page) return;
    prepareSurface(page);
    syncNavigationIndicator();
    if (reduced() || options.nativeTransition) return;
    replayClass(page, 'app-page-enter', 620);
    page.dataset.motionPage = pageName;
    const sections = [...page.children].filter((child) => !child.classList.contains('story-chapter-ambient'));
    sections.forEach((section, index) => {
      section.style.setProperty('--app-section-delay', `${Math.min(index * 65, 195)}ms`);
      replayClass(section, 'app-section-enter', 720);
    });
    replayClass(title, 'app-title-enter', 520);
  }

  function appEntered() {
    root.classList.add('app-motion-active');
    syncNavigationIndicator();
    if (reduced()) return;
    replayClass(appShell, 'app-shell-enter', 900);
  }

  function authEntered() {
    if (reduced()) return;
    replayClass(authScreen, 'app-auth-enter', 720);
  }

  function contextUpdated(context) {
    replayClass(context, 'context-updated', 520);
  }

  function toastIn(toast, tone = 'neutral') {
    toast.dataset.tone = tone;
    toast.classList.remove('app-toast-leave', 'app-toast-enter');
    void toast.offsetWidth;
    toast.classList.add('app-toast-enter');
    return true;
  }

  function toastOut(toast) {
    if (!toast || toast.classList.contains('hidden')) return false;
    if (reduced()) {
      toast.classList.add('hidden');
      return true;
    }
    toast.classList.remove('app-toast-enter');
    toast.classList.add('app-toast-leave');
    window.setTimeout(() => {
      toast.classList.add('hidden');
      toast.classList.remove('app-toast-leave');
    }, 220);
    return true;
  }

  function animateStatus(element) {
    if (!element || element.classList.contains('hidden') || element.classList.contains('app-status-update')) return;
    replayClass(element, 'app-status-update', 520);
    if (element.classList.contains('error')) window.AAPMotion?.feedback?.(element, 'error');
    if (element.classList.contains('success')) window.AAPMotion?.feedback?.(element, 'success');
  }

  const observer = new MutationObserver((records) => {
    const added = [];
    records.forEach((record) => {
      if (record.type === 'childList') {
        added.push(...record.addedNodes);
        const host = record.target instanceof Element ? record.target.closest('.status, .auth-status, .job-note, .ai-provider-badge') : null;
        if (host) animateStatus(host);
        const value = record.target instanceof Element ? record.target.closest('.credit-summary strong, .content-stat strong, .admin-stat strong, .credit-badge') : null;
        if (value) replayClass(value, 'app-value-update', 520);
      }
      if (record.type === 'attributes' && record.attributeName === 'class') {
        const element = record.target;
        const wasHidden = String(record.oldValue || '').split(/\s+/).includes('hidden');
        if (wasHidden && element.matches('.status, .auth-status, .ai-result, .verification-banner') && !element.classList.contains('hidden')) animateStatus(element);
        if (wasHidden && element.matches('.auth-card') && !element.classList.contains('hidden')) replayClass(element, 'app-card-enter', 620);
      }
    });
    if (added.length) animateDynamicNodes(added);
  });

  prepareSurface(document.body);
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'], attributeOldValue: true });

  requestAnimationFrame(() => {
    if (!authScreen?.classList.contains('hidden')) authEntered();
    syncNavigationIndicator();
  });
  window.addEventListener('resize', syncNavigationIndicator, { passive: true });

  window.AAPAppMotion = Object.freeze({
    animateLayout, appEntered, authEntered, captureLayout, clearContentDragState,
    contentCardMoved, contentCardSaved, contextUpdated, dialogFromSource, networkEnd,
    networkStart, pageEntered, syncNavigationIndicator, toastIn, toastOut,
    transitionContentView, batchProgress, videoJobUpdated, videoPhase, videoPreviewUpdated,
  });
})();
