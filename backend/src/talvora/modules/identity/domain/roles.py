"""Identity Roles and Granted Capabilities Matrix."""

from enum import StrEnum
from typing import Final


class Role(StrEnum):
    """Base membership roles within an organization."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    RECRUITER = "RECRUITER"
    HIRING_MANAGER = "HIRING_MANAGER"
    INTERVIEWER = "INTERVIEWER"


class Capability(StrEnum):
    """Granular permissions evaluated for staff operations."""

    MANAGE_ORGANIZATION_POLICY = "MANAGE_ORGANIZATION_POLICY"
    TRANSFER_OWNERSHIP = "TRANSFER_OWNERSHIP"
    MANAGE_MEMBERS = "MANAGE_MEMBERS"
    MANAGE_TEAMS = "MANAGE_TEAMS"
    INVITE_MEMBERS = "INVITE_MEMBERS"
    VIEW_MEMBERS = "VIEW_MEMBERS"
    VIEW_TEAMS = "VIEW_TEAMS"
    CREATE_JOB = "CREATE_JOB"
    VIEW_JOBS = "VIEW_JOBS"
    EDIT_JOB = "EDIT_JOB"
    PUBLISH_JOB_REQUIREMENTS = "PUBLISH_JOB_REQUIREMENTS"
    MANAGE_JOB_ASSIGNMENTS = "MANAGE_JOB_ASSIGNMENTS"
    TRANSITION_JOB_STATUS = "TRANSITION_JOB_STATUS"
    CREATE_CANDIDATE = "CREATE_CANDIDATE"
    VIEW_CANDIDATES = "VIEW_CANDIDATES"
    EDIT_CANDIDATE = "EDIT_CANDIDATE"
    VERIFY_CANDIDATE_CONTACT = "VERIFY_CANDIDATE_CONTACT"
    CREATE_APPLICATION = "CREATE_APPLICATION"
    READ_APPLICATION = "READ_APPLICATION"
    TRANSITION_STAGE = "TRANSITION_STAGE"
    MANAGE_DOCUMENT_INTAKE = "MANAGE_DOCUMENT_INTAKE"
    READ_DOCUMENTS = "READ_DOCUMENTS"
    APPROVE_SCHEDULING = "APPROVE_SCHEDULING"
    SUBMIT_FEEDBACK = "SUBMIT_FEEDBACK"
    EXECUTE_PRIVACY_PURGE = "EXECUTE_PRIVACY_PURGE"


# The Authoritative Base Role Capability Map (ADR-0003 & permissions.md)
ROLE_CAPABILITIES: Final[dict[str, frozenset[str]]] = {
    Role.OWNER.value: frozenset(
        {
            Capability.MANAGE_ORGANIZATION_POLICY.value,
            Capability.TRANSFER_OWNERSHIP.value,
            Capability.MANAGE_MEMBERS.value,
            Capability.MANAGE_TEAMS.value,
            Capability.INVITE_MEMBERS.value,
            Capability.VIEW_MEMBERS.value,
            Capability.VIEW_TEAMS.value,
            Capability.CREATE_JOB.value,
            Capability.VIEW_JOBS.value,
            Capability.EDIT_JOB.value,
            Capability.PUBLISH_JOB_REQUIREMENTS.value,
            Capability.MANAGE_JOB_ASSIGNMENTS.value,
            Capability.TRANSITION_JOB_STATUS.value,
            Capability.CREATE_CANDIDATE.value,
            Capability.VIEW_CANDIDATES.value,
            Capability.EDIT_CANDIDATE.value,
            Capability.VERIFY_CANDIDATE_CONTACT.value,
            Capability.CREATE_APPLICATION.value,
            Capability.READ_APPLICATION.value,
            Capability.TRANSITION_STAGE.value,
            Capability.APPROVE_SCHEDULING.value,
            Capability.SUBMIT_FEEDBACK.value,
            Capability.EXECUTE_PRIVACY_PURGE.value,
            Capability.MANAGE_DOCUMENT_INTAKE.value,
            Capability.READ_DOCUMENTS.value,
        }
    ),
    Role.ADMIN.value: frozenset(
        {
            Capability.MANAGE_MEMBERS.value,
            Capability.MANAGE_TEAMS.value,
            Capability.INVITE_MEMBERS.value,
            Capability.VIEW_MEMBERS.value,
            Capability.VIEW_TEAMS.value,
            Capability.CREATE_JOB.value,
            Capability.VIEW_JOBS.value,
            Capability.MANAGE_JOB_ASSIGNMENTS.value,
            Capability.CREATE_CANDIDATE.value,
            Capability.VIEW_CANDIDATES.value,
            Capability.EDIT_CANDIDATE.value,
            Capability.VERIFY_CANDIDATE_CONTACT.value,
            Capability.CREATE_APPLICATION.value,
            Capability.READ_APPLICATION.value,
            Capability.TRANSITION_STAGE.value,
            Capability.APPROVE_SCHEDULING.value,
            Capability.SUBMIT_FEEDBACK.value,
            Capability.EXECUTE_PRIVACY_PURGE.value,
            Capability.MANAGE_DOCUMENT_INTAKE.value,
            Capability.READ_DOCUMENTS.value,
        }
    ),
    Role.RECRUITER.value: frozenset(
        {
            Capability.VIEW_MEMBERS.value,
            Capability.VIEW_TEAMS.value,
            Capability.CREATE_JOB.value,
            Capability.VIEW_JOBS.value,
            Capability.EDIT_JOB.value,
            Capability.PUBLISH_JOB_REQUIREMENTS.value,
            Capability.MANAGE_JOB_ASSIGNMENTS.value,
            Capability.TRANSITION_JOB_STATUS.value,
            Capability.CREATE_CANDIDATE.value,
            Capability.VIEW_CANDIDATES.value,
            Capability.EDIT_CANDIDATE.value,
            Capability.VERIFY_CANDIDATE_CONTACT.value,
            Capability.CREATE_APPLICATION.value,
            Capability.READ_APPLICATION.value,
            Capability.TRANSITION_STAGE.value,
            Capability.APPROVE_SCHEDULING.value,
            Capability.SUBMIT_FEEDBACK.value,
            Capability.MANAGE_DOCUMENT_INTAKE.value,
            Capability.READ_DOCUMENTS.value,
        }
    ),
    Role.HIRING_MANAGER.value: frozenset(
        {
            Capability.VIEW_MEMBERS.value,
            Capability.VIEW_TEAMS.value,
            Capability.CREATE_JOB.value,
            Capability.VIEW_JOBS.value,
            Capability.EDIT_JOB.value,
            Capability.PUBLISH_JOB_REQUIREMENTS.value,
            Capability.MANAGE_JOB_ASSIGNMENTS.value,
            Capability.TRANSITION_JOB_STATUS.value,
            Capability.VIEW_CANDIDATES.value,
            Capability.CREATE_APPLICATION.value,
            Capability.READ_APPLICATION.value,
            Capability.TRANSITION_STAGE.value,
            Capability.APPROVE_SCHEDULING.value,
            Capability.SUBMIT_FEEDBACK.value,
            Capability.MANAGE_DOCUMENT_INTAKE.value,
            Capability.READ_DOCUMENTS.value,
        }
    ),
    Role.INTERVIEWER.value: frozenset(
        {
            Capability.VIEW_MEMBERS.value,
            Capability.VIEW_TEAMS.value,
            Capability.VIEW_JOBS.value,
            Capability.VIEW_CANDIDATES.value,
            Capability.READ_APPLICATION.value,
            Capability.SUBMIT_FEEDBACK.value,
        }
    ),
}


def get_capabilities_for_role(role: str) -> frozenset[str]:
    """Retrieve capabilities for a given role, defaulting to empty set if unrecognized."""
    return ROLE_CAPABILITIES.get(role.upper(), frozenset())
