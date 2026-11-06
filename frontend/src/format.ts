export type Role = "OWNER" | "ADMIN" | "RECRUITER" | "HIRING_MANAGER" | "INTERVIEWER";

export type Cap =
  | "TRANSITION_STAGE"
  | "MANAGE_DOCUMENT_INTAKE"
  | "VERIFY_CANDIDATE_CONTACT"
  | "SUBMIT_FEEDBACK"
  | "READ_DOCUMENTS";

export const ROLE_CAPS: Record<Role, readonly Cap[]> = {
  OWNER: [
    "TRANSITION_STAGE",
    "MANAGE_DOCUMENT_INTAKE",
    "VERIFY_CANDIDATE_CONTACT",
    "SUBMIT_FEEDBACK",
    "READ_DOCUMENTS",
  ],
  ADMIN: [
    "TRANSITION_STAGE",
    "MANAGE_DOCUMENT_INTAKE",
    "VERIFY_CANDIDATE_CONTACT",
    "SUBMIT_FEEDBACK",
    "READ_DOCUMENTS",
  ],
  RECRUITER: [
    "TRANSITION_STAGE",
    "MANAGE_DOCUMENT_INTAKE",
    "VERIFY_CANDIDATE_CONTACT",
    "SUBMIT_FEEDBACK",
    "READ_DOCUMENTS",
  ],
  HIRING_MANAGER: [
    "TRANSITION_STAGE",
    "MANAGE_DOCUMENT_INTAKE",
    "SUBMIT_FEEDBACK",
    "READ_DOCUMENTS",
  ],
  INTERVIEWER: ["SUBMIT_FEEDBACK"],
};

export type Stage =
  | "NEW"
  | "IN_REVIEW"
  | "INTERVIEW_APPROVED"
  | "INTERVIEWING"
  | "DECISION_PENDING"
  | "HIRED"
  | "REJECTED"
  | "WITHDRAWN";

export const ACTIVE_STAGES: readonly Stage[] = [
  "NEW",
  "IN_REVIEW",
  "INTERVIEW_APPROVED",
  "INTERVIEWING",
  "DECISION_PENDING",
];

export const TERMINAL_STAGES: readonly Stage[] = ["HIRED", "REJECTED", "WITHDRAWN"];

export const ALLOWED: Record<Stage, readonly Stage[]> = {
  NEW: ["IN_REVIEW", "REJECTED", "WITHDRAWN"],
  IN_REVIEW: ["INTERVIEW_APPROVED", "REJECTED", "WITHDRAWN"],
  INTERVIEW_APPROVED: ["INTERVIEWING", "REJECTED", "WITHDRAWN"],
  INTERVIEWING: ["DECISION_PENDING", "REJECTED", "WITHDRAWN"],
  DECISION_PENDING: ["HIRED", "REJECTED", "WITHDRAWN"],
  HIRED: [],
  REJECTED: ["IN_REVIEW"],
  WITHDRAWN: ["IN_REVIEW"],
};

export function needsReason(from: Stage, to: Stage): boolean {
  const reopening = (from === "REJECTED" || from === "WITHDRAWN") && to === "IN_REVIEW";
  return to === "REJECTED" || to === "WITHDRAWN" || reopening;
}

export function interviewEligible(stage: Stage): boolean {
  return stage === "INTERVIEW_APPROVED" || stage === "INTERVIEWING";
}

export function destinationLabel(from: Stage, to: Stage): string {
  if ((from === "REJECTED" || from === "WITHDRAWN") && to === "IN_REVIEW") {
    return "Reopen to in review";
  }
  if (to === "HIRED") return "Record hire";
  if (to === "REJECTED") return "Reject";
  if (to === "WITHDRAWN") return "Withdraw";
  if (to === "IN_REVIEW") return "Move to in review";
  if (to === "INTERVIEW_APPROVED") return "Approve interview";
  if (to === "INTERVIEWING") return "Start interviewing";
  if (to === "DECISION_PENDING") return "Send to decision";
  return `Move to ${to}`;
}

const LABELS: Record<string, string> = {
  NEW: "New",
  IN_REVIEW: "In review",
  INTERVIEW_APPROVED: "Interview approved",
  INTERVIEWING: "Interviewing",
  DECISION_PENDING: "Decision pending",
  HIRED: "Hired",
  REJECTED: "Rejected",
  WITHDRAWN: "Withdrawn",
  DRAFT: "Draft",
  OPEN: "Open",
  PAUSED: "Paused",
  CLOSED: "Closed",
  FULL_TIME: "Full time",
  MANUAL_ENTRY: "Manual entry",
  UPLOAD_INTAKE: "Upload intake",
  RECRUITER_ENTERED: "Recruiter entered",
  CANDIDATE_SUBMITTED: "Candidate submitted",
  RESUME_EXTRACTED_UNVERIFIED: "Resume extracted, unverified",
  SYSTEM_IMPORT: "System import",
  RECRUITER_ATTESTATION: "Recruiter attestation",
  CANDIDATE_CONFIRMED_REPLY: "Candidate confirmed reply",
  THIRD_PARTY_REFERRAL: "Third-party referral",
  OTHER: "Other",
  OWNER: "Owner",
  ADMIN: "Admin",
  RECRUITER: "Recruiter",
  HIRING_MANAGER: "Hiring manager",
  INTERVIEWER: "Interviewer",
  SKILL_PRESENT: "Skill present",
  MIN_EXPERIENCE_MONTHS: "Minimum experience",
  EDUCATION_LEVEL: "Education level",
  CERTIFICATION_PRESENT: "Certification",
  MANUAL_REQUIREMENT: "Manual requirement",
  RESERVED: "Reserved",
  QUARANTINED: "Quarantined",
  SCANNING: "Scanning",
  CLEAN: "Clean",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  CANCELED: "Canceled",
  SUCCEEDED: "Succeeded",
  PARTIAL: "Partial",
  NEEDS_REVIEW: "Needs review",
  DIGITAL_TEXT: "Digital text",
  OCR: "OCR",
  DOCX_STRUCTURED: "DOCX structure",
  SYSTEM_SCAN_CLEAN: "Scan came back clean",
  MANUAL_REPROCESS: "Manual reprocess",
  PDF_PAGE: "PDF page",
};

export function pretty(value: string): string {
  if (LABELS[value]) return LABELS[value];
  const lower = value.toLowerCase().replaceAll("_", " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

export function when(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function greeting(date = new Date()): string {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}
