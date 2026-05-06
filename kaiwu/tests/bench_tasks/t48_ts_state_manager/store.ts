// State manager: Redux-like store with reducers, middleware, and selectors.
// Bugs:
// 1. store.ts: dispatch() mutates state directly instead of using reducer result
// 2. store.ts: subscribers are called BEFORE state is updated
// 3. middleware.ts: applyMiddleware chains in wrong order (last middleware runs first)

export type Action = { type: string; payload?: unknown };
export type Reducer<S> = (state: S, action: Action) => S;
export type Listener = () => void;
export type Unsubscribe = () => void;

export class Store<S> {
  private state: S;
  private reducer: Reducer<S>;
  private listeners: Set<Listener> = new Set();
  private middlewares: Array<(store: Store<S>) => (next: (a: Action) => void) => (a: Action) => void> = [];
  private dispatch_: (action: Action) => void;

  constructor(reducer: Reducer<S>, initialState: S) {
    this.reducer = reducer;
    this.state = initialState;
    this.dispatch_ = this._baseDispatch.bind(this);
  }

  getState(): S {
    return this.state;
  }

  private _baseDispatch(action: Action): void {
    // Bug: notifies subscribers BEFORE updating state
    this.listeners.forEach(l => l());
    this.state = this.reducer(this.state, action);
  }

  dispatch(action: Action): void {
    this.dispatch_(action);
  }

  subscribe(listener: Listener): Unsubscribe {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  applyMiddleware(
    ...middlewares: Array<(store: Store<S>) => (next: (a: Action) => void) => (a: Action) => void>
  ): void {
    this.middlewares = middlewares;
    const chain = middlewares.map(mw => mw(this));
    // Bug: reduces in wrong order — last middleware wraps first
    this.dispatch_ = chain.reduceRight(
      (next, mw) => mw(next),
      this._baseDispatch.bind(this)
    );
  }
}
