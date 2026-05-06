import { describe, it, expect, vi } from "vitest";
import { Store, Action } from "./store";
import { createLogger, createThunk, createValidator, createBatch } from "./middleware";
import { createSelector, combineReducers } from "./selectors";

// Simple counter reducer
function counterReducer(state = { count: 0 }, action: Action) {
  switch (action.type) {
    case "INCREMENT":
      return { count: state.count + (action.payload as number ?? 1) };
    case "DECREMENT":
      return { count: state.count - 1 };
    case "RESET":
      return { count: 0 };
    default:
      return state;
  }
}

describe("Store - basic", () => {
  it("returns initial state", () => {
    const store = new Store(counterReducer, { count: 0 });
    expect(store.getState()).toEqual({ count: 0 });
  });

  it("updates state after dispatch", () => {
    const store = new Store(counterReducer, { count: 0 });
    store.dispatch({ type: "INCREMENT" });
    expect(store.getState().count).toBe(1);
  });

  it("notifies subscriber after state update", () => {
    const store = new Store(counterReducer, { count: 0 });
    let stateAtNotification: number | undefined;
    store.subscribe(() => {
      stateAtNotification = store.getState().count;
    });
    store.dispatch({ type: "INCREMENT" });
    // Subscriber must be called AFTER state is updated
    expect(stateAtNotification).toBe(1);
  });

  it("unsubscribe stops notifications", () => {
    const store = new Store(counterReducer, { count: 0 });
    const calls: number[] = [];
    const unsub = store.subscribe(() => calls.push(store.getState().count));
    store.dispatch({ type: "INCREMENT" });
    unsub();
    store.dispatch({ type: "INCREMENT" });
    expect(calls).toHaveLength(1);
  });

  it("multiple subscribers all notified", () => {
    const store = new Store(counterReducer, { count: 0 });
    const calls: string[] = [];
    store.subscribe(() => calls.push("a"));
    store.subscribe(() => calls.push("b"));
    store.dispatch({ type: "INCREMENT" });
    expect(calls).toContain("a");
    expect(calls).toContain("b");
  });
});

describe("Store - middleware", () => {
  it("logger middleware logs in order: before then after", () => {
    const store = new Store(counterReducer, { count: 0 });
    const log: string[] = [];
    store.applyMiddleware(createLogger(log));
    store.dispatch({ type: "INCREMENT" });
    expect(log).toEqual(["before:INCREMENT", "after:INCREMENT"]);
  });

  it("middleware executes in registration order (first registered = outermost)", () => {
    const store = new Store(counterReducer, { count: 0 });
    const order: string[] = [];
    const mw1 = (_: Store<any>) => (next: any) => (action: Action) => {
      order.push("mw1-before");
      next(action);
      order.push("mw1-after");
    };
    const mw2 = (_: Store<any>) => (next: any) => (action: Action) => {
      order.push("mw2-before");
      next(action);
      order.push("mw2-after");
    };
    store.applyMiddleware(mw1, mw2);
    store.dispatch({ type: "INCREMENT" });
    expect(order).toEqual(["mw1-before", "mw2-before", "mw2-after", "mw1-after"]);
  });

  it("thunk middleware dispatches function", () => {
    const store = new Store(counterReducer, { count: 0 });
    store.applyMiddleware(createThunk());
    store.dispatch(((dispatch: any, getState: any) => {
      dispatch({ type: "INCREMENT" });
      dispatch({ type: "INCREMENT" });
    }) as any);
    expect(store.getState().count).toBe(2);
  });

  it("validator middleware rejects invalid action", () => {
    const store = new Store(counterReducer, { count: 0 });
    store.applyMiddleware(createValidator());
    expect(() => store.dispatch({} as any)).toThrow("type");
  });

  it("batch middleware processes all actions", () => {
    const store = new Store(counterReducer, { count: 0 });
    store.applyMiddleware(createBatch());
    store.dispatch({
      type: "@@BATCH",
      payload: [
        { type: "INCREMENT" },
        { type: "INCREMENT" },
        { type: "INCREMENT" },
      ],
    });
    expect(store.getState().count).toBe(3);
  });
});

describe("Selectors", () => {
  it("createSelector returns correct value", () => {
    const getCount = (s: { count: number }) => s.count;
    const getDoubled = createSelector([getCount], (count) => count * 2);
    expect(getDoubled({ count: 5 })).toBe(10);
  });

  it("createSelector memoizes result", () => {
    const getCount = (s: { count: number }) => s.count;
    let computations = 0;
    const getDoubled = createSelector([getCount], (count) => {
      computations++;
      return count * 2;
    });
    const state = { count: 5 };
    getDoubled(state);
    getDoubled(state);
    expect(computations).toBe(1);
  });

  it("createSelector recomputes when input changes", () => {
    const getCount = (s: { count: number }) => s.count;
    let computations = 0;
    const getDoubled = createSelector([getCount], (count) => {
      computations++;
      return count * 2;
    });
    getDoubled({ count: 5 });
    getDoubled({ count: 6 });
    expect(computations).toBe(2);
  });

  it("combineReducers combines state slices", () => {
    const combined = combineReducers({
      counter: counterReducer,
      name: (state = "default", action: Action) =>
        action.type === "SET_NAME" ? (action.payload as string) : state,
    });
    const state = combined(undefined, { type: "@@INIT" });
    expect(state.counter).toEqual({ count: 0 });
    expect(state.name).toBe("default");
  });

  it("combineReducers updates only changed slice", () => {
    const combined = combineReducers({
      counter: counterReducer,
      name: (state = "default", _action: Action) => state,
    });
    const s1 = combined(undefined, { type: "@@INIT" });
    const s2 = combined(s1, { type: "INCREMENT" });
    expect(s2.counter.count).toBe(1);
    expect(s2.name).toBe("default");
    // Unchanged slice should be same reference
    expect(s2.name).toBe(s1.name);
  });
});
