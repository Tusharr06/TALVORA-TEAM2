"""Unit tests for AuthorizationService, Capabilities, and Resource Concealment."""

from uuid import uuid4

import pytest

from talvora.modules.identity.application.authorization import AuthorizationService
from talvora.modules.identity.domain.roles import Capability, Role, get_capabilities_for_role
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.transport.problems import ProblemException


def test_base_role_capabilities_mapping() -> None:
    owner_caps = get_capabilities_for_role(Role.OWNER.value)
    assert Capability.MANAGE_ORGANIZATION_POLICY.value in owner_caps
    assert Capability.TRANSFER_OWNERSHIP.value in owner_caps
    assert Capability.MANAGE_MEMBERS.value in owner_caps

    admin_caps = get_capabilities_for_role(Role.ADMIN.value)
    assert Capability.MANAGE_MEMBERS.value in admin_caps
    assert Capability.MANAGE_ORGANIZATION_POLICY.value not in admin_caps
    assert Capability.TRANSFER_OWNERSHIP.value not in admin_caps

    recruiter_caps = get_capabilities_for_role(Role.RECRUITER.value)
    assert Capability.CREATE_JOB.value in recruiter_caps
    assert Capability.MANAGE_MEMBERS.value not in recruiter_caps

    interviewer_caps = get_capabilities_for_role(Role.INTERVIEWER.value)
    assert Capability.SUBMIT_FEEDBACK.value in interviewer_caps
    assert Capability.CREATE_JOB.value not in interviewer_caps


def test_unauthenticated_actor_raises_401() -> None:
    unauth = ActorContext(principal=None)
    with pytest.raises(ProblemException) as exc:
        AuthorizationService.enforce_capability(unauth, Capability.VIEW_MEMBERS)
    assert exc.value.status_code == 401
    assert exc.value.code == "UNAUTHENTICATED"


def test_cross_tenant_request_returns_404_resource_concealment() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()

    actor = ActorContext(
        principal=Principal(user_id=uuid4(), email="recruiter@a.com", issuer="iss", subject="sub"),
        membership=MembershipContext(
            membership_id=uuid4(),
            organization_id=tenant_a,
            role="OWNER",
            capabilities=get_capabilities_for_role("OWNER"),
        ),
    )

    # Calling target_tenant_id = tenant_b must return 404, NEVER 403!
    with pytest.raises(ProblemException) as exc:
        AuthorizationService.enforce_capability(
            actor=actor,
            capability=Capability.MANAGE_MEMBERS,
            target_tenant_id=tenant_b,
        )
    assert exc.value.status_code == 404
    assert exc.value.code == "RESOURCE_NOT_FOUND"


def test_missing_capability_within_same_tenant_returns_403() -> None:
    tenant = uuid4()
    actor = ActorContext(
        principal=Principal(
            user_id=uuid4(), email="panelist@test.com", issuer="iss", subject="sub"
        ),
        membership=MembershipContext(
            membership_id=uuid4(),
            organization_id=tenant,
            role="INTERVIEWER",
            capabilities=get_capabilities_for_role("INTERVIEWER"),
        ),
    )

    # Interviewer attempting to manage members within same tenant -> 403 Forbidden
    with pytest.raises(ProblemException) as exc:
        AuthorizationService.enforce_capability(
            actor=actor,
            capability=Capability.MANAGE_MEMBERS,
            target_tenant_id=tenant,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "CAPABILITY_DENIED"


def test_authorized_actor_passes_enforcement() -> None:
    tenant = uuid4()
    actor = ActorContext(
        principal=Principal(user_id=uuid4(), email="admin@test.com", issuer="iss", subject="sub"),
        membership=MembershipContext(
            membership_id=uuid4(),
            organization_id=tenant,
            role="ADMIN",
            capabilities=get_capabilities_for_role("ADMIN"),
        ),
    )

    # Admin managing members in own tenant -> no exception raised
    AuthorizationService.enforce_capability(
        actor=actor,
        capability=Capability.MANAGE_MEMBERS,
        target_tenant_id=tenant,
    )
    assert AuthorizationService.has_capability(actor, Capability.MANAGE_MEMBERS) is True
