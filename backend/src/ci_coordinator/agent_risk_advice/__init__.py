"""Pure admission of untrusted additive agent-risk evidence."""

from ci_coordinator.agent_risk_advice.admission import admit_advice, build_advice_input
from ci_coordinator.agent_risk_advice.model import (
    AGENT_ADVICE_SCHEMA_VERSION,
    MAX_AGENT_ADVICE_OUTPUT_BYTES,
    AdmittedAdvice,
    AdviceAdmission,
    AdviceAuditMetadata,
    AdviceDepthIncrease,
    AdviceEvaluationEvidence,
    AdviceExecutionEnvelope,
    AdviceInputPackage,
    AdviceRiskFinding,
    AgentAdvice,
    RejectedAdvice,
)

__all__ = [
    "AGENT_ADVICE_SCHEMA_VERSION",
    "MAX_AGENT_ADVICE_OUTPUT_BYTES",
    "AdmittedAdvice",
    "AdviceAdmission",
    "AdviceAuditMetadata",
    "AdviceDepthIncrease",
    "AdviceEvaluationEvidence",
    "AdviceExecutionEnvelope",
    "AdviceInputPackage",
    "AdviceRiskFinding",
    "AgentAdvice",
    "RejectedAdvice",
    "admit_advice",
    "build_advice_input",
]
