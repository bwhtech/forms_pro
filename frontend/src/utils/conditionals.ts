import {
  ConditionalLogic,
  ConditionalOperators,
  Condition,
  Actions,
} from "@/types/conditional-render.types";
import { FormField, Fieldtype } from "@/types/formfield";
import { getFieldTypeDef } from "@/config/fieldTypes";

/**
 * Parse conditional_logic string into ConditionalLogic object
 */
export function parseConditionalLogic(
  conditionalLogic: string | undefined | null
): ConditionalLogic | null {
  if (!conditionalLogic || conditionalLogic.trim() === "") {
    return null;
  }

  try {
    return JSON.parse(conditionalLogic) as ConditionalLogic;
  } catch (e) {
    console.error("Failed to parse conditional_logic:", e);
    return null;
  }
}

/**
 * Field types whose values are compared numerically. Rating is stored by
 * Frappe as a 0..1 fraction (e.g. 1 of 5 stars = 0.2).
 */
const NUMERIC_FIELDTYPES = new Set<string>([
  Fieldtype.NUMBER,
  Fieldtype.RATING,
]);

/**
 * Convert a value to a finite number, or null if it isn't numeric.
 * Condition values typed in the builder are stored as strings, so "0.2"
 * must be parsed before it can be compared against a numeric field value.
 */
function toNumber(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const num = Number(value.trim());
    return Number.isFinite(num) ? num : null;
  }
  return null;
}

/**
 * Get the value of a field from form data, handling different field types
 */
function getFieldValue(
  fieldValue: any,
  fieldType: string
): string | number | boolean | null {
  if (fieldValue === null || fieldValue === undefined || fieldValue === "") {
    return null;
  }

  const typeDef = getFieldTypeDef(fieldType as Fieldtype);

  if (typeDef?.isBoolean) {
    return Boolean(fieldValue);
  }

  // Handle numeric fields (Number, Rating)
  if (NUMERIC_FIELDTYPES.has(fieldType)) {
    return toNumber(fieldValue);
  }

  // Handle string fields
  return String(fieldValue);
}

/**
 * Normalize values for comparison (convert to strings for most comparisons)
 */
function normalizeValue(value: any): string | number | boolean | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value;
  }
  return String(value).trim();
}

/**
 * Compare two values numerically. Returns false if either isn't numeric.
 */
function compareNumbers(
  left: unknown,
  right: unknown,
  compare: (a: number, b: number) => boolean
): boolean {
  const a = toNumber(left);
  const b = toNumber(right);
  if (a === null || b === null) return false;
  return compare(a, b);
}

/**
 * Evaluate a single condition against form field values
 */
function evaluateCondition(
  condition: Condition,
  formValues: Record<string, any>,
  allFields: FormField[]
): boolean {
  const { fieldname, operator, value } = condition;

  // Get the field definition to understand its type
  const field = allFields.find((f) => f.fieldname === fieldname);
  if (!field) {
    console.warn(`Field ${fieldname} not found in form fields`);
    return false;
  }

  const fieldValue = getFieldValue(formValues[fieldname], field.fieldtype);
  const normalizedFieldValue = normalizeValue(fieldValue);
  let conditionValue = normalizeValue(value);
  // Numeric fields: compare the condition value as a number too, so that
  // "0.2" (as typed in the builder) matches a rating of 0.2.
  if (NUMERIC_FIELDTYPES.has(field.fieldtype)) {
    conditionValue = toNumber(conditionValue) ?? conditionValue;
  }

  switch (operator) {
    case ConditionalOperators.Is:
      return normalizedFieldValue === conditionValue;

    case ConditionalOperators.IsNot:
      return normalizedFieldValue !== conditionValue;

    case ConditionalOperators.IsEmpty:
      return (
        normalizedFieldValue === null ||
        normalizedFieldValue === "" ||
        normalizedFieldValue === undefined
      );

    case ConditionalOperators.IsNotEmpty:
      return (
        normalizedFieldValue !== null &&
        normalizedFieldValue !== "" &&
        normalizedFieldValue !== undefined
      );

    case ConditionalOperators.IsSet:
      return (
        normalizedFieldValue !== null && normalizedFieldValue !== undefined
      );

    case ConditionalOperators.Contains:
      if (normalizedFieldValue === null) return false;
      return String(normalizedFieldValue)
        .toLowerCase()
        .includes(String(conditionValue).toLowerCase());

    case ConditionalOperators.DoesNotContain:
      if (normalizedFieldValue === null) return true;
      return !String(normalizedFieldValue)
        .toLowerCase()
        .includes(String(conditionValue).toLowerCase());

    case ConditionalOperators.IsLessThan:
      return compareNumbers(
        normalizedFieldValue,
        conditionValue,
        (a, b) => a < b
      );

    case ConditionalOperators.IsLessThanOrEqualTo:
      return compareNumbers(
        normalizedFieldValue,
        conditionValue,
        (a, b) => a <= b
      );

    case ConditionalOperators.IsGreaterThan:
      return compareNumbers(
        normalizedFieldValue,
        conditionValue,
        (a, b) => a > b
      );

    case ConditionalOperators.IsGreaterThanOrEqualTo:
      return compareNumbers(
        normalizedFieldValue,
        conditionValue,
        (a, b) => a >= b
      );

    case ConditionalOperators.StartsWith:
      if (normalizedFieldValue === null) return false;
      return String(normalizedFieldValue)
        .toLowerCase()
        .startsWith(String(conditionValue).toLowerCase());

    case ConditionalOperators.DoesNotStartWith:
      if (normalizedFieldValue === null) return true;
      return !String(normalizedFieldValue)
        .toLowerCase()
        .startsWith(String(conditionValue).toLowerCase());

    case ConditionalOperators.EndsWith:
      if (normalizedFieldValue === null) return false;
      return String(normalizedFieldValue)
        .toLowerCase()
        .endsWith(String(conditionValue).toLowerCase());

    case ConditionalOperators.DoesNotEndWith:
      if (normalizedFieldValue === null) return true;
      return !String(normalizedFieldValue)
        .toLowerCase()
        .endsWith(String(conditionValue).toLowerCase());

    default:
      console.warn(`Unknown operator: ${operator}`);
      return false;
  }
}

/**
 * Evaluate all conditions in a ConditionalLogic object
 * Currently supports AND logic (all conditions must be true)
 * TODO: Add support for OR logic if needed
 */
function evaluateConditions(
  conditionalLogic: ConditionalLogic,
  formValues: Record<string, any>,
  allFields: FormField[]
): boolean {
  if (
    !conditionalLogic.conditions ||
    conditionalLogic.conditions.length === 0
  ) {
    return false;
  }

  // All conditions must be true (AND logic)
  return conditionalLogic.conditions.every((condition) =>
    evaluateCondition(condition, formValues, allFields)
  );
}

/**
 * Determine if a field should be visible based on:
 * 1. The field's `hidden` property (base state)
 * 2. Conditional logic rules defined on other fields that target this field
 *
 * Visibility logic:
 * - If field.hidden === true, field starts as hidden
 * - If field.hidden === false or undefined, field starts as visible
 * - Conditional logic can override the base state:
 *   - "Show Field" action can make a hidden field visible
 *   - "Hide Field" action can make a visible field hidden
 * - If no conditional logic applies, the base hidden state is used
 */
export function shouldFieldBeVisible(
  field: FormField,
  formValues: Record<string, any>,
  allFields: FormField[]
): boolean {
  // Start with the field's base hidden state
  // If hidden is true, field is hidden by default; if false/undefined, it's visible
  const baseIsVisible = !field.hidden;

  // Find all conditional logic rules that target this field
  const targetingRules: ConditionalLogic[] = [];

  allFields.forEach((otherField) => {
    if (!otherField.conditional_logic) return;

    const logic = parseConditionalLogic(otherField.conditional_logic);
    if (logic && logic.target_field === field.fieldname) {
      targetingRules.push(logic);
    }
  });

  // If no rules target this field, use the base hidden state
  if (targetingRules.length === 0) {
    return baseIsVisible;
  }

  // Check each rule
  let hasShowRule = false;
  let hasHideRule = false;

  for (const rule of targetingRules) {
    const conditionsMet = evaluateConditions(rule, formValues, allFields);

    if (conditionsMet) {
      if (rule.action === Actions.ShowField) {
        hasShowRule = true;
      } else if (rule.action === Actions.HideField) {
        hasHideRule = true;
      }
    }
  }

  // Conditional logic overrides base state:
  // - "Show Field" action can override hidden state (make it visible)
  if (hasShowRule) {
    return true;
  }

  // - "Hide Field" action can override visible state (make it hidden)
  if (hasHideRule) {
    return false;
  }

  // If no conditional rules are met, use the base hidden state
  return baseIsVisible;
}

/**
 * Determine if a field should be required based on conditional logic
 */
export function shouldFieldBeRequired(
  field: FormField,
  formValues: Record<string, any>,
  allFields: FormField[]
): boolean {
  // Start with the field's base required status
  let isRequired = field.reqd || false;

  // Find all conditional logic rules that target this field with "Require Answer"
  const targetingRules: ConditionalLogic[] = [];

  allFields.forEach((otherField) => {
    if (!otherField.conditional_logic) return;

    const logic = parseConditionalLogic(otherField.conditional_logic);
    if (
      logic &&
      logic.target_field === field.fieldname &&
      logic.action === Actions.RequireAnswer
    ) {
      targetingRules.push(logic);
    }
  });

  // If any "Require Answer" rule conditions are met, make field required
  for (const rule of targetingRules) {
    if (evaluateConditions(rule, formValues, allFields)) {
      isRequired = true;
      break;
    }
  }

  return isRequired;
}

/**
 * Get all fields that should be visible based on current form values
 */
export function getVisibleFields(
  fields: FormField[],
  formValues: Record<string, any>
): FormField[] {
  return fields.filter((field) =>
    shouldFieldBeVisible(field, formValues, fields)
  );
}
