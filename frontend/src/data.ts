import type { Stage } from "./format";

export const actor = {
  name: "Mira Sen",
  org: "Talvora",
  team: "Talent operations",
};

export const protectedTraits = [
  "age",
  "gender",
  "race",
  "religion",
  "caste",
  "disability",
  "national origin",
];

export type Criterion = {
  id: string;
  type:
    | "SKILL_PRESENT"
    | "MIN_EXPERIENCE_MONTHS"
    | "EDUCATION_LEVEL"
    | "CERTIFICATION_PRESENT"
    | "MANUAL_REQUIREMENT";
  required: boolean;
  human: boolean;
  source: string;
  rationale: string;
  detail: string;
};

export type RequirementVersion = {
  number: number;
  publishedAt: string;
  note: string;
  criteria: Criterion[];
};

export type Job = {
  id: string;
  title: string;
  department: string;
  location: string;
  employment: string;
  status: "DRAFT" | "OPEN" | "PAUSED" | "CLOSED";
  summary: string;
  assignments: { role: string; name: string }[];
  versions: RequirementVersion[];
  draft: Criterion[];
};

export type ContactRevision = {
  version: number;
  email: string;
  phone: string | null;
  source:
    | "RECRUITER_ENTERED"
    | "CANDIDATE_SUBMITTED"
    | "RESUME_EXTRACTED_UNVERIFIED"
    | "SYSTEM_IMPORT";
  at: string;
};

export type Verification = {
  revision: number;
  source: "RECRUITER_ATTESTATION" | "CANDIDATE_CONFIRMED_REPLY" | "THIRD_PARTY_REFERRAL" | "OTHER";
  actor: string;
  note: string;
  at: string;
};

export type Candidate = {
  id: string;
  name: string;
  source: "MANUAL_ENTRY" | "UPLOAD_INTAKE";
  location: string;
  headline: string;
  contacts: ContactRevision[];
  verification: Verification | null;
};

export type FeedbackNote = {
  id: string;
  author: string;
  body: string;
  at: string;
};

export type StageEvent = {
  id: string;
  from: Stage | null;
  to: Stage;
  actor: string;
  reason: string | null;
  at: string;
};

export type Application = {
  id: string;
  serial: string;
  candidateId: string;
  jobId: string;
  stage: Stage;
  source: "MANUAL_ENTRY" | "UPLOAD_INTAKE";
  version: number;
  attention: string | null;
  history: StageEvent[];
  feedback: FeedbackNote[];
};

export type IntakeStatus =
  | "RESERVED"
  | "QUARANTINED"
  | "SCANNING"
  | "CLEAN"
  | "BLOCKED"
  | "FAILED"
  | "CANCELED";

export type IntakeItem = {
  id: string;
  batch: string;
  jobId: string;
  filename: string;
  mediaType: string;
  bytes: number;
  status: IntakeStatus;
  verdict: "CLEAN" | "BLOCKED";
  problem: string | null;
  who: string;
};

export type Segment = {
  id: string;
  locator: string;
  method: "DIGITAL_TEXT" | "OCR" | "DOCX_STRUCTURED";
  text: string;
  confidence: number | null;
};

export type Fragment = {
  id: string;
  segmentId: string;
  quote: string;
  hash: string;
};

export type ExtractionRun = {
  id: string;
  candidateId: string;
  documentTitle: string;
  filename: string;
  mediaType: string;
  status: "SUCCEEDED" | "PARTIAL" | "NEEDS_REVIEW" | "FAILED";
  revision: number;
  extractor: string;
  normalization: string;
  trigger: "SYSTEM_SCAN_CLEAN" | "MANUAL_REPROCESS";
  pages: string;
  ocrPages: number;
  warning: string | null;
  segments: Segment[];
  fragments: Fragment[];
};

export type DuplicatePair = {
  id: string;
  a: string;
  b: string;
  reason: string;
};

const python: Criterion = {
  id: "c-python",
  type: "SKILL_PRESENT",
  required: true,
  human: false,
  source: "Demonstrated expertise in Python 3.12+, FastAPI, and SQLAlchemy async query execution.",
  rationale: "The core runtime is Python on an asynchronous ASGI stack.",
  detail: "Skill Python · canonical python · synonyms python3, asyncio, fastapi · advanced",
};

const degree: Criterion = {
  id: "c-degree",
  type: "EDUCATION_LEVEL",
  required: true,
  human: false,
  source:
    "Bachelor's degree in Computer Science, Computer Engineering, or a closely related discipline, or equivalent practical experience.",
  rationale: "The work assumes fluency with concurrency, data structures, and operating systems.",
  detail: "Minimum bachelor's · fields include computer science and applied mathematics · 48 months may substitute",
};

const clearance: Criterion = {
  id: "c-clearance",
  type: "MANUAL_REQUIREMENT",
  required: true,
  human: true,
  source:
    "Must hold an active US Secret clearance, or show eligibility for immediate sponsorship.",
  rationale: "A slice of customer workloads sits under facility clearance rules.",
  detail:
    "A recruiter confirms cleared status with the facility security officer. Do not request a foreign passport or national-origin documents.",
};

export const jobs: Job[] = [
  {
    id: "job-dse",
    title: "Senior Distributed Systems Engineer",
    department: "Platform infrastructure",
    location: "Remote · US / Canada",
    employment: "FULL_TIME",
    status: "OPEN",
    summary:
      "Lead the event stream and the durable outbox. Design fencing that survives a failover, and keep tenant publish latency honest.",
    assignments: [
      { role: "Hiring manager", name: "Idris Vale" },
      { role: "Recruiter", name: "Mira Sen" },
      { role: "Interviewer", name: "Leela Prasad" },
    ],
    draft: [],
    versions: [
      {
        number: 1,
        publishedAt: "2026-09-12T15:10:00Z",
        note: "First law. Experience bar sat at four years.",
        criteria: [
          {
            id: "c-exp-48",
            type: "MIN_EXPERIENCE_MONTHS",
            required: true,
            human: false,
            source: "At least 48 months designing high-concurrency backend services.",
            rationale: "Enough scar tissue to own a race without a chaperone.",
            detail: "48 months · titles include backend engineer and site reliability engineer",
          },
          python,
          degree,
        ],
      },
      {
        number: 2,
        publishedAt: "2026-10-03T11:40:00Z",
        note: "Clearance became a human check. The experience bar did not move.",
        criteria: [
          {
            id: "c-exp-48b",
            type: "MIN_EXPERIENCE_MONTHS",
            required: true,
            human: false,
            source: "At least 48 months designing high-concurrency backend services.",
            rationale: "Enough scar tissue to own a race without a chaperone.",
            detail: "48 months · titles include backend engineer and site reliability engineer",
          },
          python,
          degree,
          clearance,
        ],
      },
      {
        number: 3,
        publishedAt: "2026-11-01T08:05:00Z",
        note: "Experience raised to 60 months. Rust and an AWS credential are preferred, not required.",
        criteria: [
          {
            id: "c-exp-60",
            type: "MIN_EXPERIENCE_MONTHS",
            required: true,
            human: false,
            source: "Minimum 60 months designing high-concurrency backend services.",
            rationale: "The outbox and the lease protocol need an independent owner.",
            detail: "60 months · Python, PostgreSQL, distributed systems · a master's may stand in for 12",
          },
          python,
          degree,
          clearance,
          {
            id: "c-rust",
            type: "SKILL_PRESENT",
            required: false,
            human: false,
            source: "Rust or Go for low-level services. Preferred, not a gate.",
            rationale: "A later OCR worker may leave Python.",
            detail: "Skill Rust · synonyms golang, go · intermediate",
          },
          {
            id: "c-aws",
            type: "CERTIFICATION_PRESENT",
            required: false,
            human: false,
            source: "AWS Certified Solutions Architect, associate or professional.",
            rationale: "Talvora runs on RDS, SQS, and private S3.",
            detail: "Amazon Web Services · expired certificates do not count",
          },
        ],
      },
    ],
  },
  {
    id: "job-extract",
    title: "Evidence Extraction Engineer",
    department: "Document intelligence",
    location: "Remote",
    employment: "FULL_TIME",
    status: "OPEN",
    summary:
      "Turn a clean PDF or DOCX into source-addressable text. Pages, paragraphs, and table cells stay citable. Quotes never mutate under a citation.",
    assignments: [
      { role: "Hiring manager", name: "Nia Ferrer" },
      { role: "Recruiter", name: "Mira Sen" },
    ],
    draft: [],
    versions: [
      {
        number: 1,
        publishedAt: "2026-10-18T13:20:00Z",
        note: "Digital text only. OCR was still a draft.",
        criteria: [
          {
            id: "c-pdf",
            type: "SKILL_PRESENT",
            required: true,
            human: false,
            source: "Production experience extracting text from PDF and DOCX without destroying location.",
            rationale: "Citations are only as good as the locator.",
            detail: "Skill document extraction · synonyms pdfminer, python-docx",
          },
        ],
      },
      {
        number: 2,
        publishedAt: "2026-11-04T16:45:00Z",
        note: "OCR joined the law, and a human must review low-confidence pages.",
        criteria: [
          {
            id: "c-pdf-2",
            type: "SKILL_PRESENT",
            required: true,
            human: false,
            source: "Production experience extracting text from PDF and DOCX without destroying location.",
            rationale: "Citations are only as good as the locator.",
            detail: "Skill document extraction · synonyms pdfminer, python-docx",
          },
          {
            id: "c-ocr",
            type: "SKILL_PRESENT",
            required: true,
            human: false,
            source: "Has shipped an OCR fallback for scanned pages and knows when to refuse the read.",
            rationale: "A scanned clearance packet is not a digital resume.",
            detail: "Skill OCR · low confidence must surface as needs-review, never as a silent success",
          },
          {
            id: "c-review",
            type: "MANUAL_REQUIREMENT",
            required: true,
            human: true,
            source: "Will not treat a partial extraction as a finished citation.",
            rationale: "Review is a human command.",
            detail: "Interviewer or recruiter reads the highlighted span before anyone relies on it.",
          },
        ],
      },
    ],
  },
  {
    id: "job-ops",
    title: "Recruiting Operations Lead",
    department: "People systems",
    location: "Bengaluru / remote",
    employment: "FULL_TIME",
    status: "PAUSED",
    summary:
      "Own the desk itself: intake batches, stage hygiene, and the audit trail hiring managers actually read.",
    assignments: [{ role: "Hiring manager", name: "Mira Sen" }],
    draft: [],
    versions: [
      {
        number: 1,
        publishedAt: "2026-08-22T10:00:00Z",
        note: "Paused before a second version. The published rule still stands, but the vacancy takes no new files.",
        criteria: [
          {
            id: "c-ops",
            type: "MIN_EXPERIENCE_MONTHS",
            required: true,
            human: false,
            source: "36 months running a high-volume hiring desk or an adjacent operations system.",
            rationale: "The queue is the product.",
            detail: "36 months · relevant titles include recruiting operations and program manager",
          },
          {
            id: "c-audit",
            type: "MANUAL_REQUIREMENT",
            required: true,
            human: true,
            source: "Can walk an auditor from a stage change back to a named person and a reason.",
            rationale: "If the trail is fuzzy, the hire is fuzzy.",
            detail: "Panel reviews a sample of rejected files and their reasons.",
          },
        ],
      },
    ],
  },
  {
    id: "job-design",
    title: "Staff Product Designer, Evidence Surfaces",
    department: "Design",
    location: "Remote",
    employment: "FULL_TIME",
    status: "DRAFT",
    summary:
      "Design the surfaces where a recruiter reads a quote, a locator, and a human decision without confusing the three.",
    assignments: [{ role: "Hiring manager", name: "Nia Ferrer" }],
    versions: [],
    draft: [
      {
        id: "c-design",
        type: "MANUAL_REQUIREMENT",
        required: true,
        human: true,
        source: "A portfolio that shows dense operational software, not a marketing site alone.",
        rationale: "This desk is a tool for judgment.",
        detail: "Hiring manager reviews two case studies before any stage past in review.",
      },
      {
        id: "c-type",
        type: "SKILL_PRESENT",
        required: false,
        human: false,
        source: "Comfortable specifying type, states, and empty cases for audit-heavy interfaces.",
        rationale: "The product voice is severe on purpose.",
        detail: "Preferred · not yet published, so it screens nobody",
      },
    ],
  },
];

export const candidateSeed: Candidate[] = [
  {
    id: "cand-adeel",
    name: "Adeel Rahman",
    source: "UPLOAD_INTAKE",
    location: "Toronto",
    headline: "Distributed systems · outbox, fencing, Postgres",
    contacts: [
      {
        version: 1,
        email: "adeel.rahman@mailbox.test",
        phone: "+1 416 555 0148",
        source: "RESUME_EXTRACTED_UNVERIFIED",
        at: "2026-11-02T14:12:00Z",
      },
      {
        version: 2,
        email: "adeel.rahman@mailbox.test",
        phone: "+1 416 555 0190",
        source: "CANDIDATE_SUBMITTED",
        at: "2026-11-03T09:40:00Z",
      },
    ],
    verification: {
      revision: 2,
      source: "RECRUITER_ATTESTATION",
      actor: "Mira Sen",
      note: "Adeel confirmed the new mobile on a call. This does not prove he owns the mailbox.",
      at: "2026-11-03T09:44:00Z",
    },
  },
  {
    id: "cand-naomi",
    name: "Naomi Okonkwo",
    source: "MANUAL_ENTRY",
    location: "Austin",
    headline: "Backend · Python, leases, and a scanned clearance packet",
    contacts: [
      {
        version: 1,
        email: "naomi.okonkwo@mailbox.test",
        phone: "+1 512 555 0172",
        source: "RECRUITER_ENTERED",
        at: "2026-11-04T18:02:00Z",
      },
    ],
    verification: null,
  },
  {
    id: "cand-jules",
    name: "Jules Park",
    source: "UPLOAD_INTAKE",
    location: "Seattle",
    headline: "Backend engineer · resume arrived this morning",
    contacts: [
      {
        version: 1,
        email: "jules.park@mailbox.test",
        phone: null,
        source: "RESUME_EXTRACTED_UNVERIFIED",
        at: "2026-11-06T08:16:00Z",
      },
    ],
    verification: null,
  },
  {
    id: "cand-jpark",
    name: "J. Park",
    source: "UPLOAD_INTAKE",
    location: "Unknown",
    headline: "Draft from a second file. Not the same record as Jules.",
    contacts: [
      {
        version: 1,
        email: "j.park@other.test",
        phone: null,
        source: "RESUME_EXTRACTED_UNVERIFIED",
        at: "2026-11-06T08:22:00Z",
      },
    ],
    verification: null,
  },
  {
    id: "cand-helen",
    name: "Helen Cho",
    source: "MANUAL_ENTRY",
    location: "Vancouver",
    headline: "Extraction pipelines · PDF locators, careful OCR",
    contacts: [
      {
        version: 1,
        email: "helen.cho@mailbox.test",
        phone: "+1 604 555 0114",
        source: "RECRUITER_ENTERED",
        at: "2026-10-28T12:00:00Z",
      },
    ],
    verification: {
      revision: 1,
      source: "CANDIDATE_CONFIRMED_REPLY",
      actor: "Mira Sen",
      note: "Helen replied from this address. Still a human record, not a mailbox proof protocol.",
      at: "2026-10-28T16:12:00Z",
    },
  },
  {
    id: "cand-mateo",
    name: "Mateo Ruiz",
    source: "UPLOAD_INTAKE",
    location: "Mexico City",
    headline: "Document systems · ready for a decision",
    contacts: [
      {
        version: 1,
        email: "mateo.ruiz@mailbox.test",
        phone: "+52 55 5555 0198",
        source: "RECRUITER_ENTERED",
        at: "2026-10-20T15:18:00Z",
      },
    ],
    verification: {
      revision: 1,
      source: "RECRUITER_ATTESTATION",
      actor: "Mira Sen",
      note: "Attested after the intake batch went clean.",
      at: "2026-10-21T11:02:00Z",
    },
  },
  {
    id: "cand-priya",
    name: "Priya Natarajan",
    source: "MANUAL_ENTRY",
    location: "Bengaluru",
    headline: "Recruiting operations · rejected when the role paused",
    contacts: [
      {
        version: 1,
        email: "priya.natarajan@mailbox.test",
        phone: null,
        source: "RECRUITER_ENTERED",
        at: "2026-09-02T06:30:00Z",
      },
    ],
    verification: {
      revision: 1,
      source: "RECRUITER_ATTESTATION",
      actor: "Mira Sen",
      note: "Entered from Priya's own note.",
      at: "2026-09-02T06:40:00Z",
    },
  },
  {
    id: "cand-samir",
    name: "Samir El-Sayed",
    source: "MANUAL_ENTRY",
    location: "Montréal",
    headline: "Hired onto the platform team under version 2 of the rule",
    contacts: [
      {
        version: 1,
        email: "samir.elsayed@mailbox.test",
        phone: "+1 514 555 0166",
        source: "CANDIDATE_SUBMITTED",
        at: "2026-09-20T13:00:00Z",
      },
    ],
    verification: {
      revision: 1,
      source: "CANDIDATE_CONFIRMED_REPLY",
      actor: "Mira Sen",
      note: "Confirmed before the offer.",
      at: "2026-09-21T15:14:00Z",
    },
  },
  {
    id: "cand-lina",
    name: "Lina Voss",
    source: "MANUAL_ENTRY",
    location: "Berlin",
    headline: "Product design · withdrew while the role was still a draft",
    contacts: [
      {
        version: 1,
        email: "lina.voss@mailbox.test",
        phone: null,
        source: "RECRUITER_ENTERED",
        at: "2026-10-09T09:12:00Z",
      },
    ],
    verification: null,
  },
  {
    id: "cand-ren",
    name: "Ren Ibarra",
    source: "UPLOAD_INTAKE",
    location: "Manila",
    headline: "Parser engineer · file still in the scanner",
    contacts: [
      {
        version: 1,
        email: "ren.ibarra@mailbox.test",
        phone: null,
        source: "RESUME_EXTRACTED_UNVERIFIED",
        at: "2026-11-06T10:48:00Z",
      },
    ],
    verification: null,
  },
];

function opened(
  id: string,
  at: string,
  actorName: string,
): StageEvent {
  return { id, from: null, to: "NEW", actor: actorName, reason: null, at };
}

export const applicationSeed: Application[] = [
  {
    id: "app-adeel",
    serial: "TV-1048",
    candidateId: "cand-adeel",
    jobId: "job-dse",
    stage: "INTERVIEWING",
    source: "UPLOAD_INTAKE",
    version: 4,
    attention: null,
    history: [
      opened("e-a1", "2026-11-02T14:20:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-a2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-11-02T16:05:00Z",
      },
      {
        id: "e-a3",
        from: "IN_REVIEW",
        to: "INTERVIEW_APPROVED",
        actor: "Idris Vale · Hiring manager",
        reason: null,
        at: "2026-11-04T15:12:00Z",
      },
      {
        id: "e-a4",
        from: "INTERVIEW_APPROVED",
        to: "INTERVIEWING",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-11-05T11:30:00Z",
      },
    ],
    feedback: [
      {
        id: "f-a1",
        author: "Leela Prasad · Interviewer",
        body: "Strong on fencing and the outbox. Ask what happens when the OCR worker restarts mid-page.",
        at: "2026-11-05T18:20:00Z",
      },
    ],
  },
  {
    id: "app-naomi",
    serial: "TV-1052",
    candidateId: "cand-naomi",
    jobId: "job-dse",
    stage: "IN_REVIEW",
    source: "MANUAL_ENTRY",
    version: 2,
    attention:
      "Confirm clearance with the facility security officer before any interview approval. Do not ask for national-origin documents.",
    history: [
      opened("e-n1", "2026-11-04T18:10:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-n2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-11-04T18:18:00Z",
      },
    ],
    feedback: [],
  },
  {
    id: "app-jules",
    serial: "TV-1061",
    candidateId: "cand-jules",
    jobId: "job-dse",
    stage: "NEW",
    source: "UPLOAD_INTAKE",
    version: 1,
    attention: null,
    history: [opened("e-j1", "2026-11-06T08:18:00Z", "Mira Sen · Recruiter")],
    feedback: [],
  },
  {
    id: "app-helen",
    serial: "TV-1074",
    candidateId: "cand-helen",
    jobId: "job-extract",
    stage: "INTERVIEW_APPROVED",
    source: "MANUAL_ENTRY",
    version: 3,
    attention: null,
    history: [
      opened("e-h1", "2026-10-28T12:10:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-h2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-10-29T09:00:00Z",
      },
      {
        id: "e-h3",
        from: "IN_REVIEW",
        to: "INTERVIEW_APPROVED",
        actor: "Nia Ferrer · Hiring manager",
        reason: null,
        at: "2026-11-05T14:41:00Z",
      },
    ],
    feedback: [],
  },
  {
    id: "app-mateo",
    serial: "TV-1080",
    candidateId: "cand-mateo",
    jobId: "job-extract",
    stage: "DECISION_PENDING",
    source: "UPLOAD_INTAKE",
    version: 5,
    attention: "Decision is waiting on Nia. The model does not have a vote.",
    history: [
      opened("e-m1", "2026-10-21T11:10:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-m2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-10-22T10:00:00Z",
      },
      {
        id: "e-m3",
        from: "IN_REVIEW",
        to: "INTERVIEW_APPROVED",
        actor: "Nia Ferrer · Hiring manager",
        reason: null,
        at: "2026-10-24T16:12:00Z",
      },
      {
        id: "e-m4",
        from: "INTERVIEW_APPROVED",
        to: "INTERVIEWING",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-10-27T15:02:00Z",
      },
      {
        id: "e-m5",
        from: "INTERVIEWING",
        to: "DECISION_PENDING",
        actor: "Nia Ferrer · Hiring manager",
        reason: null,
        at: "2026-11-04T19:26:00Z",
      },
    ],
    feedback: [
      {
        id: "f-m1",
        author: "Leela Prasad · Interviewer",
        body: "Mateo refused to cite a low-confidence OCR page. That is the job.",
        at: "2026-11-03T17:05:00Z",
      },
    ],
  },
  {
    id: "app-priya",
    serial: "TV-0988",
    candidateId: "cand-priya",
    jobId: "job-ops",
    stage: "REJECTED",
    source: "MANUAL_ENTRY",
    version: 3,
    attention: null,
    history: [
      opened("e-p1", "2026-09-02T06:45:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-p2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-09-03T07:10:00Z",
      },
      {
        id: "e-p3",
        from: "IN_REVIEW",
        to: "REJECTED",
        actor: "Mira Sen · Recruiter",
        reason: "The operations role was paused. The published criteria no longer match the seat we are filling.",
        at: "2026-10-16T08:30:00Z",
      },
    ],
    feedback: [],
  },
  {
    id: "app-samir",
    serial: "TV-0902",
    candidateId: "cand-samir",
    jobId: "job-dse",
    stage: "HIRED",
    source: "MANUAL_ENTRY",
    version: 6,
    attention: null,
    history: [
      opened("e-s1", "2026-09-20T13:12:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-s2",
        from: "NEW",
        to: "IN_REVIEW",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-09-21T15:20:00Z",
      },
      {
        id: "e-s3",
        from: "IN_REVIEW",
        to: "INTERVIEW_APPROVED",
        actor: "Idris Vale · Hiring manager",
        reason: null,
        at: "2026-09-28T14:00:00Z",
      },
      {
        id: "e-s4",
        from: "INTERVIEW_APPROVED",
        to: "INTERVIEWING",
        actor: "Mira Sen · Recruiter",
        reason: null,
        at: "2026-10-01T13:40:00Z",
      },
      {
        id: "e-s5",
        from: "INTERVIEWING",
        to: "DECISION_PENDING",
        actor: "Idris Vale · Hiring manager",
        reason: null,
        at: "2026-10-08T18:05:00Z",
      },
      {
        id: "e-s6",
        from: "DECISION_PENDING",
        to: "HIRED",
        actor: "Idris Vale · Hiring manager",
        reason: null,
        at: "2026-10-14T16:48:00Z",
      },
    ],
    feedback: [],
  },
  {
    id: "app-lina",
    serial: "TV-1011",
    candidateId: "cand-lina",
    jobId: "job-design",
    stage: "WITHDRAWN",
    source: "MANUAL_ENTRY",
    version: 2,
    attention: null,
    history: [
      opened("e-l1", "2026-10-09T09:20:00Z", "Mira Sen · Recruiter"),
      {
        id: "e-l2",
        from: "NEW",
        to: "WITHDRAWN",
        actor: "Mira Sen · Recruiter",
        reason: "Lina withdrew while the design vacancy was still an unpublished draft.",
        at: "2026-10-30T12:15:00Z",
      },
    ],
    feedback: [],
  },
  {
    id: "app-ren",
    serial: "TV-1104",
    candidateId: "cand-ren",
    jobId: "job-extract",
    stage: "NEW",
    source: "UPLOAD_INTAKE",
    version: 1,
    attention: null,
    history: [opened("e-r1", "2026-11-06T10:50:00Z", "Mira Sen · Recruiter")],
    feedback: [],
  },
];

export const intakeSeed: IntakeItem[] = [
  {
    id: "item-adeel",
    batch: "02 Nov · Platform intake",
    jobId: "job-dse",
    filename: "adeel-rahman.pdf",
    mediaType: "application/pdf",
    bytes: 248_331,
    status: "CLEAN",
    verdict: "CLEAN",
    problem: null,
    who: "Adeel Rahman",
  },
  {
    id: "item-naomi",
    batch: "04 Nov · Platform intake",
    jobId: "job-dse",
    filename: "naomi-clearance-scan.pdf",
    mediaType: "application/pdf",
    bytes: 1_804_220,
    status: "CLEAN",
    verdict: "CLEAN",
    problem: null,
    who: "Naomi Okonkwo",
  },
  {
    id: "item-macro",
    batch: "06 Nov · Platform intake",
    jobId: "job-dse",
    filename: "benefits-macro.docm",
    mediaType: "application/vnd.ms-word.document.macroEnabled.12",
    bytes: 88_120,
    status: "BLOCKED",
    verdict: "BLOCKED",
    problem: "MACRO_CONTENT_BLOCKED",
    who: "Unassigned draft",
  },
  {
    id: "item-jules",
    batch: "06 Nov · Platform intake",
    jobId: "job-dse",
    filename: "jules-park.docx",
    mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    bytes: 96_440,
    status: "QUARANTINED",
    verdict: "CLEAN",
    problem: null,
    who: "Jules Park",
  },
  {
    id: "item-ren",
    batch: "06 Nov · Extraction intake",
    jobId: "job-extract",
    filename: "ren-ibarra.pdf",
    mediaType: "application/pdf",
    bytes: 512_008,
    status: "SCANNING",
    verdict: "CLEAN",
    problem: null,
    who: "Ren Ibarra",
  },
];

export const runs: ExtractionRun[] = [
  {
    id: "run-adeel",
    candidateId: "cand-adeel",
    documentTitle: "Adeel Rahman · resume",
    filename: "adeel-rahman.pdf",
    mediaType: "application/pdf",
    status: "SUCCEEDED",
    revision: 1,
    extractor: "talvora-extract-0.7.3",
    normalization: "norm-1",
    trigger: "SYSTEM_SCAN_CLEAN",
    pages: "2 of 2",
    ocrPages: 0,
    warning: null,
    segments: [
      {
        id: "seg-a1",
        locator: "PDF page 1 · digital text",
        method: "DIGITAL_TEXT",
        confidence: null,
        text: "Adeel Rahman. Toronto. Eight years on multi-tenant backends. Python, PostgreSQL, and queues that have to survive the writer that thinks it is still primary.",
      },
      {
        id: "seg-a2",
        locator: "PDF page 2 · digital text",
        method: "DIGITAL_TEXT",
        confidence: null,
        text: "Led the durable-outbox publisher for a multi-tenant event stream. p95 publish latency stayed under 180ms while lease fencing rejected stale writers after a primary failover.",
      },
    ],
    fragments: [
      {
        id: "frag-a2",
        segmentId: "seg-a2",
        quote: "lease fencing rejected stale writers after a primary failover",
        hash: "sha256:9c1e0ab4d77f…a41b",
      },
    ],
  },
  {
    id: "run-naomi",
    candidateId: "cand-naomi",
    documentTitle: "Naomi Okonkwo · scanned packet",
    filename: "naomi-clearance-scan.pdf",
    mediaType: "application/pdf",
    status: "NEEDS_REVIEW",
    revision: 2,
    extractor: "talvora-extract-0.7.3",
    normalization: "norm-1",
    trigger: "MANUAL_REPROCESS",
    pages: "1 of 1",
    ocrPages: 1,
    warning: "OCR confidence on page 1 is 0.61. The quote is addressable, and it is not yet something to hire on.",
    segments: [
      {
        id: "seg-n1",
        locator: "PDF page 1 · OCR",
        method: "OCR",
        confidence: 0.61,
        text: "Clearance status: active secret. Facility security officer to confirm. The scan is soft at the margin and should be read by a person before it is cited.",
      },
    ],
    fragments: [
      {
        id: "frag-n1",
        segmentId: "seg-n1",
        quote: "Clearance status: active secret.",
        hash: "sha256:41bb90ce12aa…09e2",
      },
    ],
  },
];

export const duplicatePairs: DuplicatePair[] = [
  {
    id: "dupe-park",
    a: "cand-jules",
    b: "cand-jpark",
    reason:
      "The normalized names overlap. The emails do not. Talvora will not merge these records, automatically or quietly.",
  },
];

export function jobById(id: string): Job | undefined {
  return jobs.find((job) => job.id === id);
}

export const BATCH_ITEM_LIMIT = 12;
