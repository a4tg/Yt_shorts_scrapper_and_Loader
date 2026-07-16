export class EventBus {
  constructor() {
    this.target = new EventTarget();
  }

  on(type, listener, options = {}) {
    const handler = (event) => listener(event.detail, event);
    this.target.addEventListener(type, handler, options);
    return () => this.target.removeEventListener(type, handler, options);
  }

  once(type, listener) {
    return this.on(type, listener, { once: true });
  }

  emit(type, detail = {}) {
    return this.target.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

export const workspaceBus = new EventBus();
