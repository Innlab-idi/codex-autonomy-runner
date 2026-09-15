"""Repository-local composition of the reviewed runtime primitives.

This module owns ordering only.  Durable refresh and semantic coordination are
injected boundaries; it neither parses consumer documents nor selects work.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Callable, Optional, Protocol, Tuple

from .execution_baseline import (
    ExecutionBaselineStatus, IntendedRefObservation, resolve_execution_baseline,
)
from .existing_work import ExistingWorkDiscovery
from .invocation_contract import InvocationOutcome, InvocationRequest, InvocationResult
from .runtime_durable_state import (
    DirectedFinalizerContext, DurableStateTransport, FinalizerPrepassResult,
    FinalizerPrepassStatus, run_finalizer_prepass,
)
from .finalizer_merge import ProtectedMergeTransport
from .finalizer_publication import FinalizerPublicationTransport
from .runtime_invocation import RuntimeInvocationContext, RuntimeInvocationReport, run_repository_invocation
from .runtime_publication import (
    PublicationTransport, RuntimePublicationRequest,
    RuntimePublicationStatus, publish_validated_worker_work,
)
from .runtime_worker import (
    CheckDeclaration, CheckExecutor, CheckKind, RuntimeWorkerStatus,
    WorkerExecutor, run_runtime_worker,
)


class WakeStage(Enum):
    DURABLE_REFRESH = "durable_refresh"
    FINALIZER_PREPASS = "finalizer_prepass"
    POST_FINALIZER_REFRESH = "post_finalizer_refresh"
    COORDINATOR = "coordinator"
    WORKER = "worker"
    PUBLICATION = "publication"


class CoordinatorDisposition(Enum):
    NO_OP = "no_op"
    BLOCKED = "blocked"
    TRANSITION = "transition"


@dataclass(frozen=True)
class RecoveryEvidence:
    """Caller-supplied, sanitized recovery marker; no retry authority.

    ``continuation_proven`` is retained only as diagnostic input.  The reviewed
    primitives expose no generic continuation operation, so it never permits a
    new worker attempt in this wake.
    """

    possible_prior_worker: bool = False
    continuation_proven: bool = False


@dataclass(frozen=True)
class DurableWakeState:
    """Fresh durable facts, already obtained by a narrow caller-owned seam."""

    coordinator_observation: object = field(repr=False, compare=False)
    finalizer_context: Optional[DirectedFinalizerContext] = field(default=None, repr=False, compare=False)
    durable_transport: Optional[DurableStateTransport] = field(default=None, repr=False, compare=False)
    finalizer_publication_transport: Optional[FinalizerPublicationTransport] = field(default=None, repr=False, compare=False)
    finalizer_merge_transport: Optional[ProtectedMergeTransport] = field(default=None, repr=False, compare=False)
    recovery: RecoveryEvidence = RecoveryEvidence()


class DurableWakeRefresher(Protocol):
    def refresh(self, context: RuntimeInvocationContext) -> DurableWakeState: ...


@dataclass(frozen=True)
class AuthorizedTransition:
    """One caller-authorized transition containing data, never HOST capability."""

    checkpoint_id: str
    intended_ref: IntendedRefObservation
    existing_work: ExistingWorkDiscovery
    permitted_paths: Tuple[str, ...]
    checks: Tuple[CheckDeclaration, ...]
    branch: Optional[str]
    attempt_id: str
    instructions: object = field(default=None, repr=False, compare=False)
    publication_request: Optional[RuntimePublicationRequest] = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class WakeHostServices:
    """Narrow HOST-owned execution and publication services for one wake."""

    worker_executor: WorkerExecutor = field(repr=False, compare=False)
    check_executor: Optional[CheckExecutor] = field(default=None, repr=False, compare=False)
    publication_transport: Optional[PublicationTransport] = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CoordinatorResponse:
    disposition: CoordinatorDisposition
    transition: Optional[AuthorizedTransition] = field(default=None, repr=False, compare=False)


class WakeCoordinator(Protocol):
    def coordinate(self, observation: object) -> CoordinatorResponse: ...


@dataclass(frozen=True)
class WakeEvidence:
    """Stable in-memory evidence with no paths, prose, commands, or secrets."""

    duration: float
    stages: Tuple[WakeStage, ...]
    refresh_count: int
    worker_attempts: int
    publication_attempted: bool
    finalizer_status: Optional[FinalizerPrepassStatus]
    worker_status: Optional[RuntimeWorkerStatus]
    publication_status: Optional[RuntimePublicationStatus]
    outcome: InvocationOutcome


@dataclass(frozen=True)
class RuntimeWakeReport:
    invocation: RuntimeInvocationReport
    evidence: WakeEvidence

    @property
    def result(self) -> InvocationResult:
        return self.invocation.result


def _valid_transition(value: object, context: RuntimeInvocationContext,
                      host: object) -> bool:
    """Reject all side-effect-free structural incoherence before RUNTIME-03."""
    if not (
        isinstance(value, AuthorizedTransition)
        and isinstance(value.checkpoint_id, str) and bool(value.checkpoint_id)
        and isinstance(value.intended_ref, IntendedRefObservation)
        and value.intended_ref.ref == context.intended_ref
        and isinstance(value.existing_work, ExistingWorkDiscovery)
        and isinstance(value.permitted_paths, tuple)
        and value.permitted_paths
        and all(isinstance(path, str) and path for path in value.permitted_paths)
        and len(set(value.permitted_paths)) == len(value.permitted_paths)
        and isinstance(value.checks, tuple)
        and all(isinstance(check, CheckDeclaration) for check in value.checks)
        and len({check.check_id for check in value.checks}) == len(value.checks)
        and isinstance(value.attempt_id, str) and bool(value.attempt_id)
        and isinstance(value.publication_request, RuntimePublicationRequest)
        and isinstance(host, WakeHostServices)
        and callable(getattr(host.worker_executor, "execute", None))
        and (not value.checks or callable(getattr(host.check_executor, "execute", None)))
        and all(callable(getattr(host.publication_transport, name, None)) for name in (
            "observe_remote_branch", "push_non_force", "create_pull_request",
            "observe_pull_request",
        ))
    ):
        return False
    request = value.publication_request
    if (
        request.repository.resolve() != context.inspection.root
        or request.permitted_paths != value.permitted_paths
        or not isinstance(request.required_publication_check_ids, tuple)
        or len(set(request.required_publication_check_ids)) != len(request.required_publication_check_ids)
    ):
        return False
    publication_ids = {
        check.check_id for check in value.checks if check.kind is CheckKind.PUBLICATION
    }
    if not set(request.required_publication_check_ids).issubset(publication_ids):
        return False
    baseline = resolve_execution_baseline(
        value.checkpoint_id, InvocationRequest(context.inspection.root, context.intended_ref),
        value.intended_ref, value.existing_work,
    )
    if baseline.status not in (ExecutionBaselineStatus.NEW_WORK, ExecutionBaselineStatus.EXISTING_WORK):
        return False
    resolved_branch = baseline.baseline.resolved_ref
    if baseline.status is ExecutionBaselineStatus.NEW_WORK:
        # New work must name a distinct prepared branch: using the intended ref
        # would consume the worker attempt on a branch RUNTIME-04 cannot safely
        # treat as new work.
        if not isinstance(value.branch, str) or not value.branch or value.branch == resolved_branch:
            return False
    elif value.branch != resolved_branch:
        return False
    return request.branch == value.branch


def run_repository_wake(
    request: InvocationRequest,
    refresher: DurableWakeRefresher,
    coordinator: WakeCoordinator,
    host_services: WakeHostServices,
    *,
    clock: Callable[[], float] = monotonic,
    execution_profile: object = None,
) -> RuntimeWakeReport:
    """Run one synchronous local wake, under RUNTIME-01's lock envelope.

    An uncertain possible prior worker is deliberately terminal.  Current
    primitives do not provide sufficient generic proof for safely continuing a
    partially published operation, so this boundary never retries it.
    """
    started = clock()
    stages = []
    refresh_count = worker_attempts = 0
    publication_attempted = False
    finalizer_status = worker_status = publication_status = None

    def action(context: RuntimeInvocationContext) -> InvocationResult:
        nonlocal refresh_count, worker_attempts, publication_attempted
        nonlocal finalizer_status, worker_status, publication_status
        finalizer_transition_completed = False

        def refresh(stage: WakeStage) -> DurableWakeState:
            nonlocal refresh_count
            stages.append(stage)
            refresh_count += 1
            observed = refresher.refresh(context)
            if not isinstance(observed, DurableWakeState):
                raise RuntimeError("Invalid durable refresh")
            return observed

        state = refresh(WakeStage.DURABLE_REFRESH)
        if state.recovery.possible_prior_worker:
            return InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)

        stages.append(WakeStage.FINALIZER_PREPASS)
        if state.finalizer_context is None:
            prepass = FinalizerPrepassResult(
                FinalizerPrepassStatus.NO_FINALIZATION,
                InvocationResult(InvocationOutcome.NO_OP), False, None,
            )
        elif state.durable_transport is None:
            return InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE)
        else:
            prepass = run_finalizer_prepass(
                state.finalizer_context, state.durable_transport,
                publication_transport=state.finalizer_publication_transport,
                merge_transport=state.finalizer_merge_transport,
            )
        finalizer_status = prepass.status
        if prepass.invocation_result.outcome is not InvocationOutcome.NO_OP and not prepass.refresh_required:
            return prepass.invocation_result
        if prepass.refresh_required:
            finalizer_transition_completed = (
                prepass.invocation_result.outcome
                is InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION
            )
            state = refresh(WakeStage.POST_FINALIZER_REFRESH)
            if state.recovery.possible_prior_worker:
                return InvocationResult(InvocationOutcome.INTERRUPTED_OR_UNTRUSTWORTHY)

        stages.append(WakeStage.COORDINATOR)
        response = coordinator.coordinate(state.coordinator_observation)
        if not isinstance(response, CoordinatorResponse):
            raise RuntimeError("Invalid coordinator response")
        if response.disposition is CoordinatorDisposition.NO_OP and response.transition is None:
            return InvocationResult(
                InvocationOutcome.COMPLETED_AUTHORIZED_TRANSITION
                if finalizer_transition_completed else InvocationOutcome.NO_OP
            )
        if response.disposition is CoordinatorDisposition.BLOCKED and response.transition is None:
            return InvocationResult(InvocationOutcome.BLOCKED)
        if response.disposition is not CoordinatorDisposition.TRANSITION or not _valid_transition(response.transition, context, host_services):
            return InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE)

        transition = response.transition
        stages.append(WakeStage.WORKER)
        worker_attempts = 1
        worker = run_runtime_worker(
            InvocationRequest(context.inspection.root, context.intended_ref),
            transition.checkpoint_id, transition.intended_ref, transition.existing_work,
            host_services.worker_executor, permitted_paths=transition.permitted_paths,
            checks=transition.checks, check_executor=host_services.check_executor,
            new_branch=transition.branch, attempt_id=transition.attempt_id,
            instructions=transition.instructions, execution_profile=context.execution_profile,
        )
        worker_status = worker.status
        if worker.status is not RuntimeWorkerStatus.VALIDATED:
            return worker.invocation_result or InvocationResult(InvocationOutcome.RUNTIME_EXECUTION_FAILURE)

        stages.append(WakeStage.PUBLICATION)
        publication_attempted = True
        publication = publish_validated_worker_work(
            transition.publication_request, worker, host_services.publication_transport
        )
        publication_status = publication.status
        return publication.invocation_result

    invocation = run_repository_invocation(request, action, execution_profile=execution_profile)
    duration = clock() - started
    evidence = WakeEvidence(
        duration if duration >= 0 else 0.0, tuple(stages), refresh_count,
        worker_attempts, publication_attempted, finalizer_status, worker_status,
        publication_status, invocation.result.outcome,
    )
    return RuntimeWakeReport(invocation, evidence)
