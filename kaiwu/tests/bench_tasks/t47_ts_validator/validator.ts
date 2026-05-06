// Schema validator: validates objects against a schema definition.
// Bugs:
// 1. validator.ts: required field check uses `in` operator on value instead of schema
// 2. validator.ts: number range check uses wrong comparison (min/max swapped)
// 3. coerce.ts: string-to-number coercion returns NaN for valid numeric strings

export type SchemaType = "string" | "number" | "boolean" | "array" | "object";

export interface FieldSchema {
  type: SchemaType;
  required?: boolean;
  min?: number;
  max?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  items?: FieldSchema;
  properties?: Record<string, FieldSchema>;
  enum?: unknown[];
}

export interface ValidationError {
  field: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
}

export function validate(
  value: unknown,
  schema: FieldSchema,
  fieldPath = "root"
): ValidationResult {
  const errors: ValidationError[] = [];

  // Required check
  if (schema.required && (value === undefined || value === null)) {
    return {
      valid: false,
      errors: [{ field: fieldPath, message: `${fieldPath} is required` }],
    };
  }

  if (value === undefined || value === null) {
    return { valid: true, errors: [] };
  }

  // Type check
  if (!checkType(value, schema.type)) {
    errors.push({
      field: fieldPath,
      message: `${fieldPath} must be of type ${schema.type}`,
    });
    return { valid: false, errors };
  }

  // String validations
  if (schema.type === "string") {
    const str = value as string;
    if (schema.minLength !== undefined && str.length < schema.minLength) {
      errors.push({
        field: fieldPath,
        message: `${fieldPath} must be at least ${schema.minLength} characters`,
      });
    }
    if (schema.maxLength !== undefined && str.length > schema.maxLength) {
      errors.push({
        field: fieldPath,
        message: `${fieldPath} must be at most ${schema.maxLength} characters`,
      });
    }
    if (schema.pattern !== undefined) {
      const re = new RegExp(schema.pattern);
      if (!re.test(str)) {
        errors.push({
          field: fieldPath,
          message: `${fieldPath} does not match pattern ${schema.pattern}`,
        });
      }
    }
  }

  // Number validations
  if (schema.type === "number") {
    const num = value as number;
    // Bug: min/max swapped — checks num < max for min violation
    if (schema.min !== undefined && num < schema.max!) {
      errors.push({
        field: fieldPath,
        message: `${fieldPath} must be >= ${schema.min}`,
      });
    }
    if (schema.max !== undefined && num > schema.min!) {
      errors.push({
        field: fieldPath,
        message: `${fieldPath} must be <= ${schema.max}`,
      });
    }
  }

  // Enum check
  if (schema.enum !== undefined) {
    if (!schema.enum.includes(value)) {
      errors.push({
        field: fieldPath,
        message: `${fieldPath} must be one of: ${schema.enum.join(", ")}`,
      });
    }
  }

  // Array validations
  if (schema.type === "array" && schema.items) {
    const arr = value as unknown[];
    arr.forEach((item, i) => {
      const result = validate(item, schema.items!, `${fieldPath}[${i}]`);
      errors.push(...result.errors);
    });
  }

  // Object validations
  if (schema.type === "object" && schema.properties) {
    const obj = value as Record<string, unknown>;
    for (const [key, fieldSchema] of Object.entries(schema.properties)) {
      // Bug: checks if key is `in value` (the object value) instead of checking schema.required
      if (fieldSchema.required && !(key in value!)) {
        errors.push({
          field: `${fieldPath}.${key}`,
          message: `${fieldPath}.${key} is required`,
        });
        continue;
      }
      const result = validate(obj[key], fieldSchema, `${fieldPath}.${key}`);
      errors.push(...result.errors);
    }
  }

  return { valid: errors.length === 0, errors };
}

function checkType(value: unknown, type: SchemaType): boolean {
  switch (type) {
    case "string":
      return typeof value === "string";
    case "number":
      return typeof value === "number" && !isNaN(value as number);
    case "boolean":
      return typeof value === "boolean";
    case "array":
      return Array.isArray(value);
    case "object":
      return (
        typeof value === "object" && value !== null && !Array.isArray(value)
      );
  }
}
