(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const slowUpdate = window.matchMedia('(update: slow)');
  const compactViewport = window.matchMedia('(max-width: 700px)');
  const saveData = navigator.connection?.saveData === true;
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { alpha: true });
  if (!context || saveData) return;

  canvas.className = 'ambient-particles';
  canvas.setAttribute('aria-hidden', 'true');

  const colors = [
    [128, 109, 255],
    [64, 217, 160],
    [93, 140, 255],
  ];
  let particles = [];
  let width = 0;
  let height = 0;
  let frame = 0;
  let previousTime = 0;
  let resizeTimer = 0;

  function isDisabled() {
    return reducedMotion.matches || slowUpdate.matches;
  }

  function particleCount() {
    const lowPower = (navigator.hardwareConcurrency || 8) <= 4;
    if (compactViewport.matches) return lowPower ? 14 : 18;
    return lowPower ? 24 : 34;
  }

  function createParticle() {
    const color = colors[Math.floor(Math.random() * colors.length)];
    const angle = Math.random() * Math.PI * 2;
    const speed = 4 + Math.random() * 8;
    return {
      x: Math.random() * width,
      y: Math.random() * height,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      radius: .7 + Math.random() * 1.25,
      color,
    };
  }

  function resize() {
    const density = Math.min(window.devicePixelRatio || 1, 1.5);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.round(width * density);
    canvas.height = Math.round(height * density);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(density, 0, 0, density, 0, 0);
    const count = particleCount();
    particles = Array.from({ length: count }, (_, index) => particles[index] || createParticle());
  }

  function drawParticle(particle) {
    const [red, green, blue] = particle.color;
    context.beginPath();
    context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
    context.fillStyle = `rgba(${red}, ${green}, ${blue}, .42)`;
    context.fill();
  }

  function drawConnections() {
    const reach = compactViewport.matches ? 92 : 124;
    for (let first = 0; first < particles.length; first += 1) {
      for (let second = first + 1; second < particles.length; second += 1) {
        const dx = particles[first].x - particles[second].x;
        const dy = particles[first].y - particles[second].y;
        const distance = Math.hypot(dx, dy);
        if (distance >= reach) continue;
        context.beginPath();
        context.moveTo(particles[first].x, particles[first].y);
        context.lineTo(particles[second].x, particles[second].y);
        context.strokeStyle = `rgba(130, 121, 255, ${(1 - distance / reach) * .11})`;
        context.lineWidth = .6;
        context.stroke();
      }
    }
  }

  function render(time) {
    frame = 0;
    if (document.hidden || isDisabled()) return;
    if (time - previousTime < 33) {
      frame = requestAnimationFrame(render);
      return;
    }
    const elapsed = Math.min((time - previousTime) / 1000 || 0, .05);
    previousTime = time;
    context.clearRect(0, 0, width, height);

    particles.forEach((particle) => {
      particle.x += particle.vx * elapsed;
      particle.y += particle.vy * elapsed;
      if (particle.x < -8) particle.x = width + 8;
      if (particle.x > width + 8) particle.x = -8;
      if (particle.y < -8) particle.y = height + 8;
      if (particle.y > height + 8) particle.y = -8;
      drawParticle(particle);
    });
    drawConnections();
    frame = requestAnimationFrame(render);
  }

  function stop() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    previousTime = 0;
  }

  function syncPlayback() {
    canvas.hidden = isDisabled();
    if (document.hidden || isDisabled()) {
      stop();
      return;
    }
    if (!frame) frame = requestAnimationFrame(render);
  }

  document.body.prepend(canvas);
  resize();
  syncPlayback();
  reducedMotion.addEventListener?.('change', syncPlayback);
  slowUpdate.addEventListener?.('change', syncPlayback);
  document.addEventListener('visibilitychange', syncPlayback);
  compactViewport.addEventListener?.('change', resize);
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(resize, 160);
  }, { passive: true });
})();
