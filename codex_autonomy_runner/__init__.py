"""Reusable execution primitives for codex-autonomy-runner."""

from .native_process import NativeProcessLaunchError, NativeProcessResult, run_native_process
from .finalizer_eligibility import (
    CurrentHeadRelationship,
    EligibleFinalizerUnit,
    FinalizerEligibility,
    FinalizerEligibilityObservation,
    FinalizerEligibilityPath,
    FinalizerEligibilityViolation,
    FinalizerTarget,
    validate_finalizer_eligibility,
)
from .finalizer_closure import (
    ALLOWLISTED_CLOSURE_PATH,
    FinalizerClosurePlan,
    FinalizerClosureResult,
    FinalizerClosureViolation,
    derive_allowlisted_closure,
    validate_allowlisted_closure,
)
from .finalizer_merge import (
    FinalizerMergeResult, FinalizerMergeStatus, FinalizerMergeViolation,
    ProtectedMergeRequest, ProtectedMergeTransport, ProtectedMergeTransportResult,
    merge_after_fresh_revalidation,
)
from .finalizer_publication import (
    FinalizerPublicationFailureStage,
    FinalizerPublicationResult,
    FinalizerPublicationStatus,
    FinalizerPublicationTransport,
    FinalizerPublicationViolation,
    NativeFinalizerPublicationTransport,
    RemoteRefObservation,
    ResolvedFinalizerPublicationDestination,
    publish_allowlisted_closure,
)
from .pre_worker_preparation import (
    PreWorkerPreparationValidation,
    PreWorkerPreparationViolation,
    validate_pre_worker_preparation,
)
from .execution_baseline import (
    ExecutionBaseline,
    ExecutionBaselineResolution,
    ExecutionBaselineStatus,
    IntendedRefObservation,
    resolve_execution_baseline,
)
from .worker_boundary_validation import (
    WorkerBoundaryValidation,
    WorkerBoundaryViolation,
    validate_worker_boundary,
)
from .changed_path_validation import ChangedPathValidation, validate_changed_paths
from .supervisor_decisions import (
    SupervisorDecisionDiscovery,
    SupervisorDecisionKind,
    SupervisorDecisionMatch,
    SupervisorDecisionObservation,
    discover_supervisor_decisions,
)
from .existing_work import (
    ExistingWorkDiscovery,
    ExistingWorkMatch,
    ExistingWorkObservation,
    discover_existing_work,
)
from .invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from .runtime_invocation import (
    ControlledInvocationFailure,
    InvocationEvidence,
    InvocationStage,
    RuntimeInvocationContext,
    RuntimeInvocationReport,
    run_repository_invocation,
)
from .runtime_durable_state import (
    DirectedFinalizerContext, DurableComment, DurablePullRequest,
    FinalizerPrepassResult, FinalizerPrepassStatus, GhDurableStateTransport,
    GhProtectedMergeTransport, adapt_durable_state, build_eligibility_observation,
    parse_decision, run_finalizer_prepass,
)
from .repository_inspection import (
    ChangedPaths,
    RepositoryInspection,
    RepositoryInspectionError,
    inspect_repository,
)
from .runtime_worker import (
    WorktreeFingerprint, WorktreeFingerprintEntry, WorktreeFingerprintError,
    fingerprint_worktree,
)
from .runtime_publication import (
    CreatePullRequestRequest, PublicationPullRequest, PublicationTransport,
    PushCompletion, RemoteBranchObservation, RuntimePublicationRequest,
    RuntimePublicationResult, RuntimePublicationStatus, publish_validated_worker_work,
)
from .runtime_wake import (
    AuthorizedTransition, CoordinatorDisposition, CoordinatorResponse,
    DurableWakeRefresher, DurableWakeState, RecoveryEvidence, RuntimeWakeReport,
    WakeHostServices,
    WakeCoordinator, WakeEvidence, WakeStage, run_repository_wake,
)

__all__ = [
    "ControlledInvocationFailure",
    "InvocationEvidence",
    "InvocationStage",
    "RuntimeInvocationContext",
    "RuntimeInvocationReport",
    "run_repository_invocation",
    "DirectedFinalizerContext",
    "DurableComment",
    "DurablePullRequest",
    "FinalizerPrepassResult",
    "FinalizerPrepassStatus",
    "GhDurableStateTransport",
    "GhProtectedMergeTransport",
    "adapt_durable_state",
    "build_eligibility_observation",
    "parse_decision",
    "run_finalizer_prepass",
    "NativeProcessLaunchError",
    "NativeProcessResult",
    "CurrentHeadRelationship",
    "EligibleFinalizerUnit",
    "FinalizerEligibility",
    "FinalizerEligibilityObservation",
    "FinalizerEligibilityPath",
    "FinalizerEligibilityViolation",
    "FinalizerTarget",
    "validate_finalizer_eligibility",
    "ALLOWLISTED_CLOSURE_PATH",
    "FinalizerClosurePlan",
    "FinalizerClosureResult",
    "FinalizerClosureViolation",
    "derive_allowlisted_closure",
    "validate_allowlisted_closure",
    "FinalizerMergeResult", "FinalizerMergeStatus", "FinalizerMergeViolation",
    "ProtectedMergeRequest", "ProtectedMergeTransport", "ProtectedMergeTransportResult",
    "merge_after_fresh_revalidation",
    "FinalizerPublicationFailureStage",
    "FinalizerPublicationResult",
    "FinalizerPublicationStatus",
    "FinalizerPublicationTransport",
    "FinalizerPublicationViolation",
    "NativeFinalizerPublicationTransport",
    "RemoteRefObservation",
    "ResolvedFinalizerPublicationDestination",
    "publish_allowlisted_closure",
    "PreWorkerPreparationValidation",
    "PreWorkerPreparationViolation",
    "validate_pre_worker_preparation",
    "ExecutionBaseline",
    "ExecutionBaselineResolution",
    "ExecutionBaselineStatus",
    "IntendedRefObservation",
    "resolve_execution_baseline",
    "WorkerBoundaryValidation",
    "WorkerBoundaryViolation",
    "validate_worker_boundary",
    "ChangedPathValidation",
    "validate_changed_paths",
    "SupervisorDecisionDiscovery",
    "SupervisorDecisionKind",
    "SupervisorDecisionMatch",
    "SupervisorDecisionObservation",
    "discover_supervisor_decisions",
    "ExistingWorkDiscovery",
    "ExistingWorkMatch",
    "ExistingWorkObservation",
    "discover_existing_work",
    "InvocationOutcome",
    "InvocationRequest",
    "InvocationResult",
    "ChangedPaths",
    "RepositoryInspection",
    "RepositoryInspectionError",
    "inspect_repository",
    "run_native_process",
    "WorktreeFingerprint", "WorktreeFingerprintEntry", "WorktreeFingerprintError",
    "fingerprint_worktree",
    "CreatePullRequestRequest", "PublicationPullRequest", "PublicationTransport",
    "PushCompletion", "RemoteBranchObservation", "RuntimePublicationRequest",
    "RuntimePublicationResult", "RuntimePublicationStatus", "publish_validated_worker_work",
    "AuthorizedTransition", "CoordinatorDisposition", "CoordinatorResponse",
    "DurableWakeRefresher", "DurableWakeState", "RecoveryEvidence", "RuntimeWakeReport",
    "WakeHostServices",
    "WakeCoordinator", "WakeEvidence", "WakeStage", "run_repository_wake",
]
