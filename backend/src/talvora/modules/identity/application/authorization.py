"""Authorization Service for Role-Based Capability Evaluation."""

from uuid import UUID

from talvora.modules.identity.domain.roles import Capability, get_capabilities_for_role
from talvora.shared.application.actor import ActorContext
from talvora.shared.transport.problems import ProblemException


class AuthorizationService:
    """Evaluates actor permissions with multi-tenant defense and resource concealment."""

    @staticmethod
    def has_capability(actor: ActorContext, capability: Capability | str) -> bool:
        """Check if actor possesses capability within active tenant."""
        if not actor.is_authenticated or actor.membership is None:
            return False
        cap_val = capability.value if isinstance(capability, Capability) else str(capability)
        role_caps = get_capabilities_for_role(actor.membership.role)
        return cap_val in role_caps or cap_val in actor.membership.capabilities

    @classmethod
    def enforce_capability(
        cls,
        actor: ActorContext,
        capability: Capability | str,
        target_tenant_id: UUID | None = None,
    ) -> None:
        """Enforces capability check with resource concealment.

        1. Unauthenticated actors -> 401 UNAUTHORIZED
        2. Cross-tenant queries -> 404 NOT_FOUND (Resource Concealment)
        3. Missing capability within active tenant -> 403 FORBIDDEN
        """
        if not actor.is_authenticated:
            raise ProblemException(
                status_code=401,
                title="Unauthorized",
                code="UNAUTHENTICATED",
                detail="Valid session authentication is required to access this resource.",
            )

        # Resource Concealment: If querying across tenant boundary, return 404, never 403
        if target_tenant_id is not None and (
            actor.tenant_id is None or actor.tenant_id != target_tenant_id
        ):
            raise ProblemException(
                status_code=404,
                title="Resource Not Found",
                code="RESOURCE_NOT_FOUND",
                detail="The requested resource was not found.",
            )

        if not cls.has_capability(actor, capability):
            cap_val = capability.value if isinstance(capability, Capability) else str(capability)
            raise ProblemException(
                status_code=403,
                title="Forbidden",
                code="CAPABILITY_DENIED",
                detail=f"Actor lacks required capability '{cap_val}' in this organization.",
            )
