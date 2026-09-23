type Observer = (request: Request, status: number) => void;
const observers = new Set<{ readonly observe: Observer }>();

export function observeResponses(observer: Observer): () => void {
  const subscription = { observe: observer };
  observers.add(subscription);
  return () => {
    observers.delete(subscription);
  };
}

export function captureResponseObservers(request: Request): (status: number) => void {
  const captured = [...observers];
  return (status) => {
    if (request.signal.aborted) return;
    for (const observer of captured) {
      if (!observers.has(observer)) continue;
      try {
        observer.observe(request, status);
      } catch {
        /* Observation cannot change transport results. */
      }
    }
  };
}
