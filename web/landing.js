if (['#verify=', '#reset='].some((prefix) => location.hash.startsWith(prefix))) {
  location.replace(`/app${location.hash}`);
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const header = document.querySelector('.landing-header');

const syncHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 18);
syncHeader();
window.addEventListener('scroll', syncHeader, { passive: true });

if (!reducedMotion) {
  document.documentElement.classList.add('motion-ready');

  const revealGroups = [
    ['.hero-copy', '.hero-board'],
    ['.proof span'],
    ['.landing-section > .kicker', '.landing-section > h2'],
    ['.feature-grid article'],
    ['.video-feature > div'],
    ['.price-grid article'],
    ['.faq details'],
    ['.final-cta > *'],
    ['footer > *'],
  ];

  const revealItems = [];
  revealGroups.forEach((selectors) => {
    const group = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
    group.forEach((element, index) => {
      element.classList.add('reveal-item');
      element.style.setProperty('--reveal-delay', `${Math.min(index % 4, 3) * 90}ms`);
      revealItems.push(element);
    });
  });

  const observer = new IntersectionObserver((entries, activeObserver) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-visible');
      activeObserver.unobserve(entry.target);
    });
  }, { threshold: .12, rootMargin: '0px 0px -42px' });

  revealItems.forEach((element) => observer.observe(element));

  if (window.matchMedia('(pointer: fine)').matches) {
    let pointerFrame = 0;
    window.addEventListener('pointermove', (event) => {
      if (pointerFrame) return;
      pointerFrame = requestAnimationFrame(() => {
        document.documentElement.style.setProperty('--pointer-x', `${event.clientX}px`);
        document.documentElement.style.setProperty('--pointer-y', `${event.clientY}px`);
        pointerFrame = 0;
      });
    }, { passive: true });
  }
}
