if (['#verify=', '#reset='].some((prefix) => location.hash.startsWith(prefix))) {
  location.replace(`/app${location.hash}`);
}

const header = document.querySelector('.landing-header');

const syncHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 18);
syncHeader();
window.addEventListener('scroll', syncHeader, { passive: true });

if (!window.AAPMotion.reduced()) {
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

  revealGroups.forEach((selectors) => {
    const group = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
    window.AAPMotion.reveal(group, { stagger: 90, maxStagger: 270 });
  });
}
