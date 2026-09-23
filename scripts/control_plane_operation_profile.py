from __future__ import annotations

from scripts.control_plane_profile_values import require_exact_json_value
from scripts.proofkit_common import as_array, as_object


def _caller(credential_plane: str, *roles: str) -> dict[str, object]:
    return {"credentialPlane": credential_plane, "requiredRoles": list(roles)}


def _administrator_callers(*roles: str) -> tuple[dict[str, object], ...]:
    return (
        _caller("human-administration", *roles),
        _caller("machine-administration", *roles),
    )


def _operation(
    operation_id: str,
    callers: tuple[dict[str, object], ...],
    *,
    provider_effect_mode: str = "forbidden",
    provider_profile: str | None = None,
    provider_evidence_input: str | None = None,
) -> dict[str, object]:
    return {
        "operationId": operation_id,
        "callerAuthorities": list(callers),
        "providerEffect": {
            "mode": provider_effect_mode,
            "permissionProfile": provider_profile,
        },
        "providerEvidenceInput": provider_evidence_input,
    }


_EXPECTED_OPERATIONS = (
    _operation(
        "activate-configuration",
        _administrator_callers("activate"),
        provider_effect_mode="required",
        provider_profile="current-read",
    ),
    _operation("audit-read", _administrator_callers("audit")),
    _operation(
        "disable-omission",
        (_caller("emergency-safety"), *_administrator_callers("override")),
    ),
    _operation("enable-omission", _administrator_callers("activate", "override")),
    _operation("export-configuration-source", _administrator_callers("configure")),
    _operation(
        "force-full-ci",
        (_caller("emergency-safety"), *_administrator_callers("override")),
    ),
    _operation(
        "read-control-plane",
        _administrator_callers("read"),
        provider_effect_mode="required",
        provider_profile="current-read",
    ),
    _operation("read-configuration-status", _administrator_callers("configure")),
    _operation("register-configuration", _administrator_callers("configure")),
    _operation(
        "repository-attestation",
        (_caller("repository-attestation"),),
        provider_effect_mode="required",
        provider_profile="current-read",
    ),
    _operation("rollback-configuration", _administrator_callers("activate")),
    _operation(
        "validate-configuration",
        _administrator_callers("configure"),
        provider_evidence_input="current-read-receipt",
    ),
)


def validate_operation_profile(
    value: object,
    *,
    admitted_plane_ids: set[str],
    admitted_roles: set[str],
) -> tuple[str, ...]:
    operations = tuple(
        as_object(row, "control-plane operation")
        for row in as_array(value, "control-plane operations")
    )
    require_exact_json_value(
        list(operations),
        list(_EXPECTED_OPERATIONS),
        "operation authority",
    )
    for operation in operations:
        for caller_value in as_array(
            operation.get("callerAuthorities"), "operation caller authorities"
        ):
            caller = as_object(caller_value, "operation caller authority")
            if caller.get("credentialPlane") not in admitted_plane_ids:
                raise ValueError("operation references an unadmitted credential plane")
            roles = as_array(caller.get("requiredRoles"), "operation caller roles")
            if not set(roles) <= admitted_roles:
                raise ValueError("operation references an unadmitted role")
        effect = as_object(operation.get("providerEffect"), "operation provider effect")
        mode = effect.get("mode")
        profile = effect.get("permissionProfile")
        if mode == "forbidden":
            valid_effect = profile is None
        elif mode == "required":
            valid_effect = isinstance(profile, str)
        else:
            valid_effect = False
        if not valid_effect:
            raise ValueError("operation provider effect is internally inconsistent")
        evidence_input = operation.get("providerEvidenceInput")
        if evidence_input not in {None, "current-read-receipt"}:
            raise ValueError("operation provider evidence input is unadmitted")
        if evidence_input is not None and mode != "forbidden":
            raise ValueError("operation cannot combine provider I/O with receipt input")
    return tuple(str(row["operationId"]) for row in operations)
