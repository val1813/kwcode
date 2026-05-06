// Priority event emitter: listeners run in priority order (higher = first).

import { EventEmitter, Listener } from "./emitter";

interface PriorityEntry<T> {
  fn: Listener<T>;
  priority: number;
  once: boolean;
}

export class PriorityEmitter<
  Events extends Record<string, unknown> = Record<string, unknown>
> extends EventEmitter<Events> {
  private _priorityListeners: Map<string, PriorityEntry<any>[]> = new Map();

  onWithPriority<K extends keyof Events>(
    event: K,
    listener: Listener<Events[K]>,
    priority: number
  ): this {
    const key = event as string;
    if (!this._priorityListeners.has(key)) {
      this._priorityListeners.set(key, []);
    }
    const entries = this._priorityListeners.get(key)!;
    entries.push({ fn: listener, priority, once: false });
    // Bug: sorts ascending (lowest priority first) instead of descending
    entries.sort((a, b) => a.priority - b.priority);
    return this;
  }

  emit<K extends keyof Events>(event: K, data: Events[K]): boolean {
    const key = event as string;
    const priorityEntries = this._priorityListeners.get(key);
    if (priorityEntries && priorityEntries.length > 0) {
      const toRemove: number[] = [];
      priorityEntries.forEach((entry, i) => {
        entry.fn(data);
        if (entry.once) toRemove.push(i);
      });
      for (let i = toRemove.length - 1; i >= 0; i--) {
        priorityEntries.splice(toRemove[i], 1);
      }
    }
    // Also call regular listeners
    return super.emit(event, data);
  }
}
