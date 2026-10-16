# Copyright (c) 2025, harsh@buildwithhussain.com and contributors
# For license information, please see license.txt

import json
import unittest
from types import SimpleNamespace

from forms_pro.api.submission.endpoints import (
    _coerce_field_value,
    _evaluate_conditions,
    _validate_form_response,
)
from forms_pro.forms_pro.doctype.form_field.form_field import _DISPLAY_ONLY_FIELDTYPES
from forms_pro.utils.constants import FORMS_PRO_SYSTEM_FIELDNAMES, UNSUPPORTED_FRAPPE_FIELDTYPES
from forms_pro.utils.form_generator import LINKED_FORM_FIELDOPTIONS, SUBMISSION_STATUS_FIELDOPTIONS


def _field(fieldname, fieldtype="Data", label=None, reqd=0, hidden=0, conditional_logic=None):
    """Build a minimal field stub matching what _validate_form_response expects."""
    return SimpleNamespace(
        fieldname=fieldname,
        fieldtype=fieldtype,
        label=label or fieldname.replace("_", " ").title(),
        reqd=reqd,
        hidden=hidden,
        conditional_logic=conditional_logic,
    )


def _form(*fields):
    """Build a minimal form stub."""
    return SimpleNamespace(fields=list(fields))


class TestCoerceFieldValue(unittest.TestCase):
    def test_empty_string_returns_none(self):
        self.assertIsNone(_coerce_field_value("", "Data"))

    def test_none_returns_none(self):
        self.assertIsNone(_coerce_field_value(None, "Data"))

    def test_switch_coerces_to_bool(self):
        self.assertTrue(_coerce_field_value(1, "Switch"))
        self.assertFalse(_coerce_field_value(0, "Switch"))
        self.assertFalse(_coerce_field_value("", "Switch"))

    def test_checkbox_coerces_to_bool(self):
        self.assertTrue(_coerce_field_value("true", "Checkbox"))
        self.assertFalse(_coerce_field_value(0, "Checkbox"))

    def test_number_coerces_to_float(self):
        self.assertEqual(_coerce_field_value("42", "Number"), 42.0)
        self.assertEqual(_coerce_field_value(7, "Number"), 7.0)
        self.assertEqual(_coerce_field_value("3.14", "Number"), 3.14)
        self.assertEqual(_coerce_field_value(3.14, "Number"), 3.14)

    def test_number_invalid_returns_none(self):
        self.assertIsNone(_coerce_field_value("abc", "Number"))

    def test_data_returns_string(self):
        self.assertEqual(_coerce_field_value("hello", "Data"), "hello")
        self.assertEqual(_coerce_field_value(123, "Data"), "123")


class TestEvaluateConditions(unittest.TestCase):
    def _field_map(self, *fields):
        return {f.fieldname: f for f in fields}

    def test_is_operator_match(self):
        fields = self._field_map(_field("status", "Data"))
        conditions = [{"fieldname": "status", "operator": "Is", "value": "Active"}]
        self.assertTrue(_evaluate_conditions(conditions, {"status": "Active"}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"status": "Inactive"}, fields))

    def test_is_not_operator(self):
        fields = self._field_map(_field("status", "Data"))
        conditions = [{"fieldname": "status", "operator": "Is Not", "value": "Active"}]
        self.assertTrue(_evaluate_conditions(conditions, {"status": "Inactive"}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"status": "Active"}, fields))

    def test_is_empty_operator(self):
        fields = self._field_map(_field("note", "Data"))
        conditions = [{"fieldname": "note", "operator": "Is Empty", "value": None}]
        self.assertTrue(_evaluate_conditions(conditions, {"note": ""}, fields))
        self.assertTrue(_evaluate_conditions(conditions, {"note": None}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"note": "some text"}, fields))

    def test_is_not_empty_operator(self):
        fields = self._field_map(_field("note", "Data"))
        conditions = [{"fieldname": "note", "operator": "Is Not Empty", "value": None}]
        self.assertTrue(_evaluate_conditions(conditions, {"note": "hi"}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"note": ""}, fields))

    def test_all_conditions_must_pass(self):
        """AND logic — all conditions must be true."""
        fields = self._field_map(_field("a", "Data"), _field("b", "Data"))
        conditions = [
            {"fieldname": "a", "operator": "Is", "value": "yes"},
            {"fieldname": "b", "operator": "Is", "value": "yes"},
        ]
        self.assertTrue(_evaluate_conditions(conditions, {"a": "yes", "b": "yes"}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"a": "yes", "b": "no"}, fields))

    def test_unknown_field_returns_false(self):
        conditions = [{"fieldname": "nonexistent", "operator": "Is", "value": "x"}]
        self.assertFalse(_evaluate_conditions(conditions, {}, {}))

    def test_number_condition_matches_float(self):
        """float("3.14") must equal float(3.14) — old int() coercion would have failed."""
        fields = self._field_map(_field("score", "Number"))
        conditions = [{"fieldname": "score", "operator": "Is", "value": 3.14}]
        self.assertTrue(_evaluate_conditions(conditions, {"score": "3.14"}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"score": "3.15"}, fields))

    def test_number_condition_int_vs_float_parity(self):
        """Condition value stored as int 42 must match submitted value "42"."""
        fields = self._field_map(_field("age", "Number"))
        conditions = [{"fieldname": "age", "operator": "Is", "value": 42}]
        self.assertTrue(_evaluate_conditions(conditions, {"age": "42"}, fields))

    def test_boolean_condition_type_aware(self):
        """True (bool) condition must match truthy submitted value without str() mismatch."""
        fields = self._field_map(_field("agreed", "Switch"))
        conditions = [{"fieldname": "agreed", "operator": "Is", "value": True}]
        self.assertTrue(_evaluate_conditions(conditions, {"agreed": 1}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"agreed": 0}, fields))

    def test_rating_less_than_or_equal(self):
        """Rating is a 0..1 fraction; builder condition values arrive as strings."""
        fields = self._field_map(_field("rating", "Rating"))
        conditions = [{"fieldname": "rating", "operator": "Is Less Than Or Equal To", "value": "0.2"}]
        self.assertTrue(_evaluate_conditions(conditions, {"rating": 0.2}, fields))
        self.assertTrue(_evaluate_conditions(conditions, {"rating": 0}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {"rating": 0.4}, fields))
        self.assertFalse(_evaluate_conditions(conditions, {}, fields))

    def test_number_comparison_operators(self):
        fields = self._field_map(_field("score", "Number"))

        def holds(operator, value, submitted):
            conditions = [{"fieldname": "score", "operator": operator, "value": value}]
            return _evaluate_conditions(conditions, {"score": submitted}, fields)

        self.assertTrue(holds("Is Less Than", "10", "5"))
        self.assertFalse(holds("Is Less Than", "10", "10"))
        self.assertTrue(holds("Is Greater Than", "10", 11))
        self.assertTrue(holds("Is Greater Than Or Equal To", "10", 10))
        self.assertFalse(holds("Is Less Than", "abc", 1))

    def test_text_operators(self):
        fields = self._field_map(_field("note"))

        def holds(operator, value, submitted):
            conditions = [{"fieldname": "note", "operator": operator, "value": value}]
            return _evaluate_conditions(conditions, {"note": submitted}, fields)

        self.assertTrue(holds("Contains", "LO", "hello"))
        self.assertTrue(holds("Does Not Contain", "x", "hello"))
        self.assertTrue(holds("Starts With", "he", "Hello"))
        self.assertTrue(holds("Ends With", "LO", "hello"))
        self.assertFalse(holds("Does Not End With", "lo", "hello"))

    def test_unknown_operator_does_not_match(self):
        fields = self._field_map(_field("note"))
        conditions = [{"fieldname": "note", "operator": "Bogus", "value": "x"}]
        self.assertFalse(_evaluate_conditions(conditions, {"note": "x"}, fields))


class TestValidateFormResponse(unittest.TestCase):
    def test_required_field_missing_raises(self):
        import frappe

        form = _form(_field("name", reqd=1))
        with self.assertRaises(frappe.ValidationError):
            _validate_form_response(form, {})

    def test_required_field_present_passes(self):
        form = _form(_field("name", reqd=1))
        _validate_form_response(form, {"name": "John"})  # must not raise

    def test_hidden_required_field_is_skipped(self):
        """A hidden required field must not be validated."""
        form = _form(_field("secret", reqd=1, hidden=1))
        _validate_form_response(form, {})  # must not raise

    def test_conditional_show_makes_field_required(self):
        """A field conditionally required via require_answer must be checked."""
        import frappe

        logic = json.dumps(
            {
                "target_field": "phone",
                "action": "Require Answer",
                "conditions": [{"fieldname": "contact_method", "operator": "Is", "value": "Phone"}],
            }
        )
        contact = _field("contact_method")
        phone = _field("phone", reqd=0)
        phone_with_logic = _field("contact_method", conditional_logic=logic)

        form = _form(contact, phone, phone_with_logic)
        with self.assertRaises(frappe.ValidationError):
            _validate_form_response(form, {"contact_method": "Phone", "phone": ""})

    def test_conditional_hide_skips_required_field(self):
        """A conditionally hidden field must not be validated even if reqd=1."""
        logic = json.dumps(
            {
                "target_field": "extra",
                "action": "Hide Field",
                "conditions": [{"fieldname": "flag", "operator": "Is", "value": "yes"}],
            }
        )
        flag = _field("flag")
        extra = _field("extra", reqd=1)
        flag_with_logic = _field("flag", conditional_logic=logic)

        form = _form(flag, extra, flag_with_logic)
        # extra is required but should be hidden when flag=yes — no error expected
        _validate_form_response(form, {"flag": "yes", "extra": ""})

    def test_multiple_missing_fields_all_reported(self):
        """All missing required fields should appear in the error, not just the first."""
        import frappe

        form = _form(
            _field("first_name", reqd=1),
            _field("last_name", reqd=1),
        )
        with self.assertRaises(frappe.ValidationError) as ctx:
            _validate_form_response(form, {})

        error_msg = str(ctx.exception)
        self.assertIn("First Name", error_msg)
        self.assertIn("Last Name", error_msg)

    def test_required_field_with_zero_passes(self):
        """Numeric 0 is a valid value and must not trigger a required-field error."""
        form = _form(_field("score", fieldtype="Number", reqd=1))
        _validate_form_response(form, {"score": 0})  # must not raise

    def test_required_field_with_false_passes(self):
        """Boolean False (unchecked Switch) is a valid value for a required field."""
        form = _form(_field("agreed", fieldtype="Switch", reqd=1))
        _validate_form_response(form, {"agreed": False})  # must not raise


class TestConstants(unittest.TestCase):
    def test_system_fieldnames_derived_from_form_generator(self):
        """Constants must stay in sync with form_generator field definitions."""
        self.assertIn(SUBMISSION_STATUS_FIELDOPTIONS["fieldname"], FORMS_PRO_SYSTEM_FIELDNAMES)
        self.assertIn(LINKED_FORM_FIELDOPTIONS["fieldname"], FORMS_PRO_SYSTEM_FIELDNAMES)

    def test_unsupported_fieldtypes_contains_layout_types(self):
        for fieldtype in ("Section Break", "Column Break", "Tab Break", "Fold"):
            self.assertIn(fieldtype, UNSUPPORTED_FRAPPE_FIELDTYPES)

    def test_unsupported_fieldtypes_contains_non_input_types(self):
        for fieldtype in ("HTML", "Button", "Barcode", "Dynamic Link"):
            self.assertIn(fieldtype, UNSUPPORTED_FRAPPE_FIELDTYPES)


class TestHeadingFieldValidation(unittest.TestCase):
    def test_all_heading_levels_skipped_even_when_required(self):
        """reqd=1 heading fields must never trigger a validation error."""
        for fieldtype in ("Heading 1", "Heading 2", "Heading 3"):
            with self.subTest(fieldtype=fieldtype):
                form = _form(_field("h", fieldtype=fieldtype, reqd=1))
                _validate_form_response(form, {})  # must not raise

    def test_required_data_field_still_validated_when_heading_present(self):
        """Heading fields being skipped must not suppress validation of adjacent required fields."""
        import frappe

        form = _form(
            _field("section_title", fieldtype="Heading 1", reqd=1),
            _field("full_name", fieldtype="Data", reqd=1),
        )
        with self.assertRaises(frappe.ValidationError) as ctx:
            _validate_form_response(form, {})
        self.assertIn("Full Name", str(ctx.exception))

    def test_display_only_fieldtypes_contains_all_heading_levels(self):
        for fieldtype in ("Heading 1", "Heading 2", "Heading 3"):
            self.assertIn(fieldtype, _DISPLAY_ONLY_FIELDTYPES)


class TestPageBreakValidation(unittest.TestCase):
    def test_page_break_in_display_only_fieldtypes(self):
        self.assertIn("Page Break", _DISPLAY_ONLY_FIELDTYPES)

    def test_page_break_skipped_even_when_required(self):
        """reqd=1 Page Break must never trigger a validation error."""
        form = _form(_field("pb", fieldtype="Page Break", reqd=1))
        _validate_form_response(form, {})  # must not raise

    def test_required_data_field_still_validated_when_page_break_present(self):
        """Page Break being skipped must not suppress validation of adjacent required fields."""
        import frappe

        form = _form(
            _field("step_two", fieldtype="Page Break"),
            _field("full_name", fieldtype="Data", reqd=1),
        )
        with self.assertRaises(frappe.ValidationError) as ctx:
            _validate_form_response(form, {})
        self.assertIn("Full Name", str(ctx.exception))


class TestPageBreakProperties(unittest.TestCase):
    def test_page_break_stores_value_is_false(self):
        """Page Break maps to Tab Break which is a no_value_field — stores_value must be False."""
        from frappe.model import no_value_fields

        from forms_pro.forms_pro.doctype.form_field.form_field import FORM_TO_FRAPPE_FIELDTYPE

        mapped = FORM_TO_FRAPPE_FIELDTYPE["Page Break"]
        self.assertIn(mapped["fieldtype"], no_value_fields)

    def test_page_break_frappe_fieldtype_is_tab_break(self):
        from forms_pro.forms_pro.doctype.form_field.form_field import FORM_TO_FRAPPE_FIELDTYPE

        self.assertEqual(FORM_TO_FRAPPE_FIELDTYPE["Page Break"]["fieldtype"], "Tab Break")
