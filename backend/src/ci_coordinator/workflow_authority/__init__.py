"""Fail-closed workflow authority source evidence."""

from .codec import (
    decode_manifest,
    decode_source_binding,
    encode_manifest,
    encode_source_binding,
)
from .errors import WorkflowAuthorityError
from .evidence import WorkflowAuthorityEvidence
from .git_objects import git_blob_oid, git_tree_content, git_tree_oid, git_tree_sort_key
from .model import (
    WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
    WORKFLOW_SOURCE_BINDING_SCHEMA,
    GitTreeChild,
    RetainedBlobObject,
    RetainedTreeObject,
    WorkflowAuthorityManifest,
    WorkflowAuthorityRepository,
    WorkflowCommitRequest,
    WorkflowManifestEntry,
    WorkflowSourceBinding,
)
from .outcomes import (
    WorkflowAuthorityFailureReason,
    WorkflowAuthorityReadOutcome,
    WorkflowAuthorityUnavailable,
)

__all__ = [
    "WORKFLOW_AUTHORITY_MANIFEST_SCHEMA",
    "WORKFLOW_SOURCE_BINDING_SCHEMA",
    "GitTreeChild",
    "RetainedBlobObject",
    "RetainedTreeObject",
    "WorkflowAuthorityError",
    "WorkflowAuthorityEvidence",
    "WorkflowAuthorityFailureReason",
    "WorkflowAuthorityManifest",
    "WorkflowAuthorityReadOutcome",
    "WorkflowAuthorityRepository",
    "WorkflowAuthorityUnavailable",
    "WorkflowCommitRequest",
    "WorkflowManifestEntry",
    "WorkflowSourceBinding",
    "decode_manifest",
    "decode_source_binding",
    "encode_manifest",
    "encode_source_binding",
    "git_blob_oid",
    "git_tree_content",
    "git_tree_oid",
    "git_tree_sort_key",
]
