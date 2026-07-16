(() => {
  const root = document.querySelector('#product-demo');
  if (!root) return;

  const tabs = [...root.querySelectorAll('[data-demo-target]')];
  const panels = [...root.querySelectorAll('[data-demo-panel]')];
  const live = root.querySelector('[data-demo-live]');

  const announce = (message) => {
    if (!live) return;
    live.textContent = '';
    requestAnimationFrame(() => { live.textContent = message; });
  };

  const activateTab = (target, focus = false) => {
    tabs.forEach((tab) => {
      const active = tab.dataset.demoTarget === target;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      tab.classList.toggle('is-active', active);
      if (active && focus) tab.focus();
    });
    panels.forEach((panel) => {
      const active = panel.dataset.demoPanel === target;
      panel.hidden = !active;
      panel.classList.toggle('is-active', active);
    });
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.demoTarget));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else next = (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + tabs.length) % tabs.length;
      activateTab(tabs[next].dataset.demoTarget, true);
    });
  });

  const board = root.querySelector('[data-demo-board]');
  const columns = board ? [...board.querySelectorAll('[data-demo-column]')] : [];
  const updateColumnCounts = () => columns.forEach((column) => {
    const count = column.querySelectorAll('[data-demo-card]').length;
    const badge = column.querySelector('header b');
    if (badge) badge.textContent = String(count);
  });

  board?.addEventListener('click', (event) => {
    const card = event.target.closest('[data-demo-card]');
    if (!card || !board.contains(card)) return;
    if (card.classList.contains('is-moving')) return;
    const currentColumn = card.closest('[data-demo-column]');
    const currentIndex = columns.indexOf(currentColumn);
    const nextColumn = columns[(currentIndex + 1) % columns.length];
    card.classList.add('is-moving');
    window.setTimeout(() => {
      nextColumn.append(card);
      card.classList.remove('is-moving');
      card.classList.toggle('done', nextColumn.dataset.demoColumn === 'ready');
      updateColumnCounts();
      const destination = nextColumn.querySelector('header span')?.textContent || 'следующий этап';
      announce(`Материал «${card.querySelector('b')?.textContent}» перемещён: ${destination}`);
    }, window.AAPMotion?.reduced?.() ? 0 : 180);
  });

  const approvalStates = [
    { status: 'Ожидает редактуру', message: '«Проверяю текст и тайминг финального кадра»', author: 'Мария, редактор' },
    { status: 'На согласовании клиента', message: '«Визуал утверждён, меняем только формулировку CTA»', author: 'Алексей, клиент' },
    { status: 'Готово к публикации', message: '«Все правки внесены. Материал можно ставить в план»', author: 'Система' },
  ];
  const approvalButtons = [...root.querySelectorAll('[data-approval-step]')];
  const approvalStatus = root.querySelector('[data-approval-status]');
  const approvalMessage = root.querySelector('[data-approval-message]');

  approvalButtons.forEach((button, selectedIndex) => button.addEventListener('click', () => {
    approvalButtons.forEach((item, index) => {
      item.classList.toggle('is-complete', index < selectedIndex);
      item.classList.toggle('is-current', index === selectedIndex);
    });
    const state = approvalStates[selectedIndex];
    approvalStatus.textContent = state.status;
    approvalStatus.classList.toggle('ready', selectedIndex === approvalStates.length - 1);
    approvalMessage.innerHTML = `${state.message}<small>${state.author}</small>`;
    announce(`Этап согласования: ${state.status}`);
  }));

  const stage = root.querySelector('[data-demo-stage]');
  const overlay = root.querySelector('[data-demo-overlay]');
  const size = root.querySelector('[data-demo-size]');
  const vertical = root.querySelector('[data-demo-y]');
  const opacity = root.querySelector('[data-demo-opacity]');
  let overlayX = 50;
  let overlayY = Number(vertical?.value || 72);

  const syncOverlay = () => {
    if (!overlay) return;
    overlay.style.setProperty('--demo-overlay-size', `${size.value}%`);
    overlay.style.setProperty('--demo-overlay-opacity', Number(opacity.value) / 100);
    if (stage?.clientWidth) {
      const halfWidth = Math.min(48, (overlay.offsetWidth / stage.clientWidth) * 50);
      overlayX = Math.min(100 - halfWidth, Math.max(halfWidth, overlayX));
    }
    overlay.style.setProperty('--demo-overlay-x', `${overlayX}%`);
    overlay.style.setProperty('--demo-overlay-y', `${overlayY}%`);
    root.querySelector('[data-demo-size-value]').textContent = `${size.value}%`;
    root.querySelector('[data-demo-y-value]').textContent = `${Math.round(overlayY)}%`;
    root.querySelector('[data-demo-opacity-value]').textContent = `${opacity.value}%`;
  };

  [size, vertical, opacity].forEach((input) => input?.addEventListener('input', () => {
    if (input === vertical) overlayY = Number(vertical.value);
    syncOverlay();
  }));

  let dragging = false;
  const moveOverlay = (event) => {
    if (!dragging || !stage) return;
    const bounds = stage.getBoundingClientRect();
    overlayX = Math.min(92, Math.max(8, ((event.clientX - bounds.left) / bounds.width) * 100));
    overlayY = Math.min(92, Math.max(8, ((event.clientY - bounds.top) / bounds.height) * 100));
    vertical.value = String(Math.round(overlayY));
    syncOverlay();
  };

  overlay?.addEventListener('pointerdown', (event) => {
    dragging = true;
    overlay.setPointerCapture(event.pointerId);
    overlay.classList.add('is-dragging');
  });
  overlay?.addEventListener('pointermove', moveOverlay);
  overlay?.addEventListener('pointerup', (event) => {
    if (!dragging) return;
    dragging = false;
    overlay.releasePointerCapture(event.pointerId);
    overlay.classList.remove('is-dragging');
    announce(`Оверлей установлен: ${Math.round(overlayX)}% по горизонтали, ${Math.round(overlayY)}% по вертикали`);
  });
  overlay?.addEventListener('pointercancel', () => {
    dragging = false;
    overlay.classList.remove('is-dragging');
  });
  overlay?.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? 5 : 2;
    if (event.key === 'ArrowLeft') overlayX -= step;
    if (event.key === 'ArrowRight') overlayX += step;
    if (event.key === 'ArrowUp') overlayY -= step;
    if (event.key === 'ArrowDown') overlayY += step;
    overlayY = Math.min(92, Math.max(8, overlayY));
    vertical.value = String(Math.round(overlayY));
    syncOverlay();
    announce(`Позиция оверлея: ${Math.round(overlayX)}% по горизонтали, ${Math.round(overlayY)}% по вертикали`);
  });

  activateTab('plan');
  updateColumnCounts();
  syncOverlay();
})();
