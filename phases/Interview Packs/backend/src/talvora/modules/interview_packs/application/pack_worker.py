"""Fenced Durable Worker for Automated Draft Interview Pack Generation (P15)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from talvora.modules.identity.domain.models import User
from talvora.modules.identity.domain.roles import get_capabilities_for_role
from talvora.modules.interview_packs.application.ports import (
    InterviewContextPort,
    StructuredPackAIProvider,
)
from talvora.modules.interview_packs.application.service import InterviewPackService
from talvora.modules.operations.domain.models import JobOperation, OperationStatus
from talvora.modules.organizations.domain.models import Membership
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.domain.clock import Clock, SystemClock
from talvora.shared.infrastructure.uow import UnitOfWork
from talvora.shared.transport.problems import ProblemException
from talvora.workers.task_handlers import TaskExecutionManager


class PackGenerationWorker:
    """Executes `interview_packs.generate_draft` background operations under lease fencing."""

    def __init__(
        self,
        ai_provider: StructuredPackAIProvider | None = None,
        interview_port: InterviewContextPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.ai_provider = ai_provider
        self.interview_port = interview_port
        self.clock = clock or SystemClock()
        self.manager = TaskExecutionManager(clock=self.clock)

    def execute_operation(
        self,
        session: Session,
        tenant_id: UUID,
        operation_id: UUID,
        worker_id: str = "pack-worker-1",
    ) -> JobOperation:
        with UnitOfWork(session=session, tenant_id=tenant_id, clock=self.clock) as uow:
            op, token = self.manager.claim_job(uow.session, tenant_id, operation_id, worker_id)
            if op.status != OperationStatus.RUNNING.value:
                uow.commit()
                return op

            self.manager.heartbeat(uow.session, tenant_id, operation_id, token)
            payload = dict(op.payload or {})
            interview_id = UUID(str(payload["interview_id"]))
            actor_user_id = UUID(str(payload["actor_id"]))
            reason = str(payload.get("reason") or "BOOKING_EVENT")

            user = uow.session.scalar(select(User).where(User.id == actor_user_id))
            membership = uow.session.scalar(
                select(Membership).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == actor_user_id,
                )
            )
            if user is None or membership is None:
                final_op = self.manager.finalize_job(
                    session=uow.session,
                    tenant_id=tenant_id,
                    job_id=operation_id,
                    fencing_token=token,
                    status=OperationStatus.FAILED,
                    error_detail={
                        "code": "ACTOR_NOT_FOUND",
                        "detail": "Originating actor no longer active.",
                    },
                )
                uow.commit()
                return final_op

            actor = ActorContext(
                principal=Principal(
                    user_id=user.id,
                    email=user.email,
                    issuer=user.issuer,
                    subject=user.subject,
                ),
                membership=MembershipContext(
                    membership_id=membership.id,
                    organization_id=tenant_id,
                    role=membership.role,  # type: ignore[arg-type]
                    capabilities=get_capabilities_for_role(membership.role),
                ),
            )

            try:
                bundle = InterviewPackService.generate_draft_pack(
                    uow=uow,
                    actor=actor,
                    interview_id=interview_id,
                    reason=reason,
                    ai_provider=self.ai_provider,
                    interview_port=self.interview_port,
                )
                final_op = self.manager.finalize_job(
                    session=uow.session,
                    tenant_id=tenant_id,
                    job_id=operation_id,
                    fencing_token=token,
                    status=OperationStatus.SUCCEEDED,
                    result={
                        "pack_revision_id": str(bundle.revision.id),
                        "revision_number": bundle.revision.revision_number,
                    },
                )
            except ProblemException as exc:
                target_status = (
                    OperationStatus.RETRY_WAIT
                    if exc.status_code == 503
                    else OperationStatus.NEEDS_REVIEW
                )
                final_op = self.manager.finalize_job(
                    session=uow.session,
                    tenant_id=tenant_id,
                    job_id=operation_id,
                    fencing_token=token,
                    status=target_status,
                    error_detail={"code": exc.code, "detail": exc.detail},
                )

            uow.commit()
            return final_op
