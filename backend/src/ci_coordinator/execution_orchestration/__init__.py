"""Provider-observable identities derived from signed execution plans."""

from ci_coordinator.execution_orchestration.adapter_bundle import (
    MAX_TARGET_ADAPTER_FILES,
    MAX_TARGET_ADAPTER_WORKFLOWS,
    TARGET_CONTROL_FILE_PATHS,
    TargetAdapterFileBinding,
    digest_adapter_file,
    is_adapter_workflow_path,
)
from ci_coordinator.execution_orchestration.provider_signal import (
    ProviderOccurrence,
    ProviderSignal,
)
from ci_coordinator.execution_orchestration.target_registry import (
    CONTROL_INVOCATION_JOB_ID,
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    MAX_TARGET_SERVICE_PROFILE_IDS,
    ExecutionKind,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
    TrustedExecutionTarget,
)
from ci_coordinator.execution_orchestration.target_registry_codec import (
    MAX_TARGET_EXECUTION_REGISTRY_BYTES,
    TARGET_EXECUTION_REGISTRY_PATH,
    TARGET_EXECUTION_REGISTRY_SCHEMA_VERSION,
    parse_target_execution_registry,
)

__all__ = [
    "CONTROL_INVOCATION_JOB_ID",
    "LOCAL_PLAN_REQUEST_WORKFLOW_PATH",
    "LOCAL_PLAN_REQUEST_WORKFLOW_REF",
    "MAX_TARGET_ADAPTER_FILES",
    "MAX_TARGET_ADAPTER_WORKFLOWS",
    "MAX_TARGET_EXECUTION_REGISTRY_BYTES",
    "MAX_TARGET_SERVICE_PROFILE_IDS",
    "TARGET_CONTROL_FILE_PATHS",
    "TARGET_EXECUTION_REGISTRY_PATH",
    "TARGET_EXECUTION_REGISTRY_SCHEMA_VERSION",
    "ExecutionKind",
    "ProviderOccurrence",
    "ProviderSignal",
    "TargetAdapterFileBinding",
    "TargetExecutionRegistry",
    "TargetJobBinding",
    "TargetProfileBinding",
    "TargetWorkflowBinding",
    "TrustedExecutionTarget",
    "digest_adapter_file",
    "is_adapter_workflow_path",
    "parse_target_execution_registry",
]
