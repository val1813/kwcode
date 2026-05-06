// HTTP API client with interceptors, retry, and caching.
// Bugs:
// 1. client.ts: request interceptors run in reverse order (last registered runs first)
// 2. client.ts: retry logic retries on ALL errors, not just network/5xx errors
// 3. cache.ts: cache key ignores query params (different URLs get same cached response)

export interface RequestConfig {
  url: string;
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  headers?: Record<string, string>;
  body?: unknown;
  params?: Record<string, string>;
  retries?: number;
  useCache?: boolean;
}

export interface Response<T = unknown> {
  status: number;
  data: T;
  headers: Record<string, string>;
  url: string;
}

export type RequestInterceptor = (config: RequestConfig) => RequestConfig;
export type ResponseInterceptor = (response: Response) => Response;

export class ApiClient {
  private baseUrl: string;
  private defaultHeaders: Record<string, string>;
  private requestInterceptors: RequestInterceptor[] = [];
  private responseInterceptors: ResponseInterceptor[] = [];
  private _transport: (config: RequestConfig) => Promise<Response>;

  constructor(
    baseUrl: string,
    transport: (config: RequestConfig) => Promise<Response>
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.defaultHeaders = {};
    this._transport = transport;
  }

  setDefaultHeader(key: string, value: string): void {
    this.defaultHeaders[key] = value;
  }

  addRequestInterceptor(interceptor: RequestInterceptor): void {
    this.requestInterceptors.push(interceptor);
  }

  addResponseInterceptor(interceptor: ResponseInterceptor): void {
    this.responseInterceptors.push(interceptor);
  }

  private applyRequestInterceptors(config: RequestConfig): RequestConfig {
    // Bug: runs interceptors in reverse order (reduceRight instead of reduce)
    return this.requestInterceptors.reduceRight(
      (cfg, interceptor) => interceptor(cfg),
      config
    );
  }

  private applyResponseInterceptors(response: Response): Response {
    return this.responseInterceptors.reduce(
      (resp, interceptor) => interceptor(resp),
      response
    );
  }

  private buildUrl(path: string, params?: Record<string, string>): string {
    const url = `${this.baseUrl}${path}`;
    if (!params || Object.keys(params).length === 0) return url;
    const qs = new URLSearchParams(params).toString();
    return `${url}?${qs}`;
  }

  async request<T>(config: RequestConfig): Promise<Response<T>> {
    const fullConfig: RequestConfig = {
      method: "GET",
      ...config,
      headers: { ...this.defaultHeaders, ...config.headers },
      url: this.buildUrl(config.url, config.params),
    };

    const processedConfig = this.applyRequestInterceptors(fullConfig);
    const maxRetries = config.retries ?? 0;

    let lastError: Error | undefined;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const response = await this._transport(processedConfig);
        const processed = this.applyResponseInterceptors(response);
        return processed as Response<T>;
      } catch (err) {
        lastError = err as Error;
        // Bug: retries on ALL errors including 4xx client errors
        // Should only retry on network errors or 5xx
        if (attempt < maxRetries) continue;
      }
    }
    throw lastError;
  }

  async get<T>(path: string, params?: Record<string, string>): Promise<Response<T>> {
    return this.request<T>({ url: path, method: "GET", params });
  }

  async post<T>(path: string, body: unknown): Promise<Response<T>> {
    return this.request<T>({ url: path, method: "POST", body });
  }

  async put<T>(path: string, body: unknown): Promise<Response<T>> {
    return this.request<T>({ url: path, method: "PUT", body });
  }

  async delete<T>(path: string): Promise<Response<T>> {
    return this.request<T>({ url: path, method: "DELETE" });
  }
}
