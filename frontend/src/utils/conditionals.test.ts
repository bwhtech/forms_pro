import { describe, it, expect, vi } from "vitest";
import type { FormField } from "@/types/formfield";
import { Fieldtype } from "@/types/FormsPro/form_field.types";
import {
  Actions,
  ConditionalOperators,
} from "@/types/conditional-render.types";

// The real registry imports Vue components, which the node test environment
// can't load. Conditionals only need `isBoolean`.
vi.mock("@/config/fieldTypes", () => ({
  getFieldTypeDef: (name: Fieldtype) => ({
    isBoolean: name === Fieldtype.SWITCH || name === Fieldtype.CHECKBOX,
  }),
}));

const { shouldFieldBeVisible } = await import("./conditionals");

function sourceField(
  fieldtype: Fieldtype,
  operator: ConditionalOperators,
  value: string | number
): FormField {
  return {
    fieldname: "source",
    label: "Source",
    fieldtype,
    conditional_logic: JSON.stringify({
      conditions: [{ fieldname: "source", operator, value }],
      action: Actions.ShowField,
      target_field: "target",
    }),
  };
}

const target: FormField = {
  fieldname: "target",
  label: "Target",
  fieldtype: Fieldtype.DATA,
  hidden: true,
};

function isShown(
  fieldtype: Fieldtype,
  operator: ConditionalOperators,
  conditionValue: string | number,
  fieldValue: unknown
): boolean {
  const fields = [sourceField(fieldtype, operator, conditionValue), target];
  return shouldFieldBeVisible(target, { source: fieldValue }, fields);
}

describe("numeric conditions", () => {
  // Rating is stored as a 0..1 fraction; the builder stores condition values
  // as strings typed into a text input.
  it("matches Rating <= a string threshold", () => {
    const op = ConditionalOperators.IsLessThanOrEqualTo;
    expect(isShown(Fieldtype.RATING, op, "0.2", 0.2)).toBe(true);
    expect(isShown(Fieldtype.RATING, op, "0.2", 0)).toBe(true);
    expect(isShown(Fieldtype.RATING, op, "0.2", 0.4)).toBe(false);
  });

  it("matches Rating with the other comparison operators", () => {
    expect(
      isShown(Fieldtype.RATING, ConditionalOperators.IsLessThan, "0.6", 0.4)
    ).toBe(true);
    expect(
      isShown(Fieldtype.RATING, ConditionalOperators.IsGreaterThan, "0.6", 0.8)
    ).toBe(true);
    expect(
      isShown(
        Fieldtype.RATING,
        ConditionalOperators.IsGreaterThanOrEqualTo,
        "0.6",
        0.4
      )
    ).toBe(false);
  });

  it("matches Rating Is regardless of how the number is written", () => {
    expect(isShown(Fieldtype.RATING, ConditionalOperators.Is, "0.2", 0.2)).toBe(
      true
    );
    expect(isShown(Fieldtype.RATING, ConditionalOperators.Is, ".2", 0.2)).toBe(
      true
    );
  });

  it("matches Number fields against string condition values", () => {
    expect(
      isShown(Fieldtype.NUMBER, ConditionalOperators.IsLessThan, "10", "5")
    ).toBe(true);
    expect(isShown(Fieldtype.NUMBER, ConditionalOperators.Is, "3", 3)).toBe(
      true
    );
  });

  it("does not match when the field is empty", () => {
    const op = ConditionalOperators.IsLessThanOrEqualTo;
    expect(isShown(Fieldtype.RATING, op, "0.2", null)).toBe(false);
    expect(isShown(Fieldtype.NUMBER, op, "10", "")).toBe(false);
  });

  it("does not match when the condition value isn't numeric", () => {
    expect(
      isShown(Fieldtype.NUMBER, ConditionalOperators.IsLessThan, "abc", 1)
    ).toBe(false);
  });
});
