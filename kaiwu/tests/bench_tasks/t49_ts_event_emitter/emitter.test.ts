import { describe, it, expect, vi } from "vitest";
import { EventEmitter } from "./emitter";
import { PriorityEmitter } from "./priority";

describe("EventEmitter - on/emit", () => {
  it("calls listener when event emitted", () => {
    const ee = new EventEmitter<{ click: { x: number } }>();
    const calls: { x: number }[] = [];
    ee.on("click", (data) => calls.push(data));
    ee.emit("click", { x: 5 });
    expect(calls).toHaveLength(1);
    expect(calls[0].x).toBe(5);
  });

  it("calls multiple listeners in registration order", () => {
    const ee = new EventEmitter<{ event: string }>();
    const order: string[] = [];
    ee.on("event", () => order.push("first"));
    ee.on("event", () => order.push("second"));
    ee.emit("event", "data");
    expect(order).toEqual(["first", "second"]);
  });

  it("returns false when no listeners", () => {
    const ee = new EventEmitter<{ event: string }>();
    expect(ee.emit("event", "data")).toBe(false);
  });

  it("returns true when listeners exist", () => {
    const ee = new EventEmitter<{ event: string }>();
    ee.on("event", () => {});
    expect(ee.emit("event", "data")).toBe(true);
  });
});

describe("EventEmitter - once", () => {
  it("calls once listener exactly once", () => {
    const ee = new EventEmitter<{ event: string }>();
    let count = 0;
    ee.once("event", () => count++);
    ee.emit("event", "a");
    ee.emit("event", "b");
    expect(count).toBe(1);
  });

  it("removes once listener after first call", () => {
    const ee = new EventEmitter<{ event: string }>();
    ee.once("event", () => {});
    ee.emit("event", "a");
    expect(ee.listenerCount("event")).toBe(0);
  });

  it("once listener does not affect regular listeners", () => {
    const ee = new EventEmitter<{ event: string }>();
    let regular = 0;
    let once = 0;
    ee.on("event", () => regular++);
    ee.once("event", () => once++);
    ee.emit("event", "a");
    ee.emit("event", "b");
    expect(regular).toBe(2);
    expect(once).toBe(1);
  });
});

describe("EventEmitter - off", () => {
  it("removes only the specified listener", () => {
    const ee = new EventEmitter<{ event: string }>();
    let a = 0;
    let b = 0;
    const listenerA = () => a++;
    const listenerB = () => b++;
    ee.on("event", listenerA);
    ee.on("event", listenerB);
    ee.off("event", listenerA);
    ee.emit("event", "data");
    expect(a).toBe(0);
    expect(b).toBe(1);
  });

  it("does not affect other events", () => {
    const ee = new EventEmitter<{ a: string; b: string }>();
    let countA = 0;
    let countB = 0;
    const listenerA = () => countA++;
    ee.on("a", listenerA);
    ee.on("b", () => countB++);
    ee.off("a", listenerA);
    ee.emit("a", "data");
    ee.emit("b", "data");
    expect(countA).toBe(0);
    expect(countB).toBe(1);
  });

  it("listenerCount decrements after off", () => {
    const ee = new EventEmitter<{ event: string }>();
    const fn = () => ;
    ee.on("event", fn);
    ee.on("event", fn);
    ee.off("event", fn);
    expect(ee.listenerCount("event")).toBe(1);
  });
});

describe("EventEmitter - removeAllListeners", () => {
  it("removes all listeners for a specific event", () => {
    const ee = new EventEmitter<{ a: string; b: string }>();
    ee.on("a", () => {});
    ee.on("a", () => {});
    ee.on("b", () => {});
    ee.removeAllListeners("a");
    expect(ee.listenerCount("a")).toBe(0);
    expect(ee.listenerCount("b")).toBe(1);
  });

  it("removes all listeners when no event specified", () => {
    const ee = new EventEmitter<{ a: string; b: string }>();
    ee.on("a", () => {});
    ee.on("b", () => {});
    ee.removeAllListeners();
    expect(ee.eventNames()).toHaveLength(0);
  });
});

describe("PriorityEmitter", () => {
  it("calls higher priority listeners first", () => {
    const pe = new PriorityEmitter<{ event: string }>();
    const order: number[] = [];
    pe.onWithPriority("event", () => order.push(1), 1);
    pe.onWithPriority("event", () => order.push(10), 10);
    pe.onWithPriority("event", () => order.push(5), 5);
    pe.emit("event", "data");
    expect(order[0]).toBe(10);
    expect(order[1]).toBe(5);
    expect(order[2]).toBe(1);
  });

  it("priority listeners run before regular listeners", () => {
    const pe = new PriorityEmitter<{ event: string }>();
    const order: string[] = [];
    pe.on("event", () => order.push("regular"));
    pe.onWithPriority("event", () => order.push("priority"), 1);
    pe.emit("event", "data");
    expect(order[0]).toBe("priority");
    expect(order[1]).toBe("regular");
  });
});
