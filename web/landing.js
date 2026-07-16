if (['#verify=', '#reset='].some((prefix) => location.hash.startsWith(prefix))) {
  location.replace(`/app${location.hash}`);
}

const header = document.querySelector('.landing-header');

function buildHeroStage() {
  const hero = document.querySelector('.hero');
  const heroCopy = hero?.querySelector('.hero-copy');
  const board = hero?.querySelector('.hero-board');
  if (!hero || !heroCopy || !board || hero.querySelector('.hero-stage')) return;

  const signal = document.createElement('div');
  signal.className = 'hero-signal';
  signal.innerHTML = '<span>↗</span> От хаоса к единому контент-процессу';
  heroCopy.prepend(signal);

  const stage = document.createElement('div');
  stage.className = 'hero-stage';
  stage.setAttribute('aria-label', 'Интерактивная схема работы с контентом');

  const routes = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  routes.classList.add('hero-route-map');
  routes.setAttribute('viewBox', '0 0 600 600');
  routes.setAttribute('aria-hidden', 'true');
  routes.innerHTML = `
    <defs><linearGradient id="hero-route-gradient" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#806dff"/><stop offset="1" stop-color="#40d9a0"/></linearGradient></defs>
    <path d="M55 105 C145 115 152 212 235 250"/><path d="M545 92 C455 112 450 195 370 244"/>
    <path d="M52 505 C150 475 152 395 235 350"/><path d="M548 510 C452 474 452 402 373 354"/>
    <circle cx="55" cy="105" r="3"/><circle cx="545" cy="92" r="3"/><circle cx="52" cy="505" r="3"/><circle cx="548" cy="510" r="3"/>
  `;

  const core = document.createElement('div');
  core.className = 'hero-stage-core';
  const liveBadge = document.createElement('div');
  liveBadge.className = 'hero-live-badge';
  liveBadge.innerHTML = '<i></i> Живой контент-процесс';
  const flowRail = document.createElement('div');
  flowRail.className = 'hero-flow-rail';
  flowRail.innerHTML = '<span>Идея</span><i></i><span>Создание</span><i></i><span>Готово</span>';

  const particleData = [
    ['video', '▶', 'Видео', 'Shorts · MP4'],
    ['document', '▤', 'Документ', 'Текст · PDF'],
    ['banner', '▧', 'Баннер', 'Креатив · PNG'],
    ['post', '✦', 'Публикация', 'Telegram · VK'],
  ];
  const particles = particleData.map(([kind, icon, title, meta]) => {
    const particle = document.createElement('div');
    particle.className = `hero-particle hero-particle-${kind}`;
    particle.setAttribute('aria-hidden', 'true');
    particle.innerHTML = `<span>${icon}</span><div><b>${title}</b><small>${meta}</small></div>`;
    return particle;
  });

  const scrollCue = document.createElement('div');
  scrollCue.className = 'hero-scroll-cue';
  scrollCue.setAttribute('aria-hidden', 'true');
  scrollCue.innerHTML = '<span>Исследуйте</span><i></i>';

  board.replaceWith(stage);
  core.append(liveBadge, board, flowRail);
  stage.append(routes, ...particles, core, scrollCue);

  if (!window.AAPMotion.reduced() && window.matchMedia('(pointer: fine)').matches) {
    let frame = 0;
    stage.addEventListener('pointermove', (event) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        const bounds = stage.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width - .5;
        const y = (event.clientY - bounds.top) / bounds.height - .5;
        stage.style.setProperty('--hero-rotate-x', `${y * -5}deg`);
        stage.style.setProperty('--hero-rotate-y', `${x * 7}deg`);
        frame = 0;
      });
    }, { passive: true });
    stage.addEventListener('pointerleave', () => {
      stage.style.setProperty('--hero-rotate-x', '0deg');
      stage.style.setProperty('--hero-rotate-y', '0deg');
    });
  }
}

buildHeroStage();

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
