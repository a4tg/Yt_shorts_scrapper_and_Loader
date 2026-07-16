const PAGE_NAMES = new Set([
  'dashboard', 'content', 'documents', 'library', 'video', 'approvals',
  'messages', 'ai', 'billing', 'admin', 'graph', 'attention',
]);

export function parseWorkspaceHash(hash = window.location.hash) {
  if (!hash.startsWith('#/')) return { page: null, params: new URLSearchParams() };
  const route = hash.slice(2); const separator = route.indexOf('?');
  const rawPage = separator === -1 ? route : route.slice(0, separator);
  const query = separator === -1 ? '' : route.slice(separator + 1);
  const page = PAGE_NAMES.has(rawPage) ? rawPage : null;
  return { page, params: new URLSearchParams(query) };
}

export function buildWorkspaceHash(page, params = {}) {
  if (!PAGE_NAMES.has(page)) throw new Error(`Unknown workspace page: ${page}`);
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value));
  }
  return `#/${page}${query.size ? `?${query}` : ''}`;
}

export class ContextRouter {
  constructor({ bus, bridge = () => window.AAPLegacyApp } = {}) {
    this.bus = bus; this.bridge = bridge; this.started = false;
    this.sync = this.sync.bind(this);
  }

  start() {
    if (this.started) return;
    this.started = true;
    window.addEventListener('hashchange', this.sync);
    window.addEventListener('popstate', this.sync);
    this.sync();
  }

  stop() {
    window.removeEventListener('hashchange', this.sync);
    window.removeEventListener('popstate', this.sync);
    this.started = false;
  }

  open(page, params = {}, { replace = false } = {}) {
    const hash = buildWorkspaceHash(page, params);
    history[replace ? 'replaceState' : 'pushState']({ page, params }, '', `${location.pathname}${location.search}${hash}`);
    this.bridge()?.navigate?.(page, false);
    this.sync();
  }

  sync() {
    const route = parseWorkspaceHash();
    if (!route.page) return;
    this.bus?.emit('route:change', {
      page: route.page,
      params: Object.fromEntries(route.params.entries()),
    });
  }
}
