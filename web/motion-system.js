(() => {
  const root = document.documentElement;
  const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointerQuery = window.matchMedia('(pointer: fine)');
  let pointerFrame = 0;
  let regionObserver = null;
  const animatedRegionSelector = [
    '.hero-stage', '.product-demo', '.video-feature', '.value-flow', '.final-cta-visual',
    '.dashboard-orbit', '.audience-section', '.auth-brand-scene', '[data-motion-region]',
  ].join(',');

  function reduced() {
    return reducedMotionQuery.matches;
  }

  function syncPreference() {
    const isReduced = reduced();
    root.dataset.motion = isReduced ? 'reduced' : 'full';
    root.classList.toggle('motion-ready', !isReduced);
    if (!isReduced) requestAnimationFrame(() => registerAnimatedRegions());
  }

  function reveal(elements, options = {}) {
    const items = [...elements].filter(Boolean);
    if (!items.length) return null;
    const stagger = Number(options.stagger ?? 80);
    const maxStagger = Number(options.maxStagger ?? 320);

    items.forEach((element, index) => {
      element.classList.add('reveal-item');
      element.style.setProperty('--reveal-delay', `${Math.min(index * stagger, maxStagger)}ms`);
    });

    if (reduced() || !('IntersectionObserver' in window)) {
      items.forEach((element) => element.classList.add('is-visible'));
      return null;
    }

    const observer = new IntersectionObserver((entries, activeObserver) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        activeObserver.unobserve(entry.target);
      });
    }, {
      threshold: Number(options.threshold ?? .12),
      rootMargin: options.rootMargin ?? '0px 0px -42px',
    });

    items.forEach((element) => observer.observe(element));
    return observer;
  }

  function feedback(element, type = 'success') {
    if (!element || reduced()) return;
    const className = type === 'error' ? 'motion-feedback-error' : 'motion-feedback-success';
    element.classList.remove(className);
    requestAnimationFrame(() => {
      element.classList.add(className);
      element.addEventListener('animationend', () => element.classList.remove(className), { once: true });
    });
  }

  function registerSurfaces(scope = document) {
    scope.querySelectorAll('.dashboard-card, .project-card, .library-card, .document-card, .auth-card').forEach((element) => {
      element.classList.add('motion-surface');
    });
  }

  function registerAnimatedRegions(scope = document) {
    if (reduced() || !('IntersectionObserver' in window)) return null;
    if (!regionObserver) {
      regionObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => entry.target.classList.toggle('motion-offscreen', !entry.isIntersecting));
      }, { rootMargin: '180px 0px', threshold: 0 });
    }
    const regions = [];
    if (scope instanceof Element && scope.matches(animatedRegionSelector)) regions.push(scope);
    regions.push(...scope.querySelectorAll(animatedRegionSelector));
    regions.forEach((region) => {
      if (region.dataset.motionRegionObserved) return;
      region.dataset.motionRegionObserved = 'true';
      regionObserver.observe(region);
    });
    return regionObserver;
  }

  function updatePointer(event) {
    if (pointerFrame || reduced() || !finePointerQuery.matches) return;
    pointerFrame = requestAnimationFrame(() => {
      root.style.setProperty('--motion-pointer-x', `${event.clientX}px`);
      root.style.setProperty('--motion-pointer-y', `${event.clientY}px`);
      root.style.setProperty('--pointer-x', `${event.clientX}px`);
      root.style.setProperty('--pointer-y', `${event.clientY}px`);
      pointerFrame = 0;
    });
  }

  syncPreference();
  reducedMotionQuery.addEventListener?.('change', syncPreference);
  document.addEventListener('pointermove', updatePointer, { passive: true });
  document.addEventListener('visibilitychange', () => {
    root.classList.toggle('motion-paused', document.hidden);
  });
  document.addEventListener('DOMContentLoaded', () => {
    registerSurfaces();
    registerAnimatedRegions();
  });

  window.AAPMotion = Object.freeze({ reduced, reveal, feedback, registerAnimatedRegions, registerSurfaces });
})();
