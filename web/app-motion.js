(() => {
  const root = document.documentElement;
  const appShell = document.querySelector('#app-shell');
  const authScreen = document.querySelector('#auth-screen');
  const reduced = () => window.AAPMotion?.reduced?.() ?? window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const activeRequests = new Set();
  let requestSequence = 0;
  let networkHideTimer = 0;

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

  function pageEntered(page, pageName, title) {
    if (!page) return;
    prepareSurface(page);
    if (reduced()) return;
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
    if (reduced()) return;
    replayClass(appShell, 'app-shell-enter', 900);
  }

  function authEntered() {
    if (reduced()) return;
    replayClass(authScreen, 'app-auth-enter', 720);
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
  });

  window.AAPAppMotion = Object.freeze({
    appEntered, authEntered, networkEnd, networkStart, pageEntered, toastIn, toastOut,
  });
})();
