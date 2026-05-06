import { describe, it, expect, vi } from "vitest";
import { ApiClient, RequestConfig, Response } from "./client";
import { ResponseCache } from "./cache";

function makeTransport(responses: Response[]): (config: RequestConfig) => Promise<Response> {
  let i = 0;
  return async (_config) => {
    if (i >= responses.length) throw new Error("No more responses");
    return responses[i++];
  };
}

function makeFailTransport(error: Error): (config: RequestConfig) => Promise<Response> {
  return async () => { throw error; };
}

describe("ApiClient - basic requests", () => {
  it("sends GET request and returns response", async () => {
    const transport = makeTransport([{ status: 200, data: { id: 1 }, headers: {}, url: "/users/1" }]);
    const client = new ApiClient("https://api.example.com", transport);
    const resp = await client.get("/users/1");
    expect(resp.status).toBe(200);
    expect((resp.data as any).id).toBe(1);
  });

  it("builds URL with base URL", async () => {
    const received: string[] = [];
    const transport = async (config: RequestConfig): Promise<Response> => {
      received.push(config.url);
      return { status: 200, data: {}, headers: {}, url: config.url };
    };
    const client = new ApiClient("https://api.example.com", transport);
    await client.get("/users");
    expect(received[0]).toBe("https://api.example.com/users");
  });

  it("appends query params to URL", async () => {
    const received: string[] = [];
    const transport = async (config: RequestConfig): Promise<Response> => {
      received.push(config.url);
      return { status: 200, data: {}, headers: {}, url: config.url };
    };
    const client = new ApiClient("https://api.example.com", transport);
    await client.get("/users", { page: "2", limit: "10" });
    expect(received[0]).toContain("page=2");
    expect(received[0]).toContain("limit=10");
  });

  it("sends default headers", async () => {
    const received: Record<string, string>[] = [];
    const transport = async (config: RequestConfig): Promise<Response> => {
      received.push(config.headers ?? {});
      return { status: 200, data: {}, headers: {}, url: config.url };
    };
    const client = new ApiClient("https://api.example.com", transport);
    client.setDefaultHeader("Authorization", "Bearer token");
    await client.get("/users");
    expect(received[0]["Authorization"]).toBe("Bearer token");
  });
});

describe("ApiClient - interceptors", () => {
  it("request interceptors run in registration order (first registered = first applied)", async () => {
    const order: string[] = [];
    const transport = async (config: RequestConfig): Promise<Response> => ({
      status: 200, data: {}, headers: {}, url: config.url,
    });
    const client = new ApiClient("https://api.example.com", transport);
    client.addRequestInterceptor((cfg) => { order.push("first"); return cfg; });
    client.addRequestInterceptor((cfg) => { order.push("second"); return cfg; });
    await client.get("/test");
    expect(order).toEqual(["first", "second"]);
  });

  it("request interceptor can modify config", async () => {
    const received: Record<string, string>[] = [];
    const transport = async (config: RequestConfig): Promise<Response> => {
      received.push(config.headers ?? {});
      return { status: 200, data: {}, headers: {}, url: config.url };
    };
    const client = new ApiClient("https://api.example.com", transport);
    client.addRequestInterceptor((cfg) => ({
      ...cfg,
      headers: { ...cfg.headers, "X-Custom": "injected" },
    }));
    await client.get("/test");
    expect(received[0]["X-Custom"]).toBe("injected");
  });

  it("response interceptors run in registration order", async () => {
    const order: string[] = [];
    const transport = async (): Promise<Response> => ({
      status: 200, data: {}, headers: {}, url: "/test",
    });
    const client = new ApiClient("https://api.example.com", transport);
    client.addResponseInterceptor((resp) => { order.push("first"); return resp; });
    client.addResponseInterceptor((resp) => { order.push("second"); return resp; });
    await client.get("/test");
    expect(order).toEqual(["first", "second"]);
  });
});

describe("ApiClient - retry", () => {
  it("retries on network error", async () => {
    let attempts = 0;
    const transport = async (): Promise<Response> => {
      attempts++;
      if (attempts < 3) throw new Error("Network error");
      return { status: 200, data: "ok", headers: {}, url: "/test" };
    };
    const client = new ApiClient("https://api.example.com", transport);
    const resp = await client.request({ url: "/test", retries: 2 });
    expect(resp.data).toBe("ok");
    expect(attempts).toBe(3);
  });

  it("does not retry on 4xx client errors", async () => {
    let attempts = 0;
    const transport = async (): Promise<Response> => {
      attempts++;
      const err = new Error("Client error") as any;
      err.status = 400;
      err.isClientError = true;
      throw err;
    };
    const client = new ApiClient("https://api.example.com", transport);
    try {
      await client.request({ url: "/test", retries: 3 });
    } catch {}
    expect(attempts).toBe(1);
  });

  it("throws after exhausting retries", async () => {
    const transport = makeFailTransport(new Error("Network error"));
    const client = new ApiClient("https://api.example.com", transport);
    await expect(client.request({ url: "/test", retries: 2 })).rejects.toThrow("Network error");
  });
});

describe("ResponseCache", () => {
  it("stores and retrieves cached response", () => {
    const cache = new ResponseCache(60_000);
    cache.set("/users", { data: [1, 2, 3] }, undefined, 1000);
    const result = cache.get("/users", 1000);
    expect(result).toEqual({ data: [1, 2, 3] });
  });

  it("returns undefined for expired entry", () => {
    const cache = new ResponseCache(60_000);
    cache.set("/users", { data: [] }, 100, 1000);
    expect(cache.get("/users", 1200)).toBeUndefined();
  });

  it("different query params produce different cache entries", () => {
    const cache = new ResponseCache(60_000);
    cache.set("/users?page=1", { page: 1 }, undefined, 1000);
    cache.set("/users?page=2", { page: 2 }, undefined, 1000);
    expect(cache.get("/users?page=1", 1000)).toEqual({ page: 1 });
    expect(cache.get("/users?page=2", 1000)).toEqual({ page: 2 });
  });

  it("same URL without params hits same cache entry", () => {
    const cache = new ResponseCache(60_000);
    cache.set("/users", { data: "all" }, undefined, 1000);
    expect(cache.get("/users", 1000)).toEqual({ data: "all" });
  });

  it("delete removes entry", () => {
    const cache = new ResponseCache(60_000);
    cache.set("/users", { data: [] }, undefined, 1000);
    cache.delete("/users");
    expect(cache.get("/users", 1000)).toBeUndefined();
  });
});
