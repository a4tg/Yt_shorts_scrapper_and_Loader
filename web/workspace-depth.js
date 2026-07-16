import { workspaceBus } from './core/event-bus.js';
import { FeatureFlags } from './core/feature-flags.js';
import { ContextRouter } from './core/context-router.js';
import { initChatAnywhere } from './modules/chat-anywhere.js';
import { initAssetViewer } from './modules/asset-viewer.js';
import { initAssetReviews } from './modules/asset-reviews.js';

const flags = new FeatureFlags(window.AAPLegacyApp?.getAuthConfig?.() || {});
const router = new ContextRouter({ bus: workspaceBus });
const modules = new Map();

function registerModule(name, initializer, requiredFlag = 'workspace_depth_shell') {
  if (modules.has(name)) throw new Error(`Workspace module already registered: ${name}`);
  modules.set(name, { initializer, requiredFlag, instance: null });
  if (flags.enabled(requiredFlag)) startModule(name);
}

function startModule(name) {
  const module = modules.get(name);
  if (!module || module.instance || !flags.enabled(module.requiredFlag)) return module?.instance || null;
  module.instance = module.initializer({ bus: workspaceBus, flags, router, bridge: window.AAPLegacyApp }) || {};
  workspaceBus.emit('module:started', { name });
  return module.instance;
}

function hydrateFeatures(config) {
  flags.hydrate(config);
  document.documentElement.dataset.workspaceDepth = flags.enabled('workspace_depth_shell') ? 'enabled' : 'disabled';
  for (const name of modules.keys()) startModule(name);
  workspaceBus.emit('features:changed', flags.snapshot());
}

window.AAPWorkspaceDepth = Object.freeze({
  bus: workspaceBus,
  flags,
  router,
  registerModule,
  startModule,
});

registerModule('chat-anywhere', initChatAnywhere, 'chat_anywhere');
registerModule('asset-viewer', initAssetViewer, 'asset_viewer');
registerModule('asset-reviews', initAssetReviews, 'asset_reviews');

window.addEventListener('aap:auth-config', (event) => hydrateFeatures(event.detail));
window.addEventListener('aap:context-change', (event) => workspaceBus.emit('context:change', event.detail));
window.addEventListener('aap:legacy-ready', () => hydrateFeatures(window.AAPLegacyApp?.getAuthConfig?.() || {}));

hydrateFeatures(window.AAPLegacyApp?.getAuthConfig?.() || {});
router.start();
workspaceBus.emit('shell:ready', { context: window.AAPLegacyApp?.getContext?.() || null });
