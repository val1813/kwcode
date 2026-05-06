// Selectors: memoized state selectors.

export type Selector<S, R> = (state: S) => R;

/** Creates a memoized selector that recomputes only when inputs change. */
export function createSelector<S, I extends unknown[], R>(
  inputSelectors: { [K in keyof I]: Selector<S, I[K]> },
  resultFn: (...args: I) => R
): Selector<S, R> {
  let lastInputs: I | undefined;
  let lastResult: R;

  return (state: S): R => {
    const inputs = inputSelectors.map(sel => sel(state)) as I;
    if (
      lastInputs !== undefined &&
      inputs.every((v, i) => v === lastInputs![i])
    ) {
      return lastResult;
    }
    lastInputs = inputs;
    lastResult = resultFn(...inputs);
    return lastResult;
  };
}

/** Combines multiple reducers into one. */
export function combineReducers<S extends Record<string, unknown>>(
  reducers: { [K in keyof S]: (state: S[K] | undefined, action: { type: string }) => S[K] }
): (state: S | undefined, action: { type: string }) => S {
  return (state = {} as S, action) => {
    const nextState = {} as S;
    let changed = false;
    for (const key in reducers) {
      const prev = state[key];
      const next = reducers[key](prev, action);
      nextState[key] = next;
      if (next !== prev) changed = true;
    }
    return changed ? nextState : state;
  };
}
