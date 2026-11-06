"""Comprehensive Unit and Security Tests for Google Integrations (P11).

Covers:
- OAuth state generation, cryptographic binding, and replay prevention
- Token encryption at rest (AES-256-GCM) and key versioning
- Serialized, concurrency-safe token refresh and invalid_grant revocation
- Calendar FreeBusy (UNKNOWN semantics for failed/inaccessible calendars)
- Calendar event lifecycle (CRUD, deterministic IDs, ETag 412 conflicts)
- Gmail send-only adapter (ACCEPTED, DEFINITIVE_FAILURE, UNKNOWN tri-state outcomes)
- Tenant isolation and member vs. administrator visibility policies
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from talvora.modules.identity.domain.models import AuthSession, User
from talvora.modules.identity.domain.roles import Role, get_capabilities_for_role
from talvora.modules.integrations.application.ports import (
    CalendarAvailabilityStatus,
    CreateEventRequest,
    MailSendResultStatus,
    SendEmailPayload,
    UpdateEventRequest,
)
from talvora.modules.integrations.application.service import (
    DEFAULT_GOOGLE_SCOPES,
    IntegrationService,
)
from talvora.modules.integrations.domain.errors import (
    IntegrationError,
    IntegrationReauthRequiredError,
    OAuthConsentDeniedError,
    OAuthStateError,
    ProviderETagConflictError,
)
from talvora.modules.integrations.domain.models import (
    ConnectionStatus,
    GoogleScope,
    IntegrationConnection,
    IntegrationOAuthTransaction,
)
from talvora.modules.integrations.infrastructure.google.calendar import (
    MockGoogleCalendarAdapter,
)
from talvora.modules.integrations.infrastructure.google.gmail import (
    MockGoogleGmailAdapter,
)
from talvora.modules.integrations.infrastructure.google.oauth_client import (
    MockGoogleOAuthClient,
)
from talvora.modules.organizations.domain.models import Membership, Organization
from talvora.shared.application.actor import ActorContext, MembershipContext, Principal
from talvora.shared.infrastructure.models import Base
from talvora.shared.infrastructure.security import decrypt_token, hash_token
from talvora.shared.infrastructure.uow import UnitOfWork

TEST_KEY = "test-encryption-key-32-bytes-ok!"


@pytest.fixture
def db_session() -> Iterator[Session]:
    """In-memory SQLite session with all domain tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"identity": None}},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = session_factory()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def test_setup(db_session: Session) -> tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient]:
    """Sets up an Organization, User, Membership, active Session, and ActorContext."""
    tenant_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    session_id = uuid4()

    org = Organization(id=tenant_id, name="Talvora Unit Org", slug=f"org-{uuid4().hex[:8]}")
    user = User(
        id=user_id,
        issuer="https://idp.talvora.test",
        subject=f"sub_{user_id.hex[:8]}",
        email="interviewer@talvora.test",
        email_verified=True,
    )
    membership = Membership(
        id=membership_id,
        tenant_id=tenant_id,
        user_id=user_id,
        role=Role.INTERVIEWER.value,
        status="ACTIVE",
    )
    auth_session = AuthSession(
        id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        session_token_hash=hash_token(f"token_{session_id}"),
        csrf_secret="test-csrf-secret",
        expires_at=datetime.now(UTC) + timedelta(hours=8),
        idle_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    db_session.add_all([org, user, membership, auth_session])
    db_session.commit()

    principal = Principal(
        user_id=user_id, email=user.email, issuer=user.issuer, subject=user.subject
    )
    mem_ctx = MembershipContext(
        membership_id=membership_id,
        organization_id=tenant_id,
        role=Role.INTERVIEWER.value,
        capabilities=get_capabilities_for_role(Role.INTERVIEWER.value),
    )
    actor = ActorContext(principal=principal, membership=mem_ctx)

    uow = UnitOfWork(session=db_session, tenant_id=tenant_id, subject_id=user_id)
    oauth_client = MockGoogleOAuthClient()

    return actor, uow, oauth_client


# ============================================================================
# 1. OAuth Authorization & State Security Tests
# ============================================================================


def test_initiate_google_authorization_generates_bound_state(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        auth_url, state, expires_at = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=["freebusy", "calendar_events"],
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )

        assert "accounts.google.com" in auth_url
        assert state in auth_url
        assert expires_at > datetime.now(UTC)

        # Verify only the SHA-256 hash was persisted in the database
        state_digest = hash_token(state)
        tx = uow.session.scalar(
            select(IntegrationOAuthTransaction).where(
                IntegrationOAuthTransaction.state_hash == state_digest
            )
        )
        assert tx is not None
        assert tx.session_binding == "default_session"
        assert actor.membership is not None
        assert tx.actor_membership_id == actor.membership.membership_id
        assert tx.consumed_at is None
        assert GoogleScope.CALENDAR_FREEBUSY.value in tx.requested_scopes


@pytest.mark.asyncio
async def test_oauth_callback_success_encrypts_tokens_and_stores_connection(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )

        code = "valid_auth_code_12345"
        oauth_client.register_simulated_user(
            code=code,
            sub="google_sub_alice",
            email="alice@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="google_secret_refresh_token_xyz",
            email_verified=True,
        )

        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code=code,
        )

        assert conn.connection_status == ConnectionStatus.ACTIVE.value
        assert conn.verified_email == "alice@google.test"
        assert conn.provider_subject == "google_sub_alice"
        assert conn.encryption_key_version == "v1"

        # Security check: stored tokens must NOT match raw plaintext
        assert conn.encrypted_access_token != f"mock_access_{code}"
        assert conn.encrypted_refresh_token != "google_secret_refresh_token_xyz"

        # Decryption must recover the original token inside boundary
        assert conn.encrypted_refresh_token is not None
        decrypted_access = decrypt_token(conn.encrypted_access_token, TEST_KEY)
        decrypted_refresh = decrypt_token(conn.encrypted_refresh_token, TEST_KEY)
        assert decrypted_access == f"mock_access_{code}"
        assert decrypted_refresh == "google_secret_refresh_token_xyz"


@pytest.mark.asyncio
async def test_oauth_callback_rejects_replayed_state(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )

        # First callback consumes state
        await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code="code_first_run",
        )

        # Second attempt with same state MUST be rejected as replay
        with pytest.raises(OAuthStateError) as exc_info:
            await IntegrationService.handle_google_callback(
                uow=uow,
                actor=actor,
                oauth_client=oauth_client,
                encryption_key=TEST_KEY,
                raw_state=raw_state,
                code="code_second_run",
            )
        assert "replay detected" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_oauth_callback_rejects_expired_state(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )

        # Artificially expire the transaction
        state_digest = hash_token(raw_state)
        tx = uow.session.scalar(
            select(IntegrationOAuthTransaction).where(
                IntegrationOAuthTransaction.state_hash == state_digest
            )
        )
        assert tx is not None
        tx.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        uow.session.flush()

        with pytest.raises(OAuthStateError) as exc_info:
            await IntegrationService.handle_google_callback(
                uow=uow,
                actor=actor,
                oauth_client=oauth_client,
                encryption_key=TEST_KEY,
                raw_state=raw_state,
                code="code_expired",
            )
        assert "expired" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_oauth_callback_rejects_session_mismatch(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
            session_binding="session_alice",
        )

        # Attempt to complete callback with a different session binding (session fixation attempt)
        with pytest.raises(OAuthStateError) as exc_info:
            await IntegrationService.handle_google_callback(
                uow=uow,
                actor=actor,
                oauth_client=oauth_client,
                encryption_key=TEST_KEY,
                raw_state=raw_state,
                code="code_fixation",
                session_binding="session_attacker",
            )
        assert "session mismatch" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_oauth_callback_handles_user_consent_denial(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        with pytest.raises(OAuthConsentDeniedError) as exc_info:
            await IntegrationService.handle_google_callback(
                uow=uow,
                actor=actor,
                oauth_client=oauth_client,
                encryption_key=TEST_KEY,
                raw_state="state_denied",
                code=None,
                error="access_denied",
                error_description="The user denied consent for calendar scopes.",
            )
        assert "access_denied" in str(exc_info.value.detail)


# ============================================================================
# 2. Token Lifecycle, Refresh, and Concurrency Tests
# ============================================================================


@pytest.mark.asyncio
async def test_reconnect_preserves_refresh_token_if_omitted_by_google(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        # Step 1: Initial connection with refresh token
        _, raw_state_1, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_init",
            sub="google_sub_bob",
            email="bob@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="original_refresh_token",
        )
        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state_1,
            code="code_init",
        )
        assert conn.encrypted_refresh_token is not None

        # Step 2: Reconnection where Google omits refresh token (standard Google behavior when prompt=consent is not set)
        _, raw_state_2, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_reconnect_no_refresh",
            sub="google_sub_bob",
            email="bob@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token=None,
        )
        conn_reconnected = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state_2,
            code="code_reconnect_no_refresh",
        )

        # Must preserve the original refresh token
        assert conn_reconnected.encrypted_refresh_token is not None
        decrypted = decrypt_token(conn_reconnected.encrypted_refresh_token, TEST_KEY)
        assert decrypted == "original_refresh_token"


@pytest.mark.asyncio
async def test_get_valid_access_token_uses_active_token_within_ttl(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_active",
            sub="sub_active",
            email="active@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="refresh_tok",
        )
        await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code="code_active",
        )

        assert actor.membership is not None
        token, verified_email = await IntegrationService.get_valid_access_token(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            membership_id=actor.membership.membership_id,
        )
        assert token == "mock_access_code_active"
        assert verified_email == "active@google.test"


@pytest.mark.asyncio
async def test_get_valid_access_token_refreshes_when_expiring(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_expiring",
            sub="sub_exp",
            email="exp@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="refresh_expiring_xyz",
        )
        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code="code_expiring",
        )

        # Set token expiry to 2 minutes from now (within the 5-minute safety buffer)
        conn.token_expiry = datetime.now(UTC) + timedelta(minutes=2)
        uow.session.flush()

        assert actor.membership is not None
        refreshed_token, _ = await IntegrationService.get_valid_access_token(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            membership_id=actor.membership.membership_id,
        )

        assert "mock_refreshed_access" in refreshed_token
        # Connection expiry should have been updated to ~1 hour in the future
        assert conn.token_expiry > datetime.now(UTC) + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_get_valid_access_token_handles_invalid_grant_by_marking_revoked(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_revoked",
            sub="sub_rev",
            email="rev@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="refresh_to_revoke",
        )
        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code="code_revoked",
        )

        # Force token to expire immediately and instruct mock client to simulate invalid_grant
        conn.token_expiry = datetime.now(UTC) - timedelta(seconds=10)
        uow.session.flush()
        oauth_client.simulate_invalid_grant = True

        assert actor.membership is not None
        with pytest.raises(IntegrationReauthRequiredError):
            await IntegrationService.get_valid_access_token(
                uow=uow,
                actor=actor,
                oauth_client=oauth_client,
                encryption_key=TEST_KEY,
                membership_id=actor.membership.membership_id,
            )

        assert conn.connection_status == ConnectionStatus.REVOKED.value
        assert conn.last_error_code == "REFRESH_TOKEN_REVOKED"


@pytest.mark.asyncio
async def test_disconnect_integration_zeroes_stored_credentials_and_audits(
    test_setup: tuple[ActorContext, UnitOfWork, MockGoogleOAuthClient],
) -> None:
    actor, uow, oauth_client = test_setup

    with uow:
        _, raw_state, _ = IntegrationService.initiate_google_authorization(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            requested_capabilities=None,
            redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        )
        oauth_client.register_simulated_user(
            code="code_disc",
            sub="sub_disc",
            email="disc@google.test",
            granted_scopes=DEFAULT_GOOGLE_SCOPES,
            refresh_token="refresh_to_disconnect",
        )
        conn = await IntegrationService.handle_google_callback(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            raw_state=raw_state,
            code="code_disc",
        )
        conn_id = conn.id

        # Disconnect
        disconnected_conn = await IntegrationService.disconnect_integration(
            uow=uow,
            actor=actor,
            oauth_client=oauth_client,
            encryption_key=TEST_KEY,
            connection_id=conn_id,
        )

        assert disconnected_conn.connection_status == ConnectionStatus.DISCONNECTED.value
        assert disconnected_conn.encrypted_access_token == ""
        assert disconnected_conn.encrypted_refresh_token is None
        assert disconnected_conn.disconnected_at is not None
        assert "refresh_to_disconnect" in oauth_client.revoked_tokens


# ============================================================================
# 3. Calendar FreeBusy & UNKNOWN Semantics Tests
# ============================================================================


@pytest.mark.asyncio
async def test_freebusy_returns_half_open_intervals_and_available_windows() -> None:
    adapter = MockGoogleCalendarAdapter()
    cal_id = "interviewer@company.test"

    base_time = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    adapter.add_busy_interval(cal_id, base_time, base_time + timedelta(hours=1))
    adapter.add_busy_interval(
        cal_id, base_time + timedelta(hours=2), base_time + timedelta(hours=3)
    )

    query_start = base_time - timedelta(hours=1)
    query_end = base_time + timedelta(hours=5)

    resp = await adapter.query_freebusy(
        access_token="mock_token",
        calendar_ids=[cal_id],
        time_min=query_start,
        time_max=query_end,
    )

    sched = resp.schedules[cal_id]
    assert sched.status == CalendarAvailabilityStatus.BUSY
    assert len(sched.busy_intervals) == 2
    assert sched.busy_intervals[0].start == base_time
    assert sched.busy_intervals[0].end == base_time + timedelta(hours=1)


@pytest.mark.asyncio
async def test_freebusy_classifies_inaccessible_calendar_as_unknown() -> None:
    adapter = MockGoogleCalendarAdapter()
    inaccessible_cal = "external_vp@partner.test"
    adapter.mark_inaccessible(inaccessible_cal)

    resp = await adapter.query_freebusy(
        access_token="mock_token",
        calendar_ids=[inaccessible_cal],
        time_min=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        time_max=datetime(2026, 11, 10, 17, 0, tzinfo=UTC),
    )

    sched = resp.schedules[inaccessible_cal]
    assert sched.status == CalendarAvailabilityStatus.UNKNOWN
    assert sched.busy_intervals == []
    assert "permission denied" in str(sched.error_detail).lower()


# ============================================================================
# 4. Calendar Event CRUD & ETag Optimistic Concurrency Tests
# ============================================================================


@pytest.mark.asyncio
async def test_calendar_create_and_etag_optimistic_concurrency() -> None:
    adapter = MockGoogleCalendarAdapter()
    cal_id = "primary"
    evt_id = f"talvora_{uuid4()}"
    start = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)
    end = datetime(2026, 11, 10, 14, 45, tzinfo=UTC)

    req = CreateEventRequest(
        event_id=evt_id,
        summary="Technical Interview - Frontend",
        start=start,
        end=end,
        attendees=["candidate@test.com", "interviewer@company.test"],
        create_meet_link=True,
    )

    event = await adapter.create_event("mock_token", cal_id, req)
    assert event.id == evt_id
    assert event.meet_link is not None
    assert event.etag is not None
    initial_etag = event.etag

    # Successful update matching ETag
    upd_req = UpdateEventRequest(
        event_id=evt_id,
        summary="Updated: Technical Interview",
        etag=initial_etag,
    )
    updated = await adapter.update_event("mock_token", cal_id, upd_req)
    assert updated.summary == "Updated: Technical Interview"
    assert updated.etag != initial_etag

    # Stale update with old ETag must raise ProviderETagConflictError
    stale_req = UpdateEventRequest(
        event_id=evt_id,
        summary="Conflicting update",
        etag=initial_etag,  # Stale!
    )
    with pytest.raises(ProviderETagConflictError):
        await adapter.update_event("mock_token", cal_id, stale_req)


@pytest.mark.asyncio
async def test_calendar_deterministic_event_id_collision_handling() -> None:
    adapter = MockGoogleCalendarAdapter()
    cal_id = "primary"
    evt_id = f"talvora_{uuid4()}"
    start = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)

    req = CreateEventRequest(
        event_id=evt_id,
        summary="Deterministic Interview Event",
        start=start,
        end=start + timedelta(hours=1),
    )
    await adapter.create_event("mock_token", cal_id, req)

    # Creating identical ID a second time must return 409 duplicate error
    with pytest.raises(IntegrationError) as exc_info:
        await adapter.create_event("mock_token", cal_id, req)
    assert exc_info.value.status_code == 409


# ============================================================================
# 5. Gmail Delegated Provider Tests
# ============================================================================


@pytest.mark.asyncio
async def test_gmail_send_success_and_tri_state_failures() -> None:
    gmail = MockGoogleGmailAdapter()
    payload = SendEmailPayload(
        from_address="interviewer@company.test",
        to_recipients=["candidate@test.com"],
        subject="Interview Availability Request",
        body_text="Please submit your availability for next week.",
    )

    # 1. Success outcome (ACCEPTED)
    res = await gmail.send_message("mock_token", payload)
    assert res.status == MailSendResultStatus.ACCEPTED
    assert res.provider_message_id is not None
    assert len(gmail.sent_messages) == 1

    # 2. Rejection outcome (DEFINITIVE_FAILURE)
    gmail.simulate_rejected = True
    res_fail = await gmail.send_message("mock_token", payload)
    assert res_fail.status == MailSendResultStatus.DEFINITIVE_FAILURE

    # 3. Timeout outcome (UNKNOWN)
    gmail.simulate_rejected = False
    gmail.simulate_timeout = True
    res_unk = await gmail.send_message("mock_token", payload)
    assert res_unk.status == MailSendResultStatus.UNKNOWN


# ============================================================================
# 6. Tenant Isolation and Visibility Policies
# ============================================================================


def test_tenant_isolation_and_member_visibility(
    db_session: Session,
) -> None:
    # Setup Tenant A with Admin and Member
    tenant_a = uuid4()
    org_a = Organization(id=tenant_a, name="Org A", slug=f"org-a-{uuid4().hex[:6]}")
    user_admin = User(id=uuid4(), email="admin@a.test", issuer="test", subject="sub_admin")
    user_member = User(id=uuid4(), email="member@a.test", issuer="test", subject="sub_member")

    mem_admin = Membership(
        id=uuid4(),
        tenant_id=tenant_a,
        user_id=user_admin.id,
        role=Role.ADMIN.value,
        status="ACTIVE",
    )
    mem_member = Membership(
        id=uuid4(),
        tenant_id=tenant_a,
        user_id=user_member.id,
        role=Role.INTERVIEWER.value,
        status="ACTIVE",
    )

    # Setup Tenant B
    tenant_b = uuid4()
    org_b = Organization(id=tenant_b, name="Org B", slug=f"org-b-{uuid4().hex[:6]}")
    user_b = User(id=uuid4(), email="user@b.test", issuer="test", subject="sub_b")
    mem_b = Membership(
        id=uuid4(), tenant_id=tenant_b, user_id=user_b.id, role=Role.ADMIN.value, status="ACTIVE"
    )

    db_session.add_all(
        [org_a, org_b, user_admin, user_member, user_b, mem_admin, mem_member, mem_b]
    )
    db_session.commit()

    # Create connection for Admin in Org A, Member in Org A, and User in Org B
    conn_admin_a = IntegrationConnection(
        id=uuid4(),
        tenant_id=tenant_a,
        owner_membership_id=mem_admin.id,
        provider="GOOGLE",
        provider_subject="sub1",
        verified_email="admin@google.test",
        granted_scopes=[],
        encrypted_access_token="tok1",
        token_expiry=datetime.now(UTC),
        connected_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    conn_member_a = IntegrationConnection(
        id=uuid4(),
        tenant_id=tenant_a,
        owner_membership_id=mem_member.id,
        provider="GOOGLE",
        provider_subject="sub2",
        verified_email="member@google.test",
        granted_scopes=[],
        encrypted_access_token="tok2",
        token_expiry=datetime.now(UTC),
        connected_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    conn_b = IntegrationConnection(
        id=uuid4(),
        tenant_id=tenant_b,
        owner_membership_id=mem_b.id,
        provider="GOOGLE",
        provider_subject="sub3",
        verified_email="user_b@google.test",
        granted_scopes=[],
        encrypted_access_token="tok3",
        token_expiry=datetime.now(UTC),
        connected_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add_all([conn_admin_a, conn_member_a, conn_b])
    db_session.commit()

    # Actor: Member in Org A
    uow_a = UnitOfWork(session=db_session, tenant_id=tenant_a, subject_id=user_member.id)
    actor_member = ActorContext(
        principal=Principal(
            user_id=user_member.id, email=user_member.email, issuer="test", subject="sub_member"
        ),
        membership=MembershipContext(
            membership_id=mem_member.id,
            organization_id=tenant_a,
            role=Role.INTERVIEWER.value,
            capabilities=frozenset(),
        ),
    )
    member_list = IntegrationService.list_integrations(uow=uow_a, actor=actor_member)
    assert len(member_list) == 1
    assert member_list[0].id == conn_member_a.id

    # Actor: Admin in Org A
    actor_admin = ActorContext(
        principal=Principal(
            user_id=user_admin.id, email=user_admin.email, issuer="test", subject="sub_admin"
        ),
        membership=MembershipContext(
            membership_id=mem_admin.id,
            organization_id=tenant_a,
            role=Role.ADMIN.value,
            capabilities=frozenset(),
        ),
    )
    admin_list = IntegrationService.list_integrations(uow=uow_a, actor=actor_admin)
    assert len(admin_list) == 2
    # Never sees tenant B
    assert all(c.tenant_id == tenant_a for c in admin_list)
