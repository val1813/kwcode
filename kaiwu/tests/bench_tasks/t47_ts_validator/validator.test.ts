import { describe, it, expect } from "vitest";
import { validate, FieldSchema } from "./validator";
import { coerceNumber, coerceBoolean, coerceString, coerceArray } from "./coerce";

describe("validate - primitives", () => {
  it("accepts valid string", () => {
    const result = validate("hello", { type: "string" });
    expect(result.valid).toBe(true);
  });

  it("rejects wrong type", () => {
    const result = validate(42, { type: "string" });
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("string");
  });

  it("accepts valid number", () => {
    const result = validate(42, { type: "number" });
    expect(result.valid).toBe(true);
  });

  it("accepts valid boolean", () => {
    const result = validate(true, { type: "boolean" });
    expect(result.valid).toBe(true);
  });
});

describe("validate - required", () => {
  it("rejects undefined required field", () => {
    const result = validate(undefined, { type: "string", required: true });
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("required");
  });

  it("rejects null required field", () => {
    const result = validate(null, { type: "string", required: true });
    expect(result.valid).toBe(false);
  });

  it("accepts undefined optional field", () => {
    const result = validate(undefined, { type: "string" });
    expect(result.valid).toBe(true);
  });
});

describe("validate - string constraints", () => {
  it("enforces minLength", () => {
    const result = validate("hi", { type: "string", minLength: 5 });
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("5");
  });

  it("enforces maxLength", () => {
    const result = validate("hello world", { type: "string", maxLength: 5 });
    expect(result.valid).toBe(false);
  });

  it("accepts string within length bounds", () => {
    const result = validate("hello", { type: "string", minLength: 3, maxLength: 10 });
    expect(result.valid).toBe(true);
  });

  it("enforces pattern", () => {
    const result = validate("abc123", { type: "string", pattern: "^[a-z]+$" });
    expect(result.valid).toBe(false);
  });

  it("accepts string matching pattern", () => {
    const result = validate("hello", { type: "string", pattern: "^[a-z]+$" });
    expect(result.valid).toBe(true);
  });
});

describe("validate - number constraints", () => {
  it("enforces min", () => {
    const result = validate(3, { type: "number", min: 5 });
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("5");
  });

  it("enforces max", () => {
    const result = validate(15, { type: "number", max: 10 });
    expect(result.valid).toBe(false);
    expect(result.errors[0].message).toContain("10");
  });

  it("accepts number within range", () => {
    const result = validate(7, { type: "number", min: 1, max: 10 });
    expect(result.valid).toBe(true);
  });

  it("accepts number at min boundary", () => {
    const result = validate(5, { type: "number", min: 5 });
    expect(result.valid).toBe(true);
  });

  it("accepts number at max boundary", () => {
    const result = validate(10, { type: "number", max: 10 });
    expect(result.valid).toBe(true);
  });
});

describe("validate - enum", () => {
  it("accepts valid enum value", () => {
    const result = validate("red", { type: "string", enum: ["red", "green", "blue"] });
    expect(result.valid).toBe(true);
  });

  it("rejects invalid enum value", () => {
    const result = validate("yellow", { type: "string", enum: ["red", "green", "blue"] });
    expect(result.valid).toBe(false);
  });
});

describe("validate - object", () => {
  const schema: FieldSchema = {
    type: "object",
    properties: {
      name: { type: "string", required: true, minLength: 1 },
      age: { type: "number", min: 0, max: 150 },
      email: { type: "string", pattern: "^[^@]+@[^@]+$" },
    },
  };

  it("accepts valid object", () => {
    const result = validate({ name: "Alice", age: 30, email: "alice@example.com" }, schema);
    expect(result.valid).toBe(true);
  });

  it("rejects missing required field", () => {
    const result = validate({ age: 30 }, schema);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.field.includes("name"))).toBe(true);
  });

  it("rejects invalid nested field", () => {
    const result = validate({ name: "Alice", age: -5 }, schema);
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.field.includes("age"))).toBe(true);
  });

  it("accepts object with optional fields missing", () => {
    const result = validate({ name: "Bob" }, schema);
    expect(result.valid).toBe(true);
  });
});

describe("validate - array", () => {
  it("accepts valid array", () => {
    const result = validate([1, 2, 3], { type: "array", items: { type: "number" } });
    expect(result.valid).toBe(true);
  });

  it("rejects array with invalid items", () => {
    const result = validate([1, "two", 3], { type: "array", items: { type: "number" } });
    expect(result.valid).toBe(false);
    expect(result.errors.some(e => e.field.includes("[1]"))).toBe(true);
  });
});

describe("coerce", () => {
  it("coerceNumber handles integer string", () => {
    expect(coerceNumber("42")).toBe(42);
  });

  it("coerceNumber handles float string", () => {
    expect(coerceNumber("3.14")).toBeCloseTo(3.14);
  });

  it("coerceNumber returns undefined for non-numeric", () => {
    expect(coerceNumber("abc")).toBeUndefined();
  });

  it("coerceNumber passes through number", () => {
    expect(coerceNumber(99)).toBe(99);
  });

  it("coerceBoolean handles string 'true'", () => {
    expect(coerceBoolean("true")).toBe(true);
  });

  it("coerceBoolean handles string 'false'", () => {
    expect(coerceBoolean("false")).toBe(false);
  });

  it("coerceArray filters undefined", () => {
    const result = coerceArray(["1", "abc", "3"], coerceNumber);
    expect(result).toEqual([1, 3]);
  });
});
