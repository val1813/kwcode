// Response cache for the API client.
// Bug: cache key ignores query params — different URLs with different params get same cache entry

export interface CacheEntry<T> {
  data: T;
  expiresAt: number;
}

export class ResponseCache {
  private store: Map<string, CacheEntry<unknown>> = new Map();
  private defaultTtl: number;

  constructor(defaultTtlMs: number = 60_000) {
    this.defaultTtl = defaultTtlMs;
  }

  private buildKey(url: string): string {
    // Bug: strips query string from URL before using as cache key
    const withoutQuery = url.split("?")[0];
    return withoutQuery;
  }

  get<T>(url: string, now = Date.now()): T | undefined {
    const key = this.buildKey(url);
    const entry = this.store.get(key);
    if (!entry) return undefined;
    if (now > entry.expiresAt) {
      this.store.delete(key);
      return undefined;
    }
    return entry.data as T;
  }

  set<T>(url: string, data: T, ttlMs?: number, now = Date.now()): void {
    const key = this.buildKey(url);
    const ttl = ttlMs ?? this.defaultTtl;
    this.store.set(key, { data, expiresAt: now + ttl });
  }

  delete(url: string): boolean {
    return this.store.delete(this.buildKey(url));
  }

  clear(): void {
    this.store.clear();
  }

  size(): number {
    return this.store.size;
  }
}
