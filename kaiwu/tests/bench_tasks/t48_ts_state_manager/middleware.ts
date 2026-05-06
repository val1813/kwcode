// Middleware utilities for the state manager.

import { Store, Action } from "./store";

export type MiddlewareFn<S> = (
  store: Store<S>
) => (next: (a: Action) => void) => (a: Action) => void;

/** Logger middleware: logs action type and state before/after. */
export function createLogger<S>(log: string[]): MiddlewareFn<S> {
  return (store) => (next) => (action) => {
    log.push(`before:${action.type}`);
    next(action);
    log.push(`after:${action.type}`);
  };
}

/** Thunk middleware: allows dispatching functions instead of plain actions. */
export function createThunk<S>(): MiddlewareFn<S> {
  return (store) => (next) => (action: any) => {
    if (typeof action === "function") {
      return action(store.dispatch.bind(store), store.getState.bind(store));
    }
    return next(action);
  };
}

/** Validator middleware: rejects actions without a 'type' field. */
export function createValidator<S>(): MiddlewareFn<S> {
  return (_store) => (next) => (action) => {
    if (!action || typeof action.type !== "string") {
      throw new Error(`Invalid action: missing 'type' field`);
    }
    return next(action);
  };
}

/** Batch middleware: processes an array of actions as a single batch. */
export function createBatch<S>(): MiddlewareFn<S> {
  return (_store) => (next) => (action: any) => {
    if (action.type === "@@BATCH" && Array.isArray(action.payload)) {
      action.payload.forEach((a: Action) => next(a));
      return;
    }
    return next(action);
  };
}
