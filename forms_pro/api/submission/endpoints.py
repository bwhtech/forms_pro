import json
from operator import ge, gt, le, lt
from typing import Any

import frappe
from frappe import _
from frappe.share import add_docshare

from forms_pro.forms_pro.doctype.form.form import Form
from forms_pro.forms_pro.doctype.form_field.form_field import _DISPLAY_ONLY_FIELDTYPES
from forms_pro.utils.form_generator import SubmissionStatus
from forms_pro.utils.permissions import require_permission

from .schema import UserSubmissionResponse

_NUMERIC_FIELDTYPES = ("Number", "Rating")


def _coerce_field_value(value: Any, fieldtype: str) -> Any:
    """Coerce a submitted value to its comparable type, matching frontend conditionals.ts logic."""
    if value is None or value == "":
        return None
    # Matches isBoolean types in the frontend registry (Switch, Checkbox)
    if fieldtype in ("Switch", "Checkbox"):
        return bool(value)
    # Rating is stored as a 0..1 fraction, so it compares numerically too
    if fieldtype in _NUMERIC_FIELDTYPES:
        return _to_number(value)
    return str(value)


def _to_number(value: Any) -> float | None:
    """Convert a value to float, or None if it isn't numeric (bools excluded)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _compare_numbers(actual: Any, expected: Any, compare) -> bool:
    a, b = _to_number(actual), _to_number(expected)
    if a is None or b is None:
        return False
    return compare(a, b)


def _condition_holds(operator: str, actual: Any, expected: Any) -> bool:
    """Evaluate a single operator, mirroring evaluateCondition in conditionals.ts."""
    text = "" if actual is None else str(actual).lower()
    needle = "" if expected is None else str(expected).lower()
    match operator:
        case "Is":
            return actual == expected
        case "Is Not":
            return actual != expected
        case "Is Empty":
            return actual is None or actual == ""
        case "Is Not Empty":
            return actual is not None and actual != ""
        case "Is Set":
            return actual is not None
        case "Contains":
            return actual is not None and needle in text
        case "Does Not Contain":
            return actual is None or needle not in text
        case "Starts With":
            return actual is not None and text.startswith(needle)
        case "Does Not Start With":
            return actual is None or not text.startswith(needle)
        case "Ends With":
            return actual is not None and text.endswith(needle)
        case "Does Not End With":
            return actual is None or not text.endswith(needle)
        case "Is Less Than":
            return _compare_numbers(actual, expected, lt)
        case "Is Less Than Or Equal To":
            return _compare_numbers(actual, expected, le)
        case "Is Greater Than":
            return _compare_numbers(actual, expected, gt)
        case "Is Greater Than Or Equal To":
            return _compare_numbers(actual, expected, ge)
    return False


def _evaluate_conditions(conditions: list[dict], form_data: dict, field_map: dict) -> bool:
    """Evaluate AND-joined conditions against submitted form data."""
    for condition in conditions:
        fieldname = condition.get("fieldname")
        operator = condition.get("operator")
        expected = condition.get("value")
        field = field_map.get(fieldname)
        if not field:
            return False
        actual = _coerce_field_value(form_data.get(fieldname), field.fieldtype)
        # Coerce the condition's expected value through the same function so
        # types match — avoids str(True)=="True" vs "true" mismatches and
        # str(3.0)=="3.0" vs "3" mismatches for Number fields.
        expected_coerced = _coerce_field_value(expected, field.fieldtype)
        if not _condition_holds(operator, actual, expected_coerced):
            return False
    return True


def _validate_form_response(form: "Form", form_data: dict) -> None:
    """
    Validate required and conditionally-required fields server-side.

    Mirrors the shouldFieldBeVisible / shouldFieldBeRequired logic in
    frontend/src/utils/conditionals.ts so that direct API calls cannot
    bypass frontend validation.
    """
    field_map = {f.fieldname: f for f in form.fields}
    errors: list[str] = []

    for field in form.fields:
        if field.fieldtype in _DISPLAY_ONLY_FIELDTYPES:
            continue
        is_visible = not field.hidden
        is_required = bool(field.reqd)

        for other in form.fields:
            if not other.conditional_logic:
                continue
            try:
                logic = json.loads(other.conditional_logic)
            except (json.JSONDecodeError, TypeError):
                continue

            if logic.get("target_field") != field.fieldname:
                continue

            conditions_met = _evaluate_conditions(logic.get("conditions", []), form_data, field_map)
            if conditions_met:
                action = logic.get("action")
                if action == "Show Field":
                    is_visible = True
                elif action == "Hide Field":
                    is_visible = False
                elif action == "Require Answer":
                    is_required = True

        if not is_visible:
            continue

        value = form_data.get(field.fieldname)
        is_empty = value is None or value == "" or value == []
        if is_required and is_empty:
            errors.append(_("{0} is required").format(field.label))

    if errors:
        frappe.throw("\n".join(errors), frappe.ValidationError)


@frappe.whitelist(allow_guest=True)  # nosemgrep: frappe-semgrep-rules.rules.security.guest-whitelisted-method
def submit_form_response(
    form_id: str,
    form_data: list[dict],
    submission_status: str = SubmissionStatus.SUBMITTED.value,
) -> str:
    """
    Submit a form response

    Args:
        form_id: The ID of the form
        form_data: The data of the form
        submission_status: The status of the submission: Default is `Submitted`

    Returns:
        The name of the submission
    """
    try:
        status = SubmissionStatus(submission_status)
    except ValueError:
        frappe.throw(
            _("Invalid submission status: {0}").format(submission_status),
            frappe.ValidationError,
        )

    try:
        form: Form = frappe.get_doc("Form", form_id)
        linked_doctype = form.linked_doctype

        if not form.is_published:
            frappe.throw(
                _("This form is un-published, so responses are no longer being collected."),
                frappe.PermissionError,
            )

        form_data_dict = {item["fieldname"]: item["value"] for item in form_data}

        # Whitelist to declared form fields only — prevents injecting system fields
        # like owner, docstatus, etc. into the linked DocType.
        allowed_fieldnames = {f.fieldname for f in form.fields}
        form_data_dict = {k: v for k, v in form_data_dict.items() if k in allowed_fieldnames}

        if status == SubmissionStatus.SUBMITTED:
            _validate_form_response(form, form_data_dict)

        submission = frappe.new_doc(linked_doctype)
        for fieldname, value in form_data_dict.items():
            # JSON fields (e.g. Multiselect) must be stored as a JSON string,
            # but Frappe deserializes request body arrays into Python lists before
            # we get here — serialize them back.
            if isinstance(value, list):
                value = json.dumps(value)
            submission.set(fieldname, value)

        submission.fp_linked_form = form_id
        submission.fp_submission_status = status.value
        submission.insert(ignore_permissions=True, ignore_mandatory=True)

        # Share the submission with the owner
        add_docshare(
            doctype=linked_doctype,
            name=submission.name,
            user=frappe.session.user,
            read=1,
            write=1,
            flags={
                "ignore_share_permission": True,
            },
        )

        return submission.name
    except Exception:
        raise


@frappe.whitelist()
@require_permission("Form", "read", param="form_id")
def get_user_submissions(form_id: str) -> list[UserSubmissionResponse]:
    """
    Get the submissions for a user

    Args:
        form_id: The ID of the form

    Returns:
        A list of submissions for the user
    """
    form: Form = frappe.get_doc("Form", form_id)
    linked_doctype = form.linked_doctype

    submissions = frappe.get_all(
        doctype=linked_doctype,
        filters={"owner": frappe.session.user},
        fields=["name", "creation", "modified", "fp_submission_status", "owner"],
        order_by="creation",
    )

    return [UserSubmissionResponse.model_validate(submission).model_dump() for submission in submissions]


@frappe.whitelist()
def get_submission_response(submission_id: str, doctype: str) -> dict[str, Any]:
    """
    Get a full submission response by ID.
    This API checks if the user is the team member / the form is shared with the user.

    Args:
        submission_id: The name/ID of the submission document
        doctype: The submission's doctype (linked_doctype of the form)

    Returns:
        The submission document as a dict
    """
    linked_form = frappe.db.get_value(doctype, submission_id, "fp_linked_form")
    if not linked_form:
        frappe.throw(_("Submission not found."), frappe.DoesNotExistError)

    form_data = frappe.db.get_value("Form", linked_form, ["linked_team_id", "linked_doctype"], as_dict=True)

    if form_data.linked_doctype != doctype:
        frappe.throw(_("Invalid doctype for this submission."), frappe.PermissionError)

    if not frappe.has_permission(
        doctype="FP Team", ptype="write", doc=form_data.linked_team_id, user=frappe.session.user
    ):
        frappe.throw(
            _("You do not have permission to read this form's submissions."),
            frappe.PermissionError,
        )

    submission = frappe.get_doc(form_data.linked_doctype, submission_id)
    return submission.as_dict()


@frappe.whitelist()
def get_submission(submission_doctype: str, submission_name: str) -> dict[str, Any]:
    """
    Get a submission by name. Used usually by the form owner to view the submission details.

    Args:
        submission_name: The name of the submission

    Returns:
        The submission
    """
    submission = frappe.get_doc(submission_doctype, submission_name)

    if not frappe.has_permission(doctype=submission.doctype, ptype="read", doc=submission.name):
        frappe.throw(
            _("You do not have permission to read this submission."),
            frappe.PermissionError,
        )

    return submission.as_dict()


@frappe.whitelist()
@require_permission("Form", "read", param="form_id")
def get_all_submissions(form_id: str) -> list[UserSubmissionResponse]:
    """
    Get all submissions for a form

    Args:
        form_id: The ID of the form

    Returns:
        A list of submissions for the form
    """
    form: Form = frappe.get_doc("Form", form_id)
    linked_doctype = form.linked_doctype

    submissions = frappe.get_all(
        doctype=linked_doctype,
        fields=["name", "creation", "modified", "fp_submission_status", "owner"],
        filters={"fp_submission_status": "Submitted"},
        order_by="creation",
    )

    return [UserSubmissionResponse.model_validate(submission).model_dump() for submission in submissions]
