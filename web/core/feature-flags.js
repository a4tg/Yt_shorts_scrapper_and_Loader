export const WORKSPACE_DEPTH_FLAGS = Object.freeze([
  'workspace_depth_shell',
  'chat_anywhere',
  'asset_viewer',
  'asset_reviews',
  'project_graph',
  'decision_intelligence',
]);

export class FeatureFlags {
  constructor(initial = {}) {
    this.values = Object.fromEntries(WORKSPACE_DEPTH_FLAGS.map((name) => [name, false]));
    this.hydrate(initial);
  }

  hydrate(config = {}) {
    const values = config.features || config;
    for (const name of WORKSPACE_DEPTH_FLAGS) {
      if (Object.hasOwn(values, name)) this.values[name] = values[name] === true;
    }
    return this.snapshot();
  }

  enabled(name) {
    return this.values[name] === true;
  }

  snapshot() {
    return Object.freeze({ ...this.values });
  }
}
