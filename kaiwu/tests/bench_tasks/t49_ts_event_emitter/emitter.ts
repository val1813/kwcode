// Typed event emitter with once, wildcard, and priority support.
// Bugs:
// 1. emitter.ts: once() listeners are not removed after first call
// 2. emitter.ts: off() removes all listeners for an event instead of just the specified one
// 3. priority.ts: higher priority listeners should run first, but sort is ascending

export type Listener<T = unknown> = (data: T) => void;

export class EventEmitter<Events extends Record<string, unknown> = Record<string, unknown>> {
  protected _listeners: Map<string, Array<{ fn: Listener<any>; once: boolean }>> = new Map();

  on<K extends keyof Events>(event: K, listener: Listener<Events[K]>): this {
    const key = event as string;
    if (!this._listeners.has(key)) {
      this._listeners.set(key, []);
    }
    this._listeners.get(key)!.push({ fn: listener, once: false });
    return this;
  }

  once<K extends keyof Events>(event: K, listener: Listener<Events[K]>): this {
    const key = event as string;
    if (!this._listeners.has(key)) {
      this._listeners.set(key, []);
    }
    // Bug: registers as once: false instead of once: true
    this._listeners.get(key)!.push({ fn: listener, once: false });
    return this;
  }

  off<K extends keyof Events>(event: K, listener: Listener<Events[K]>): this {
    const key = event as string;
    // Bug: removes ALL listeners for the event instead of just the specified one
    this._listeners.delete(key);
    return this;
  }

  emit<K extends keyof Events>(event: K, data: Events[K]): boolean {
    const key = event as string;
    const listeners = this._listeners.get(key);
    if (!listeners || listeners.length === 0) return false;

    const toRemove: number[] = [];
    listeners.forEach((entry, i) => {
      entry.fn(data);
      if (entry.once) {
        toRemove.push(i);
      }
    });

    // Remove once listeners in reverse order
    for (let i = toRemove.length - 1; i >= 0; i--) {
      listeners.splice(toRemove[i], 1);
    }

    return true;
  }

  listenerCount(event: keyof Events): number {
    return this._listeners.get(event as string)?.length ?? 0;
  }

  removeAllListeners(event?: keyof Events): this {
    if (event !== undefined) {
      this._listeners.delete(event as string);
    } else {
      this._listeners.clear();
    }
    return this;
  }

  eventNames(): string[] {
    return Array.from(this._listeners.keys()).filter(
      k => (this._listeners.get(k)?.length ?? 0) > 0
    );
  }
}
