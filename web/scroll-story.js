(() => {
  const chapters = [
    { selector: '#features', eyebrow: '01', title: 'Система', hint: 'Соберите процесс' },
    { selector: '.video-feature', eyebrow: '02', title: 'Производство', hint: 'Создавайте контент' },
    { selector: '#pricing', eyebrow: '03', title: 'Масштаб', hint: 'Растите без хаоса' },
    { selector: '#faq', eyebrow: '04', title: 'Ответы', hint: 'Начните уверенно' },
  ].map((chapter) => ({ ...chapter, element: document.querySelector(chapter.selector) }))
    .filter((chapter) => chapter.element);

  if (!chapters.length) return;

  const progress = document.createElement('aside');
  progress.className = 'story-progress';
  progress.setAttribute('aria-label', 'Навигация по странице');
  progress.innerHTML = `
    <div class="story-progress-track" aria-hidden="true"><i></i></div>
    <div class="story-progress-copy" aria-live="polite">
      <small>История продукта</small>
      <b>${chapters[0].title}</b>
    </div>
    <nav></nav>
  `;

  const progressNav = progress.querySelector('nav');
  const progressTitle = progress.querySelector('.story-progress-copy b');

  chapters.forEach((chapter, index) => {
    const section = chapter.element;
    const id = section.id || `story-chapter-${index + 1}`;
    section.id = id;
    section.classList.add('story-chapter');
    section.style.setProperty('--story-index', index);
    section.dataset.storyTitle = chapter.title;

    const marker = document.createElement('div');
    marker.className = 'story-chapter-marker';
    marker.setAttribute('aria-hidden', 'true');
    marker.innerHTML = `
      <span>${chapter.eyebrow}</span>
      <div><small>${chapter.hint}</small><b>${chapter.title}</b></div>
    `;
    section.prepend(marker);

    const ambient = document.createElement('div');
    ambient.className = 'story-chapter-ambient';
    ambient.setAttribute('aria-hidden', 'true');
    ambient.innerHTML = `<i></i><i></i><strong>${chapter.eyebrow}</strong>`;
    section.prepend(ambient);

    const link = document.createElement('a');
    link.href = `#${id}`;
    link.className = 'story-progress-link';
    link.dataset.index = index;
    link.setAttribute('aria-label', `${chapter.eyebrow}. ${chapter.title}`);
    link.innerHTML = `<i></i><span>${chapter.eyebrow}</span>`;
    progressNav.append(link);
  });

  document.body.append(progress);

  let activeIndex = 0;
  let frame = 0;
  const links = [...progressNav.querySelectorAll('a')];
  const reducedMotion = window.AAPMotion?.reduced?.() ?? false;

  const setActive = (nextIndex) => {
    if (nextIndex === activeIndex && document.body.dataset.storyChapter) return;
    activeIndex = nextIndex;
    document.body.dataset.storyChapter = String(nextIndex + 1);
    progressTitle.textContent = chapters[nextIndex].title;
    chapters.forEach(({ element }, index) => element.classList.toggle('is-story-active', index === nextIndex));
    links.forEach((link, index) => {
      const active = index === nextIndex;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
  };

  const updateScrollStory = () => {
    const viewportFocus = window.innerHeight * .46;
    let nearestIndex = 0;
    let nearestDistance = Number.POSITIVE_INFINITY;

    chapters.forEach(({ element }, index) => {
      const bounds = element.getBoundingClientRect();
      const center = bounds.top + bounds.height * .42;
      const distance = Math.abs(center - viewportFocus);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = index;
      }

      const travel = window.innerHeight + bounds.height;
      const localProgress = Math.min(1, Math.max(0, (window.innerHeight - bounds.top) / travel));
      element.style.setProperty('--chapter-progress', localProgress.toFixed(3));
    });

    const storyStart = chapters[0].element.offsetTop - window.innerHeight * .55;
    const lastChapter = chapters.at(-1).element;
    const storyEnd = lastChapter.offsetTop + lastChapter.offsetHeight - window.innerHeight * .42;
    const total = Math.max(1, storyEnd - storyStart);
    const storyProgress = Math.min(1, Math.max(0, (window.scrollY - storyStart) / total));
    document.documentElement.style.setProperty('--story-progress', storyProgress.toFixed(4));
    progress.classList.toggle('is-visible', window.scrollY > storyStart - 120 && window.scrollY < storyEnd + window.innerHeight * .6);
    setActive(nearestIndex);
    frame = 0;
  };

  const requestUpdate = () => {
    if (!frame) frame = requestAnimationFrame(updateScrollStory);
  };

  links.forEach((link, index) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      chapters[index].element.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
      history.replaceState(null, '', link.hash);
    });
  });

  setActive(0);
  updateScrollStory();
  window.addEventListener('scroll', requestUpdate, { passive: true });
  window.addEventListener('resize', requestUpdate, { passive: true });
})();
