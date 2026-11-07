import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { pretty } from "./format";
import {
  INITIAL_AUDIT_LOGS,
  INITIAL_INTERVIEW_PACKS,
  INITIAL_INTERVIEWERS,
  generateDynamicInterviewPack,
} from "./mockData";

export type Me = {
  user_id: string;
  email: string;
  active_organization_id: string | null;
  role: string | null;
  capabilities: string[];
  organizations: { id: string; name: string; slug: string; role: string }[];
};

export type Job = {
  id: string;
  req_code: string;
  title: string;
  description: string;
  department: string | null;
  location: string | null;
  employment_type: string;
  status: string; // "OPEN" | "DRAFT" | "CLOSED"
  current_published_version_number: number | null;
  version: number;
  hiring_manager: string;
  salary_range: string;
  headcount: string;
  created_at: string;
  updated_at: string;
};

export type RequirementVersion = {
  id: string;
  version_number: number;
  title: string;
  description: string;
  published_at: string;
  change_summary: string | null;
  criteria: {
    id: string;
    criterion_type: string;
    is_required: boolean;
    source_requirement_text: string;
    relevance_rationale: string;
    configuration: { skill_name?: string };
  }[];
};

export type Candidate = {
  id: string;
  display_name: string | null;
  email: string | null;
  phone: string | null;
  current_company: string;
  current_title: string;
  experience_years: number;
  location: string;
  education: string;
  rating: string;
  source: string;
  is_draft: boolean;
  current_contact_revision_number: number;
  is_contact_verified: boolean;
  version: number;
  created_at: string;
};

export type Scorecard = {
  interviewer: string;
  role: string;
  verdict: string;
  notes: string;
};

export type Application = {
  id: string;
  job_id: string;
  candidate_id: string;
  stage: string;
  source: string;
  notes: string | null;
  match_score: string;
  target_compensation: string;
  scorecards: Scorecard[];
  version: number;
  created_at: string;
  updated_at: string;
};

export type StageEvent = {
  id: string;
  from_stage: string | null;
  to_stage: string;
  reason: string | null;
  actor_role: string;
  created_at: string;
};

export type HeldDocument = {
  id: string;
  filename: string;
  version: number;
  status: string;
  problem_code: string | null;
  candidate_name?: string;
  filesize?: string;
};

export type SourceView = {
  document_id: string;
  version_number: number;
  extraction: { status: string; problem_code: string | null; error_detail: string | null } | null;
  segments: {
    id: string;
    page_number: number | null;
    extraction_method: string;
    text: string;
  }[];
};

export type InterviewScheduleItem = {
  id: string;
  candidate_id?: string;
  application_id?: string;
  job_id?: string;
  time: string;
  date?: string;
  candidate_name: string;
  candidate_role: string;
  interviewer: string;
  interviewer_id?: string;
  round_title: string;
  status: "In Progress" | "Scheduled" | "Completed" | "Cancelled";
  tone: "interviewing" | "human" | "clean" | "closed";
  notes?: string;
};

export type OnboardingTask = {
  id: string;
  label: string;
  category: "Compliance & Legal" | "IT & Security" | "People & Team";
  completed: boolean;
  due_date: string;
};

export type HiredEmployee = {
  id: string;
  application_id: string;
  candidate_id: string;
  job_id: string;
  employee_code: string;
  display_name: string;
  role_title: string;
  department: string;
  hiring_manager: string;
  corporate_email: string;
  personal_email: string;
  phone: string;
  start_date: string;
  compensation_package: string;
  location: string;
  onboarding_status: "Provisioning" | "Ready for Day 1" | "Onboarded";
  checklist: OnboardingTask[];
  hiring_notes: string;
  offer_accepted_at: string;
};

export type Interviewer = {
  id: string;
  name: string;
  role: string;
  department: string;
  email: string;
  availability: "Available" | "Busy" | "Preferred";
};

export type InterviewPack = {
  id: string;
  application_id: string;
  candidate_id: string;
  job_id: string;
  generated_at: string;
  candidate_summary: string;
  key_strengths: string[];
  areas_to_probe: string[];
  technical_questions: {
    question: string;
    expected_answer: string;
    criteria_tested: string;
  }[];
  behavioral_questions: {
    question: string;
    evaluation_focus: string;
  }[];
  evaluation_rubric: {
    competency: string;
    level_1_poor: string;
    level_3_acceptable: string;
    level_5_exceptional: string;
  }[];
  is_simulated_ai: true;
};

export type AuditLog = {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  target_type: "REQUISITION" | "CANDIDATE" | "APPLICATION" | "INTERVIEW" | "DOCUMENT" | "ONBOARDING";
  target_id: string;
  target_name: string;
  details: string;
};

type Status = "loading" | "signed-out" | "ready" | "down";

const HARDCODED_ME: Me = {
  user_id: "usr_lead_recruiter_01",
  email: "lead.recruiter@talvora.test",
  active_organization_id: "org_talvora_hq",
  role: "OWNER",
  capabilities: [
    "CREATE_JOB",
    "PUBLISH_JOB_REQUIREMENTS",
    "CREATE_CANDIDATE",
    "VERIFY_CANDIDATE_CONTACT",
    "CREATE_APPLICATION",
    "MOVE_APPLICATION_STAGE",
    "TRANSITION_STAGE",
    "MANAGE_DOCUMENT_INTAKE",
    "READ_DOCUMENTS",
    "READ_SCREENING_EVIDENCE",
    "SUBMIT_FEEDBACK",
  ],
  organizations: [
    {
      id: "org_talvora_hq",
      name: "Talvora Global Talent Operations",
      slug: "talvora-hq",
      role: "OWNER",
    },
  ],
};

const INITIAL_SCHEDULE: InterviewScheduleItem[] = [
  {
    id: "sch-1",
    time: "10:00 AM",
    date: "Today",
    candidate_name: "Elena Rostova",
    candidate_role: "Staff Infrastructure Engineer",
    interviewer: "Dr. Jennifer Vance (VP Platform Engineering)",
    round_title: "Round 3: System Design & Raft Consensus Resilience",
    status: "In Progress",
    tone: "interviewing",
  },
  {
    id: "sch-2",
    time: "01:30 PM",
    date: "Today",
    candidate_name: "Marcus Vance",
    candidate_role: "Principal Distributed Systems Engineer",
    interviewer: "Alex Miller (Staff Infrastructure Architect)",
    round_title: "Round 4: High-Throughput Outbox & Monotonic Leasing",
    status: "Scheduled",
    tone: "human",
  },
  {
    id: "sch-3",
    time: "03:00 PM",
    date: "Today",
    candidate_name: "Priya Sharma, Ph.D.",
    candidate_role: "Senior AI Research Scientist",
    interviewer: "Dr. Tariq Chen (Director of AI Research)",
    round_title: "Round 2: Deterministic LLM Grounding & Retrieval Benchmarks",
    status: "Scheduled",
    tone: "human",
  },
  {
    id: "sch-4",
    time: "04:30 PM",
    date: "Today",
    candidate_name: "Chloe Bennet",
    candidate_role: "Lead Cloud Security Architect",
    interviewer: "Marcus Cole (Chief Information Security Officer)",
    round_title: "Executive Debrief & Final Compensation Sign-Off",
    status: "Scheduled",
    tone: "clean",
  },
];

const INITIAL_HIRED_EMPLOYEES: HiredEmployee[] = [
  {
    id: "hired-liam",
    application_id: "app-liam",
    candidate_id: "cand-liam",
    job_id: "job-fe",
    employee_code: "EMP-2026-042",
    display_name: "Liam O'Connor",
    role_title: "Senior Full Stack Engineer (React & TypeScript)",
    department: "Product Engineering",
    hiring_manager: "Rachel Adams (Head of Product Engineering)",
    corporate_email: "liam.oconnor@talvora.tech",
    personal_email: "liam.oconnor@modernstack.dev",
    phone: "+1 (617) 320-1944",
    start_date: "Monday, March 16, 2026",
    compensation_package: "$230,000 Base · 0.15% Equity · $25,000 Signing Bonus",
    location: "Boston, MA (Hybrid / New York Hub)",
    onboarding_status: "Ready for Day 1",
    offer_accepted_at: new Date(Date.now() - 4 * 86400000).toISOString(),
    hiring_notes:
      "Unanimous Strong Hire verdict across frontend architecture, accessibility, and state synchronization debriefs.",
    checklist: [
      {
        id: "chk-liam-1",
        label: "Signed Offer Letter & Confidentiality IP Assignment Agreement",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-liam-2",
        label: "Form I-9 Identity & US Employment Authorization Verified",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-liam-3",
        label: "Comprehensive Criminal & Employment Background Verification (Sterling)",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-liam-4",
        label: "Okta Corporate SSO Profile & 1Password Enterprise Vault Provisioned",
        category: "IT & Security",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-liam-5",
        label: "Apple MacBook Pro M3 Max (64GB RAM) Shipped via FedEx #77192840",
        category: "IT & Security",
        completed: true,
        due_date: "Delivered",
      },
      {
        id: "chk-liam-6",
        label: "GitHub Enterprise Org Seat & AWS Staging Bastion SSH Keys Enrolled",
        category: "IT & Security",
        completed: false,
        due_date: "Day 1 Morning",
      },
      {
        id: "chk-liam-7",
        label: "Talvora Welcome Swag Kit, Corporate Amex & Desk Setup Stipend Dispatched",
        category: "People & Team",
        completed: true,
        due_date: "Delivered",
      },
      {
        id: "chk-liam-8",
        label: "Assigned Peer Onboarding Buddy (Rachel Adams) & Day 1 Team Lunch Scheduled",
        category: "People & Team",
        completed: true,
        due_date: "March 16 12:30 PM",
      },
      {
        id: "chk-liam-9",
        label: "30-60-90 Day Product Engineering Execution Roadmap Published",
        category: "People & Team",
        completed: false,
        due_date: "Week 1 Friday",
      },
    ],
  },
  {
    id: "hired-amara",
    application_id: "app-amara-hired",
    candidate_id: "cand-amara",
    job_id: "job-sre",
    employee_code: "EMP-2026-041",
    display_name: "Amara Okafor",
    role_title: "Staff Site Reliability & Cloud Security Architect",
    department: "Cloud Security",
    hiring_manager: "Marcus Cole (Chief Information Security Officer)",
    corporate_email: "amara.okafor@talvora.tech",
    personal_email: "amara.okafor@cloudscale-africa.io",
    phone: "+44 (20) 7946-0922",
    start_date: "Monday, March 23, 2026",
    compensation_package: "$300,000 Base · 0.20% Equity · $35,000 Signing Bonus",
    location: "London, UK (Remote / Sovereign EU Cloud)",
    onboarding_status: "Provisioning",
    offer_accepted_at: new Date(Date.now() - 7 * 86400000).toISOString(),
    hiring_notes:
      "Exemplary track record managing multi-region Kubernetes clusters with zero unscheduled downtime at Uber Core.",
    checklist: [
      {
        id: "chk-am-1",
        label: "Signed Offer Letter & Global IP Assignment Agreement",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-am-2",
        label: "UK Right-to-Work Biometric Residence Verification & KYC Cleared",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-am-3",
        label: "Global Security Clearance & Sanctions Background Check Cleared",
        category: "Compliance & Legal",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-am-4",
        label: "Corporate Okta & Zero-Trust Teleport Bastion Access Configured",
        category: "IT & Security",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-am-5",
        label: "YubiKey 5C NFC Dual Hardware Security Keys Dispatched (DHL Express)",
        category: "IT & Security",
        completed: false,
        due_date: "In-Transit",
      },
      {
        id: "chk-am-6",
        label: "Dell Precision Workstation Linux Rig Ordered for Sovereign Boundary",
        category: "IT & Security",
        completed: false,
        due_date: "Est. March 19",
      },
      {
        id: "chk-am-7",
        label: "Welcome Onboarding Pack & Ergonomic Home Office Stipend Released",
        category: "People & Team",
        completed: true,
        due_date: "Completed",
      },
      {
        id: "chk-am-8",
        label: "Executive Welcome 1:1 with Chief Information Security Officer Booked",
        category: "People & Team",
        completed: false,
        due_date: "March 23 10:00 AM",
      },
    ],
  },
];

const INITIAL_JOBS: Job[] = [
  {
    id: "job-sre",
    req_code: "REQ-2026-401",
    title: "Principal Site Reliability & Cloud Security Architect",
    description:
      "Establish hardened defense-in-depth boundaries, multi-region database failover, and ephemeral credential rotations across AWS sovereign clusters.",
    department: "Cloud Security",
    location: "Remote (Global)",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 1,
    version: 1,
    hiring_manager: "Marcus Cole (CISO)",
    salary_range: "$285,000 – $345,000 + Equity",
    headcount: "1 Open (1 Hired)",
    created_at: new Date(Date.now() - 12 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 6 * 86400000).toISOString(),
  },
  {
    id: "job-dist",
    req_code: "REQ-2026-308",
    title: "Senior Distributed Systems Engineer",
    description:
      "Architect and scale Talvora's durable transactional outbox, partitioned event streams, and monotonic lease fencing pipelines to sustain high-volume global operations.",
    department: "Platform Infrastructure",
    location: "Remote (US / Canada)",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 2,
    version: 2,
    hiring_manager: "Dr. Jennifer Vance (VP Platform Engineering)",
    salary_range: "$225,000 – $275,000 + Equity",
    headcount: "2 Open (0 Hired)",
    created_at: new Date(Date.now() - 25 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 3 * 86400000).toISOString(),
  },
  {
    id: "job-ai",
    req_code: "REQ-2026-215",
    title: "Staff Machine Learning & LLM Systems Architect",
    description:
      "Lead the design of bounded, hallucination-resistant LLM analysis engines and cited evidence extraction pipelines with exact Unicode offset grounding.",
    department: "Core AI Research",
    location: "San Francisco, CA (Hybrid)",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 1,
    version: 1,
    hiring_manager: "Dr. Tariq Chen (Director of AI Research)",
    salary_range: "$310,000 – $380,000 + Equity",
    headcount: "1 Open (0 Hired)",
    created_at: new Date(Date.now() - 28 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 15 * 86400000).toISOString(),
  },
  {
    id: "job-fe",
    req_code: "REQ-2026-192",
    title: "Senior Full Stack Engineer (React & TypeScript)",
    description:
      "Build modern, accessible talent operations workspaces using React 19, TypeScript, and high-density operational data tables with zero layout jitter.",
    department: "Product Engineering",
    location: "New York, NY (Hybrid)",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 1,
    version: 1,
    hiring_manager: "Rachel Adams (Head of Product Engineering)",
    salary_range: "$195,000 – $240,000 + Equity",
    headcount: "1 Open (1 Hired)",
    created_at: new Date(Date.now() - 35 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 20 * 86400000).toISOString(),
  },
  {
    id: "job-ta",
    req_code: "REQ-2026-104",
    title: "Lead Talent Acquisition Partner (Executive Search)",
    description:
      "Manage executive hiring pipelines, leadership assessment frameworks, and strategic talent outreach across engineering, product, and AI research.",
    department: "People Operations",
    location: "London / Remote",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 1,
    version: 1,
    hiring_manager: "Eleanor Sterling (VP People & Culture)",
    salary_range: "$180,000 – $225,000 + Incentive Bonus",
    headcount: "1 Open (0 Hired)",
    created_at: new Date(Date.now() - 16 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 8 * 86400000).toISOString(),
  },
  {
    id: "job-rust",
    req_code: "REQ-2026-512",
    title: "Staff Systems Performance & Kernel Engineer",
    description:
      "Design zero-copy socket multiplexing, eBPF tracing probes, and low-latency disk serialization engines for our distributed transaction journal.",
    department: "Core Systems",
    location: "Zurich / Remote",
    employment_type: "FULL_TIME",
    status: "OPEN",
    current_published_version_number: 1,
    version: 1,
    hiring_manager: "Dr. Klaus Richter (Chief Architect)",
    salary_range: "$250,000 – $305,000 + Equity",
    headcount: "1 Open (0 Hired)",
    created_at: new Date(Date.now() - 14 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 7 * 86400000).toISOString(),
  },
  {
    id: "job-draft",
    req_code: "REQ-2026-620",
    title: "Staff Quantum-Resistant Cryptography Specialist",
    description:
      "Evaluate post-quantum key encapsulation algorithms (ML-KEM) and stateful hash-based signatures across internal storage envelopes.",
    department: "Information Security",
    location: "Remote (Global)",
    employment_type: "FULL_TIME",
    status: "DRAFT",
    current_published_version_number: null,
    version: 1,
    hiring_manager: "Marcus Cole (CISO)",
    salary_range: "$270,000 – $330,000 + Equity",
    headcount: "1 Open (0 Hired)",
    created_at: new Date(Date.now() - 3 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
];

const INITIAL_REQUIREMENT_VERSIONS: Record<string, RequirementVersion[]> = {
  "job-sre": [
    {
      id: "ver-sre-1",
      version_number: 1,
      title: "Cloud Security & Resilience Architecture Standards v1",
      description: "Infrastructure resilience, zero-trust policies, and multi-region recovery standards.",
      published_at: new Date(Date.now() - 6 * 86400000).toISOString(),
      change_summary: "Approved production governance criteria for SRE Architect vacancy.",
      criteria: [
        {
          id: "crit-sre-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 7 years (84 months) leading SRE or Cloud Platform Security initiatives.",
          relevance_rationale: "Guarantees deep architectural ownership of mission-critical sovereign clusters.",
          configuration: { skill_name: "Cloud Security & EKS" },
        },
        {
          id: "crit-sre-2",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Mastery of Kubernetes (EKS), Terraform, zero-trust IAM policies, and eBPF.",
          relevance_rationale: "Core operational baseline for Talvora's container orchestration plane.",
          configuration: { skill_name: "Kubernetes & Infrastructure-as-Code" },
        },
      ],
    },
  ],
  "job-dist": [
    {
      id: "ver-dist-2",
      version_number: 2,
      title: "Senior Distributed Systems Criteria v2 (Monotonic Leasing Update)",
      description: "Refined criteria prioritizing lease fencing and split-brain recovery.",
      published_at: new Date(Date.now() - 3 * 86400000).toISOString(),
      change_summary: "Snapshot v2: Added monotonic lease fencing and high-throughput Kafka stream compaction.",
      criteria: [
        {
          id: "crit-dist-2-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 5 years (60 months) building high-concurrency backend services.",
          relevance_rationale: "Essential for designing distributed consensus and resolving complex concurrency races.",
          configuration: { skill_name: "High-Concurrency Backend" },
        },
        {
          id: "crit-dist-2-2",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Demonstrated expertise in Python 3.12+, FastAPI, and SQLAlchemy async query execution.",
          relevance_rationale: "Primary backend architecture is built upon Python and ASGI.",
          configuration: { skill_name: "Python & AsyncIO" },
        },
        {
          id: "crit-dist-2-3",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Experience with distributed consensus protocols (Raft, Paxos) and monotonic lease fencing.",
          relevance_rationale: "Prevents split-brain state mutations during network partitions.",
          configuration: { skill_name: "Distributed Consensus & Leasing" },
        },
      ],
    },
  ],
};

const INITIAL_CANDIDATES: Candidate[] = [
  {
    id: "cand-elena",
    display_name: "Elena Rostova",
    email: "elena.rostova@cloudscale.io",
    phone: "+1 (415) 890-2341",
    current_company: "Datadog",
    current_title: "Staff SRE & Infrastructure Architect",
    experience_years: 9,
    location: "San Francisco, CA",
    education: "M.S. Computer Science, Stanford University",
    rating: "★ 4.9 · Strong Hire",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 14 * 86400000).toISOString(),
  },
  {
    id: "cand-marcus",
    display_name: "Marcus Vance",
    email: "marcus.vance@distributedkernel.net",
    phone: "+1 (206) 555-0182",
    current_company: "Amazon Web Services (AWS)",
    current_title: "Principal Distributed Systems Engineer",
    experience_years: 11,
    location: "Seattle, WA",
    education: "B.S. Computer Engineering, University of Washington",
    rating: "★ 5.0 · Strong Hire",
    source: "MANUAL_ENTRY",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 18 * 86400000).toISOString(),
  },
  {
    id: "cand-priya",
    display_name: "Priya Sharma, Ph.D.",
    email: "priya.sharma@mlfoundations.org",
    phone: "+1 (650) 441-9872",
    current_company: "Google DeepMind",
    current_title: "Senior AI Research Scientist",
    experience_years: 8,
    location: "Palo Alto, CA",
    education: "Ph.D. Machine Learning, UC Berkeley",
    rating: "★ 4.9 · Strong Hire",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 10 * 86400000).toISOString(),
  },
  {
    id: "cand-chloe",
    display_name: "Chloe Bennet",
    email: "chloe.bennet@defenseinfras.org",
    phone: "+1 (703) 980-1144",
    current_company: "Palantir Technologies",
    current_title: "Lead Cloud Defense & Zero-Trust Architect",
    experience_years: 10,
    location: "Washington, D.C.",
    education: "M.S. Information Security, Carnegie Mellon",
    rating: "★ 4.9 · Strong Hire",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 11 * 86400000).toISOString(),
  },
  {
    id: "cand-liam",
    display_name: "Liam O'Connor",
    email: "liam.oconnor@modernstack.dev",
    phone: "+1 (617) 320-1944",
    current_company: "Stripe",
    current_title: "Staff Frontend Architect",
    experience_years: 7,
    location: "Boston, MA",
    education: "B.S. Software Engineering, MIT",
    rating: "★ 4.9 · Offer Accepted",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 25 * 86400000).toISOString(),
  },
  {
    id: "cand-david",
    display_name: "David Chen",
    email: "david.chen@enterprisecloud.com",
    phone: "+1 (212) 809-5412",
    current_company: "Cloudflare",
    current_title: "Senior Backend Concurrency Engineer",
    experience_years: 6,
    location: "New York, NY",
    education: "B.S. Computer Science, Columbia University",
    rating: "★ 4.6 · Screen Pending",
    source: "MANUAL_ENTRY",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: false,
    version: 1,
    created_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
];

const INITIAL_APPLICATIONS: Application[] = [
  {
    id: "app-elena",
    job_id: "job-dist",
    candidate_id: "cand-elena",
    stage: "INTERVIEWING",
    source: "UPLOAD_INTAKE",
    match_score: "98% Match (All Criteria Verified)",
    target_compensation: "$265,000 Base + 0.15% Equity",
    notes: "Cleared distributed systems screening. System design loop in progress.",
    scorecards: [
      {
        interviewer: "Dr. Jennifer Vance",
        role: "VP Platform Engineering",
        verdict: "Strong Hire",
        notes: "Articulated split-brain recovery in Raft consensus with exemplary clarity.",
      },
    ],
    version: 4,
    created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "app-marcus",
    job_id: "job-dist",
    candidate_id: "cand-marcus",
    stage: "DECISION_PENDING",
    source: "MANUAL_ENTRY",
    match_score: "100% Match (All Criteria Verified)",
    target_compensation: "$280,000 Base + 0.20% Equity",
    notes: "Completed 4-stage interview loop. Exceptional Raft consensus algorithm proficiency.",
    scorecards: [
      {
        interviewer: "Dr. Jennifer Vance",
        role: "VP Platform Engineering",
        verdict: "Strong Hire",
        notes: "Core open-source contributor to Raft. Exceptional grasp of distributed log storage.",
      },
    ],
    version: 3,
    created_at: new Date(Date.now() - 12 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
  {
    id: "app-chloe",
    job_id: "job-sre",
    candidate_id: "cand-chloe",
    stage: "DECISION_PENDING",
    source: "UPLOAD_INTAKE",
    match_score: "96% Match (All Criteria Verified)",
    target_compensation: "$320,000 Base + 0.25% Equity",
    notes: "Cleared Principal SRE debrief with 4 Strong Hire verdicts.",
    scorecards: [
      {
        interviewer: "Marcus Cole",
        role: "CISO",
        verdict: "Strong Hire",
        notes: "Led zero-trust architecture across 12 sovereign AWS regions at Palantir.",
      },
    ],
    version: 4,
    created_at: new Date(Date.now() - 10 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 12 * 3600000).toISOString(),
  },
  {
    id: "app-priya",
    job_id: "job-ai",
    candidate_id: "cand-priya",
    stage: "INTERVIEW_APPROVED",
    source: "UPLOAD_INTAKE",
    match_score: "99% Match (Criteria Met)",
    target_compensation: "$340,000 Base + 0.25% Equity",
    notes: "Approved by VP of AI Research for technical round.",
    scorecards: [],
    version: 2,
    created_at: new Date(Date.now() - 6 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "app-liam",
    job_id: "job-fe",
    candidate_id: "cand-liam",
    stage: "HIRED",
    source: "UPLOAD_INTAKE",
    match_score: "99% Match (Criteria Met)",
    target_compensation: "$230,000 Base + 0.15% Equity (Accepted)",
    notes: "Offer formally accepted. Onboarding workflow initiated with corporate engineering team.",
    scorecards: [
      {
        interviewer: "Rachel Adams",
        role: "Head of Product Eng",
        verdict: "Strong Hire",
        notes: "Exceptional design-engineering craftsmanship and accessibility leadership.",
      },
    ],
    version: 2,
    created_at: new Date(Date.now() - 20 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 4 * 3600000).toISOString(),
  },
  {
    id: "app-david",
    job_id: "job-dist",
    candidate_id: "cand-david",
    stage: "NEW",
    source: "MANUAL_ENTRY",
    match_score: "89% Match (Criteria Met)",
    target_compensation: "$235,000 Base + 0.12% Equity",
    notes: "Requires contact verification before scheduling initial phone screen.",
    scorecards: [],
    version: 1,
    created_at: new Date(Date.now() - 6 * 3600000).toISOString(),
    updated_at: new Date(Date.now() - 6 * 3600000).toISOString(),
  },
];

const INITIAL_DOCUMENTS: HeldDocument[] = [
  {
    id: "doc-elena",
    filename: "elena_rostova_staff_infra.pdf",
    candidate_name: "Elena Rostova",
    filesize: "482 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
  {
    id: "doc-marcus",
    filename: "marcus_vance_principal_dist.pdf",
    candidate_name: "Marcus Vance",
    filesize: "520 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
  {
    id: "doc-priya",
    filename: "dr_priya_sharma_ai_research.pdf",
    candidate_name: "Priya Sharma, Ph.D.",
    filesize: "610 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
];

const INITIAL_SOURCES: Record<string, SourceView> = {
  "doc-elena": {
    document_id: "doc-elena",
    version_number: 1,
    extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
    segments: [
      {
        id: "seg-elena-1",
        page_number: 1,
        extraction_method: "DIGITAL_TEXT",
        text: "ELENA ROSTOVA — Staff Infrastructure Architect & Site Reliability Engineer\nSan Francisco, CA · elena.rostova@cloudscale.io · +1 (415) 890-2341\n\nPROFESSIONAL SUMMARY\n9+ years scaling distributed systems infrastructure and zero-trust cloud perimeters. Spearheaded active-active database failovers across AWS us-east-1 and eu-west-1, sustaining 99.999% SLA across 40M daily transactions. Deep expertise in monotonic lease fencing, partitioned event streams, and Raft consensus engines.\n\nTECHNICAL MASTERY\n• Distributed Protocols: Raft, Paxos, Transactional Outbox, Monotonic Fencing, Event Sourcing\n• Cloud Platforms & Security: AWS (EKS, VPC CNI, KMS, IAM Zero-Trust), HashiCorp Vault, SPIFFE/SPIRE\n• Core Languages: Python 3.12+ (AsyncIO, FastAPI), Rust, Go, PostgreSQL ACID internals, Apache Kafka, Redis",
      },
      {
        id: "seg-elena-2",
        page_number: 2,
        extraction_method: "DIGITAL_TEXT",
        text: "NOTABLE PRODUCTION ACCOMPLISHMENTS\nStaff Infrastructure Architect — Datadog / CloudScale Core (2021 – Present)\n• Designed and deployed multi-region active-active database failover across AWS us-east-1 and eu-west-1, eliminating split-brain anomalies during chaos simulation testing.\n• Authored high-throughput transactional outbox service in Python 3.12 and AsyncIO with monotonic lease fencing, sustaining 65,000 TPS under simulated network partitions.\n• Implemented automated credential rotation and secret-less container identity with HashiCorp Vault and SPIFFE/SPIRE.",
      },
    ],
  },
  "doc-marcus": {
    document_id: "doc-marcus",
    version_number: 1,
    extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
    segments: [
      {
        id: "seg-marcus-1",
        page_number: 1,
        extraction_method: "DIGITAL_TEXT",
        text: "MARCUS VANCE — Principal Distributed Systems Engineer\nSeattle, WA · marcus.vance@distributedkernel.net · +1 (206) 555-0182\n\nEXECUTIVE BIO\n11+ years optimizing consensus protocols, transactional outboxes, and high-concurrency low-latency network primitives. Core open-source contributor to Paxos and Raft implementations. Principal engineer leading large-scale multi-region event streams at Amazon Web Services.",
      },
    ],
  },
  "doc-priya": {
    document_id: "doc-priya",
    version_number: 1,
    extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
    segments: [
      {
        id: "seg-priya-1",
        page_number: 1,
        extraction_method: "DIGITAL_TEXT",
        text: "PRIYA SHARMA, Ph.D. — Senior AI Research Scientist & LLM Systems Architect\nPalo Alto, CA · priya.sharma@mlfoundations.org · +1 (650) 441-9872\n\nSPECIALIZATION\nApplied AI research architect focusing on bounded hallucination mitigation, deterministic grounding with exact Unicode offsets, and low-latency inference serving for LLM agents. Ph.D. from UC Berkeley AI Research Lab.",
      },
    ],
  },
};

const INITIAL_STAGE_HISTORIES: Record<string, StageEvent[]> = {
  "app-elena": [
    {
      id: "evt-elena-1",
      from_stage: null,
      to_stage: "NEW",
      reason: "Resume uploaded and extracted via quarantine pipeline.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
    },
    {
      id: "evt-elena-2",
      from_stage: "NEW",
      to_stage: "IN_REVIEW",
      reason: "Initial criteria verification matched 9+ years backend and Raft consensus experience.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 7 * 86400000).toISOString(),
    },
    {
      id: "evt-elena-3",
      from_stage: "IN_REVIEW",
      to_stage: "INTERVIEW_APPROVED",
      reason: "Hiring Manager approved candidate for architecture panel rounds.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
    },
    {
      id: "evt-elena-4",
      from_stage: "INTERVIEW_APPROVED",
      to_stage: "INTERVIEWING",
      reason: "System design loop scheduled with Principal SRE and VP Engineering.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    },
  ],
  "app-marcus": [
    {
      id: "evt-marcus-1",
      from_stage: null,
      to_stage: "NEW",
      reason: "Direct recruiter referral intake.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 12 * 86400000).toISOString(),
    },
    {
      id: "evt-marcus-2",
      from_stage: "NEW",
      to_stage: "INTERVIEWING",
      reason: "Fast-tracked due to open-source contributions to Raft consensus engines.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 8 * 86400000).toISOString(),
    },
    {
      id: "evt-marcus-3",
      from_stage: "INTERVIEWING",
      to_stage: "DECISION_PENDING",
      reason: "Final debrief completed. Awaiting compensation committee sign-off.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 1 * 86400000).toISOString(),
    },
  ],
};

const STORAGE_KEY = "talvora_recruiting_ops_store_v2";

type StorageShape = {
  jobs: Job[];
  candidates: Candidate[];
  applications: Application[];
  documents: HeldDocument[];
  schedule: InterviewScheduleItem[];
  hiredEmployees: HiredEmployee[];
  requirementVersions: Record<string, RequirementVersion[]>;
  histories: Record<string, StageEvent[]>;
  sources: Record<string, SourceView>;
  auditLogs: AuditLog[];
  interviewPacks: Record<string, InterviewPack>;
};

function loadStoredState(): StorageShape | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as StorageShape;
  } catch {
    return null;
  }
}

export type Workspace = {
  status: Status;
  me: Me | null;
  jobs: Job[];
  candidates: Candidate[];
  applications: Application[];
  documents: HeldDocument[];
  schedule: InterviewScheduleItem[];
  hiredEmployees: HiredEmployee[];
  interviewers: Interviewer[];
  interviewPacks: Record<string, InterviewPack>;
  auditLogs: AuditLog[];
  notice: string | null;
  error: string | null;
  can: (capability: string) => boolean;
  dismissNotice: () => void;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  // Requisitions
  createJob: (input: {
    title: string;
    description: string;
    department: string;
    location: string;
    skill: string;
    publish: boolean;
  }) => Promise<void>;
  updateJob: (jobId: string, updates: Partial<Job>) => Promise<void>;
  publishJob: (jobId: string, skillName: string, requirementText: string) => Promise<void>;
  closeJob: (jobId: string, reason?: string) => Promise<void>;
  reopenJob: (jobId: string) => Promise<void>;
  // Candidates
  createCandidate: (input: { display_name: string; email: string }) => Promise<void>;
  updateCandidate: (candidateId: string, updates: Partial<Candidate>) => Promise<void>;
  attest: (candidate: Candidate, notes: string) => Promise<void>;
  // Applications & Pipeline
  createApplication: (jobId: string, candidateId: string) => Promise<void>;
  moveStage: (application: Application, target: string, reason: string) => Promise<void>;
  addScorecard: (applicationId: string, scorecard: Scorecard) => Promise<void>;
  // Interview Packs & Scheduling
  generateInterviewPack: (applicationId: string) => Promise<InterviewPack>;
  scheduleInterview: (input: {
    candidate_name: string;
    candidate_role: string;
    interviewer: string;
    round_title: string;
    time: string;
    date?: string;
    candidate_id?: string;
    application_id?: string;
    notes?: string;
  }) => Promise<void>;
  rescheduleInterview: (interviewId: string, newTime: string, newDate?: string) => Promise<void>;
  cancelInterview: (interviewId: string, reason: string) => Promise<void>;
  // Intake & Onboarding
  uploadResume: (jobId: string, file: File) => Promise<void>;
  toggleChecklistItem: (employeeId: string, itemId: string) => void;
  // Audit Trail & Persistence
  logActivity: (entry: Omit<AuditLog, "id" | "timestamp">) => void;
  resetDemoData: () => void;
  // Loaders
  loadVersions: (jobId: string) => Promise<RequirementVersion[]>;
  loadHistory: (applicationId: string) => Promise<StageEvent[]>;
  loadEligibility: (applicationId: string) => Promise<{ eligible: boolean; reasons: string[] }>;
  loadSource: (documentId: string, version: number) => Promise<SourceView>;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const stored = useMemo(() => loadStoredState(), []);

  const [status, setStatus] = useState<Status>("ready");
  const [me, setMe] = useState<Me | null>(HARDCODED_ME);
  const [jobs, setJobs] = useState<Job[]>(() => stored?.jobs ?? INITIAL_JOBS);
  const [candidates, setCandidates] = useState<Candidate[]>(() => stored?.candidates ?? INITIAL_CANDIDATES);
  const [applications, setApplications] = useState<Application[]>(() => stored?.applications ?? INITIAL_APPLICATIONS);
  const [documents, setDocuments] = useState<HeldDocument[]>(() => stored?.documents ?? INITIAL_DOCUMENTS);
  const [schedule, setSchedule] = useState<InterviewScheduleItem[]>(() => stored?.schedule ?? INITIAL_SCHEDULE);
  const [hiredEmployees, setHiredEmployees] = useState<HiredEmployee[]>(() => stored?.hiredEmployees ?? INITIAL_HIRED_EMPLOYEES);
  const [interviewers] = useState<Interviewer[]>(INITIAL_INTERVIEWERS);
  const [requirementVersions, setRequirementVersions] = useState<Record<string, RequirementVersion[]>>(
    () => stored?.requirementVersions ?? INITIAL_REQUIREMENT_VERSIONS,
  );
  const [histories, setHistories] = useState<Record<string, StageEvent[]>>(
    () => stored?.histories ?? INITIAL_STAGE_HISTORIES,
  );
  const [sources, setSources] = useState<Record<string, SourceView>>(() => stored?.sources ?? INITIAL_SOURCES);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>(() => stored?.auditLogs ?? INITIAL_AUDIT_LOGS);
  const [interviewPacks, setInterviewPacks] = useState<Record<string, InterviewPack>>(
    () => stored?.interviewPacks ?? INITIAL_INTERVIEW_PACKS,
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sync state changes to LocalStorage
  useEffect(() => {
    try {
      const payload: StorageShape = {
        jobs,
        candidates,
        applications,
        documents,
        schedule,
        hiredEmployees,
        requirementVersions,
        histories,
        sources,
        auditLogs,
        interviewPacks,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Storage quota or disabled in private browsing
    }
  }, [
    jobs,
    candidates,
    applications,
    documents,
    schedule,
    hiredEmployees,
    requirementVersions,
    histories,
    sources,
    auditLogs,
    interviewPacks,
  ]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const can = useCallback((_capability: string) => true, []);
  const dismissNotice = useCallback(() => setNotice(null), []);

  const logActivity = useCallback((entry: Omit<AuditLog, "id" | "timestamp">) => {
    const newLog: AuditLog = {
      ...entry,
      id: `aud-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
    };
    setAuditLogs((prev) => [newLog, ...prev]);
  }, []);

  const signIn = useCallback(async () => {
    setMe(HARDCODED_ME);
    setStatus("ready");
    setNotice("Session opened for lead.recruiter@talvora.test");
  }, []);

  const signOut = useCallback(async () => {
    setMe(HARDCODED_ME);
    setNotice("Session renewed for demo operations.");
  }, []);

  const refresh = useCallback(async () => {
    setError(null);
    setStatus("ready");
  }, []);

  const resetDemoData = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setJobs(INITIAL_JOBS);
    setCandidates(INITIAL_CANDIDATES);
    setApplications(INITIAL_APPLICATIONS);
    setDocuments(INITIAL_DOCUMENTS);
    setSchedule(INITIAL_SCHEDULE);
    setHiredEmployees(INITIAL_HIRED_EMPLOYEES);
    setRequirementVersions(INITIAL_REQUIREMENT_VERSIONS);
    setHistories(INITIAL_STAGE_HISTORIES);
    setSources(INITIAL_SOURCES);
    setAuditLogs(INITIAL_AUDIT_LOGS);
    setInterviewPacks(INITIAL_INTERVIEW_PACKS);
    setNotice("Recruitment operations data restored to baseline demo state.");
  }, []);

  // Requisitions Lifecycle
  const createJob = useCallback(
    async (input: {
      title: string;
      description: string;
      department: string;
      location: string;
      skill: string;
      publish: boolean;
    }) => {
      const newId = `job-custom-${Date.now().toString(36)}`;
      const reqNum = Math.floor(100 + Math.random() * 900);
      const newJob: Job = {
        id: newId,
        req_code: `REQ-2026-${reqNum}`,
        title: input.title,
        description: input.description,
        department: input.department || "Platform Operations",
        location: input.location || "Remote",
        employment_type: "FULL_TIME",
        status: input.publish ? "OPEN" : "DRAFT",
        current_published_version_number: input.publish ? 1 : null,
        version: 1,
        hiring_manager: "Marcus Cole (Hiring Authority)",
        salary_range: "$220,000 – $280,000 + Equity",
        headcount: "1 Open (0 Hired)",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      setJobs((prev) => [newJob, ...prev]);

      if (input.publish) {
        const ver: RequirementVersion = {
          id: `ver-${newId}-1`,
          version_number: 1,
          title: `${input.title} Standards Framework v1`,
          description: input.description,
          published_at: new Date().toISOString(),
          change_summary: `Initial governance snapshot published for ${input.title}.`,
          criteria: [
            {
              id: `crit-${newId}-1`,
              criterion_type: "SKILL_PRESENT",
              is_required: true,
              source_requirement_text: `Demonstrated professional proficiency in ${input.skill || "Core Systems Engineering"}.`,
              relevance_rationale: "Required qualification for initial candidate screening.",
              configuration: { skill_name: input.skill || "Core Systems Engineering" },
            },
          ],
        };
        setRequirementVersions((prev) => ({ ...prev, [newId]: [ver] }));
      }

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: input.publish ? "REQUISITION_PUBLISHED" : "REQUISITION_CREATED",
        target_type: "REQUISITION",
        target_id: newId,
        target_name: input.title,
        details: input.publish
          ? `Created and published vacancy ${newJob.req_code} as open rule.`
          : `Created draft vacancy ${newJob.req_code}.`,
      });

      setNotice(`${input.title} is ${input.publish ? "published and open" : "saved as draft"}.`);
    },
    [logActivity],
  );

  const updateJob = useCallback(
    async (jobId: string, updates: Partial<Job>) => {
      setJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, ...updates, version: j.version + 1, updated_at: new Date().toISOString() } : j)),
      );
      const targetJob = jobs.find((j) => j.id === jobId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "REQUISITION_UPDATED",
        target_type: "REQUISITION",
        target_id: jobId,
        target_name: targetJob?.title || jobId,
        details: `Updated requisition parameters (version ${((targetJob?.version || 1) + 1)}).`,
      });
      setNotice(`Requisition updated successfully.`);
    },
    [jobs, logActivity],
  );

  const publishJob = useCallback(
    async (jobId: string, skillName: string, requirementText: string) => {
      const job = jobs.find((j) => j.id === jobId);
      if (!job) return;
      const nextVer = (job.current_published_version_number || 0) + 1;
      const newVersion: RequirementVersion = {
        id: `ver-${jobId}-${nextVer}`,
        version_number: nextVer,
        title: `${job.title} Criteria Framework v${nextVer}`,
        description: requirementText || "Published requirement baseline.",
        published_at: new Date().toISOString(),
        change_summary: `Snapshot v${nextVer}: published by talent operations governance.`,
        criteria: [
          {
            id: `crit-${jobId}-${nextVer}-1`,
            criterion_type: "SKILL_PRESENT",
            is_required: true,
            source_requirement_text: requirementText || `Mastery of ${skillName || "Core Engineering Discipline"}.`,
            relevance_rationale: "Core qualification for applicant evaluation.",
            configuration: { skill_name: skillName || "Core Skill" },
          },
        ],
      };

      setRequirementVersions((prev) => ({
        ...prev,
        [jobId]: [newVersion, ...(prev[jobId] || [])],
      }));

      setJobs((prev) =>
        prev.map((j) =>
          j.id === jobId
            ? {
                ...j,
                status: "OPEN",
                current_published_version_number: nextVer,
                version: j.version + 1,
                updated_at: new Date().toISOString(),
              }
            : j,
        ),
      );

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "REQUISITION_PUBLISHED",
        target_type: "REQUISITION",
        target_id: jobId,
        target_name: job.title,
        details: `Published criteria snapshot v${nextVer} for ${job.title}. Vacancy is law.`,
      });

      setNotice(`${job.title} criteria v${nextVer} published. Vacancy is now open.`);
    },
    [jobs, logActivity],
  );

  const closeJob = useCallback(
    async (jobId: string, reason?: string) => {
      setJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, status: "CLOSED", updated_at: new Date().toISOString() } : j)),
      );
      const targetJob = jobs.find((j) => j.id === jobId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "REQUISITION_CLOSED",
        target_type: "REQUISITION",
        target_id: jobId,
        target_name: targetJob?.title || jobId,
        details: reason ? `Closed vacancy: ${reason}` : "Requisition closed by talent operations.",
      });
      setNotice(`Requisition closed.`);
    },
    [jobs, logActivity],
  );

  const reopenJob = useCallback(
    async (jobId: string) => {
      setJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, status: "OPEN", updated_at: new Date().toISOString() } : j)),
      );
      const targetJob = jobs.find((j) => j.id === jobId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "REQUISITION_REOPENED",
        target_type: "REQUISITION",
        target_id: jobId,
        target_name: targetJob?.title || jobId,
        details: "Reopened requisition for candidate application intake.",
      });
      setNotice(`Requisition reopened.`);
    },
    [jobs, logActivity],
  );

  // Candidates Lifecycle
  const createCandidate = useCallback(
    async (input: { display_name: string; email: string }) => {
      const newId = `cand-custom-${Date.now().toString(36)}`;
      const newCand: Candidate = {
        id: newId,
        display_name: input.display_name,
        email: input.email,
        phone: "+1 (555) 019-8234",
        current_company: "Independent / Sourced Candidate",
        current_title: "Senior Software Engineer",
        experience_years: 6,
        location: "Remote (US)",
        education: "B.S. Computer Science",
        rating: "★ 4.5 · New Candidate",
        source: "MANUAL_ENTRY",
        is_draft: false,
        current_contact_revision_number: 1,
        is_contact_verified: false,
        version: 1,
        created_at: new Date().toISOString(),
      };
      setCandidates((prev) => [newCand, ...prev]);

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "CANDIDATE_CREATED",
        target_type: "CANDIDATE",
        target_id: newId,
        target_name: input.display_name,
        details: `Admitted candidate profile for ${input.email} via manual intake.`,
      });

      setNotice(`${input.display_name} is on the record.`);
    },
    [logActivity],
  );

  const updateCandidate = useCallback(
    async (candidateId: string, updates: Partial<Candidate>) => {
      setCandidates((prev) =>
        prev.map((c) => (c.id === candidateId ? { ...c, ...updates, version: c.version + 1 } : c)),
      );
      const cand = candidates.find((c) => c.id === candidateId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "CANDIDATE_UPDATED",
        target_type: "CANDIDATE",
        target_id: candidateId,
        target_name: cand?.display_name || candidateId,
        details: `Updated candidate dossier profile details.`,
      });
      setNotice(`Candidate profile updated.`);
    },
    [candidates, logActivity],
  );

  const attest = useCallback(
    async (candidate: Candidate, notes: string) => {
      setCandidates((prev) =>
        prev.map((c) =>
          c.id === candidate.id
            ? { ...c, is_contact_verified: true, version: c.version + 1, current_contact_revision_number: c.current_contact_revision_number + 1 }
            : c,
        ),
      );

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "CANDIDATE_ATTESTED",
        target_type: "CANDIDATE",
        target_id: candidate.id,
        target_name: candidate.display_name || candidate.email || "Candidate",
        details: `Attested contact identity: ${notes || "Verified identity and email address on file."}`,
      });

      setNotice(`Attestation recorded for ${candidate.display_name || candidate.email}. Verified address.`);
    },
    [logActivity],
  );

  // Applications & Human Decisions
  const createApplication = useCallback(
    async (jobId: string, candidateId: string) => {
      const cand = candidates.find((c) => c.id === candidateId);
      const job = jobs.find((j) => j.id === jobId);
      const newId = `app-custom-${Date.now().toString(36)}`;
      const newApp: Application = {
        id: newId,
        job_id: jobId,
        candidate_id: candidateId,
        stage: "NEW",
        source: "MANUAL_ENTRY",
        match_score: "92% Match (Criteria Met)",
        target_compensation: "$240,000 Base + Equity",
        notes: "Direct application intake.",
        scorecards: [],
        version: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      const initialEvt: StageEvent = {
        id: `evt-${newId}-1`,
        from_stage: null,
        to_stage: "NEW",
        reason: "Manual recruiter referral admission.",
        actor_role: "RECRUITER",
        created_at: new Date().toISOString(),
      };
      setApplications((prev) => [newApp, ...prev]);
      setHistories((prev) => ({ ...prev, [newId]: [initialEvt] }));

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "APPLICATION_OPENED",
        target_type: "APPLICATION",
        target_id: newId,
        target_name: cand?.display_name || "Candidate",
        details: `Opened application for ${cand?.display_name || "Candidate"} on ${job?.title || "Vacancy"}.`,
      });

      setNotice(`Application opened at New for ${cand?.display_name || "candidate"}.`);
    },
    [candidates, jobs, logActivity],
  );

  const moveStage = useCallback(
    async (application: Application, target: string, reason: string) => {
      const cand = candidates.find((c) => c.id === application.candidate_id);
      const job = jobs.find((j) => j.id === application.job_id);
      const newEvt: StageEvent = {
        id: `evt-${Date.now().toString(36)}`,
        from_stage: application.stage,
        to_stage: target,
        reason: reason.trim() || `Moved to ${pretty(target)} by human decision.`,
        actor_role: "RECRUITER",
        created_at: new Date().toISOString(),
      };

      setApplications((prev) =>
        prev.map((a) =>
          a.id === application.id
            ? { ...a, stage: target, version: a.version + 1, updated_at: new Date().toISOString() }
            : a,
        ),
      );

      setHistories((prev) => ({
        ...prev,
        [application.id]: [...(prev[application.id] || []), newEvt],
      }));

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "STAGE_TRANSITION",
        target_type: "APPLICATION",
        target_id: application.id,
        target_name: cand?.display_name || "Candidate",
        details: `Stage updated from ${pretty(application.stage)} to ${pretty(target)}. Reason: ${reason.trim() || "Approved by human reviewer."}`,
      });

      if (target === "HIRED") {
        setHiredEmployees((prev) => {
          if (prev.some((e) => e.candidate_id === application.candidate_id)) return prev;
          const nextEmpNum = 42 + prev.length + 1;
          const empCode = `EMP-2026-${String(nextEmpNum).padStart(3, "0")}`;
          const rawName = (cand?.display_name || "New Hire").toLowerCase().replace(/[^a-z0-9]/g, ".");
          const nameParts = rawName.split(".").filter(Boolean);
          const emailUser = nameParts.length >= 2 ? `${nameParts[0]}.${nameParts[1]}` : nameParts[0] || "emp";
          const newHired: HiredEmployee = {
            id: `hired-${application.id}`,
            application_id: application.id,
            candidate_id: application.candidate_id,
            job_id: application.job_id,
            employee_code: empCode,
            display_name: cand?.display_name || "New Employee",
            role_title: job?.title || "Specialist Engineer",
            department: job?.department || "Platform Operations",
            hiring_manager: job?.hiring_manager || "Rachel Adams",
            corporate_email: `${emailUser}@talvora.tech`,
            personal_email: cand?.email || "candidate@external.net",
            phone: cand?.phone || "+1 (555) 019-8234",
            start_date: "Monday, April 6, 2026",
            compensation_package: application.target_compensation || "$240,000 Base + Equity",
            location: cand?.location || "Remote",
            onboarding_status: "Provisioning",
            offer_accepted_at: new Date().toISOString(),
            hiring_notes: reason.trim() || "Offer formally accepted. Onboarding workflow initiated with corporate engineering team.",
            checklist: [
              {
                id: `chk-${Date.now()}-1`,
                label: "Signed Offer Letter & Intellectual Property Assignment Agreement",
                category: "Compliance & Legal",
                completed: true,
                due_date: "Completed",
              },
              {
                id: `chk-${Date.now()}-2`,
                label: "Form I-9 Identity & US Employment Authorization Verified",
                category: "Compliance & Legal",
                completed: true,
                due_date: "Completed",
              },
              {
                id: `chk-${Date.now()}-3`,
                label: "Comprehensive Criminal & Employment Background Verification",
                category: "Compliance & Legal",
                completed: true,
                due_date: "Completed",
              },
              {
                id: `chk-${Date.now()}-4`,
                label: "Okta Corporate SSO Profile & 1Password Enterprise Vault Provisioned",
                category: "IT & Security",
                completed: false,
                due_date: "Day 1 Morning",
              },
              {
                id: `chk-${Date.now()}-5`,
                label: "Apple MacBook Pro M3 Max (64GB RAM) Laptop Dispatch Ordered",
                category: "IT & Security",
                completed: false,
                due_date: "Pending In-Transit",
              },
              {
                id: `chk-${Date.now()}-6`,
                label: "GitHub Enterprise Org Seat & AWS Staging Bastion SSH Keys Enrolled",
                category: "IT & Security",
                completed: false,
                due_date: "Day 1 Morning",
              },
              {
                id: `chk-${Date.now()}-7`,
                label: "Talvora Welcome Swag Kit, Corporate Amex & Desk Setup Stipend Dispatched",
                category: "People & Team",
                completed: true,
                due_date: "Completed",
              },
              {
                id: `chk-${Date.now()}-8`,
                label: "Assigned Peer Onboarding Buddy & Day 1 Team Lunch Scheduled",
                category: "People & Team",
                completed: false,
                due_date: "April 6 12:30 PM",
              },
            ],
          };
          return [newHired, ...prev];
        });
        setNotice(`Hire confirmed! ${cand?.display_name || "Candidate"} moved to Hired. Onboarding record created in 07 Hired.`);
      } else {
        setNotice(`Stage written by you. ${cand?.display_name || "Candidate"} moved to ${pretty(target)}.`);
      }
    },
    [candidates, jobs, logActivity],
  );

  const addScorecard = useCallback(
    async (applicationId: string, scorecard: Scorecard) => {
      setApplications((prev) =>
        prev.map((a) =>
          a.id === applicationId
            ? { ...a, scorecards: [scorecard, ...a.scorecards], version: a.version + 1, updated_at: new Date().toISOString() }
            : a,
        ),
      );
      const app = applications.find((a) => a.id === applicationId);
      const cand = candidates.find((c) => c.id === app?.candidate_id);
      logActivity({
        actor: scorecard.interviewer,
        action: "SCORECARD_SUBMITTED",
        target_type: "APPLICATION",
        target_id: applicationId,
        target_name: cand?.display_name || applicationId,
        details: `Submitted evaluation verdict: ${scorecard.verdict}. Notes: "${scorecard.notes}"`,
      });
      setNotice(`Scorecard submitted by ${scorecard.interviewer} (${scorecard.verdict}).`);
    },
    [applications, candidates, logActivity],
  );

  // Interview Packs
  const generateInterviewPack = useCallback(
    async (applicationId: string): Promise<InterviewPack> => {
      if (interviewPacks[applicationId]) {
        return interviewPacks[applicationId];
      }
      const app = applications.find((a) => a.id === applicationId);
      const cand = candidates.find((c) => c.id === app?.candidate_id);
      const job = jobs.find((j) => j.id === app?.job_id);

      if (!cand || !job || !app) {
        throw new Error("Cannot generate interview pack for missing application context.");
      }

      const newPack = generateDynamicInterviewPack(cand, job, app);
      setInterviewPacks((prev) => ({ ...prev, [applicationId]: newPack }));

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "PACK_GENERATED",
        target_type: "APPLICATION",
        target_id: applicationId,
        target_name: cand.display_name || "Candidate",
        details: `Generated tailored AI Interview Pack for ${cand.display_name} targeting ${job.title}. [Demo Data]`,
      });

      setNotice(`Interview Pack generated for ${cand.display_name}. Questions & evaluation rubric ready. [Demo Data]`);
      return newPack;
    },
    [applications, candidates, interviewPacks, jobs, logActivity],
  );

  // Interview Scheduling
  const scheduleInterview = useCallback(
    async (input: {
      candidate_name: string;
      candidate_role: string;
      interviewer: string;
      round_title: string;
      time: string;
      date?: string;
      candidate_id?: string;
      application_id?: string;
      notes?: string;
    }) => {
      const newId = `sch-${Date.now().toString(36)}`;
      const newSchedule: InterviewScheduleItem = {
        id: newId,
        candidate_id: input.candidate_id,
        application_id: input.application_id,
        time: input.time,
        date: input.date || "Today",
        candidate_name: input.candidate_name,
        candidate_role: input.candidate_role,
        interviewer: input.interviewer,
        round_title: input.round_title,
        status: "Scheduled",
        tone: "human",
        notes: input.notes,
      };

      setSchedule((prev) => [newSchedule, ...prev]);

      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "INTERVIEW_SCHEDULED",
        target_type: "INTERVIEW",
        target_id: newId,
        target_name: input.candidate_name,
        details: `Scheduled ${input.round_title} with ${input.interviewer} for ${input.time} (${input.date || "Today"}).`,
      });

      setNotice(`Interview scheduled for ${input.candidate_name} with ${input.interviewer} at ${input.time}.`);
    },
    [logActivity],
  );

  const rescheduleInterview = useCallback(
    async (interviewId: string, newTime: string, newDate?: string) => {
      setSchedule((prev) =>
        prev.map((item) =>
          item.id === interviewId
            ? { ...item, time: newTime, date: newDate || item.date || "Today", status: "Scheduled", tone: "human" }
            : item,
        ),
      );
      const item = schedule.find((s) => s.id === interviewId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "INTERVIEW_RESCHEDULED",
        target_type: "INTERVIEW",
        target_id: interviewId,
        target_name: item?.candidate_name || interviewId,
        details: `Rescheduled to ${newTime} (${newDate || item?.date || "Today"}).`,
      });
      setNotice(`Interview rescheduled for ${item?.candidate_name} at ${newTime}.`);
    },
    [logActivity, schedule],
  );

  const cancelInterview = useCallback(
    async (interviewId: string, reason: string) => {
      setSchedule((prev) =>
        prev.map((item) =>
          item.id === interviewId
            ? { ...item, status: "Cancelled", tone: "closed", notes: reason }
            : item,
        ),
      );
      const item = schedule.find((s) => s.id === interviewId);
      logActivity({
        actor: "lead.recruiter@talvora.test",
        action: "INTERVIEW_CANCELLED",
        target_type: "INTERVIEW",
        target_id: interviewId,
        target_name: item?.candidate_name || interviewId,
        details: `Interview cancelled: ${reason || "Cancelled by recruiter."}`,
      });
      setNotice(`Interview loop cancelled.`);
    },
    [logActivity, schedule],
  );

  // Resume Intake, Quarantine & Screening Simulation
  const uploadResume = useCallback(
    async (jobId: string, file: File) => {
      const docId = `doc-${Date.now().toString(36)}`;
      const cleanCandidateName = file.name
        .replace(/\.[^/.]+$/, "")
        .replace(/[-_]/g, " ")
        .replace(/\b\w/g, (l) => l.toUpperCase());

      const candId = `cand-${Date.now().toString(36)}`;
      const appId = `app-${Date.now().toString(36)}`;

      const newDoc: HeldDocument = {
        id: docId,
        filename: file.name,
        candidate_name: cleanCandidateName,
        filesize: `${Math.round(file.size / 1024) || 450} KB`,
        version: 1,
        status: "CLEAN",
        problem_code: null,
      };

      const newCand: Candidate = {
        id: candId,
        display_name: cleanCandidateName,
        email: `${cleanCandidateName.toLowerCase().replace(/\s+/g, ".")}@talent-intake.net`,
        phone: "+1 (555) 018-9942",
        current_company: "Specialist Technology Contributor",
        current_title: "Senior Infrastructure & Systems Engineer",
        experience_years: 7,
        location: "Remote",
        education: "B.S. Computer Engineering",
        rating: "★ 4.8 · High Fit (Intake Verified)",
        source: "UPLOAD_INTAKE",
        is_draft: false,
        current_contact_revision_number: 1,
        is_contact_verified: true,
        version: 1,
        created_at: new Date().toISOString(),
      };

      const newApp: Application = {
        id: appId,
        job_id: jobId,
        candidate_id: candId,
        stage: "IN_REVIEW",
        source: "UPLOAD_INTAKE",
        match_score: "96% Match (All Criteria Grounded)",
        target_compensation: "$250,000 Base + Equity",
        notes: `Simulated resume extraction and automated criteria evaluation completed on ${file.name}.`,
        scorecards: [],
        version: 1,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      const initialEvt: StageEvent = {
        id: `evt-${appId}-1`,
        from_stage: null,
        to_stage: "IN_REVIEW",
        reason: "Admitted via intake quarantine. ClamAV virus scan: CLEAN. Auto-screened to In Review.",
        actor_role: "SYSTEM_SCANNER",
        created_at: new Date().toISOString(),
      };

      const extractedSource: SourceView = {
        document_id: docId,
        version_number: 1,
        extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
        segments: [
          {
            id: `seg-${docId}-1`,
            page_number: 1,
            extraction_method: "DIGITAL_TEXT",
            text: `${cleanCandidateName.toUpperCase()} — Professional Dossier & Resume\nVerified Digest: SHA-256 clean receipt.\nClamAV Virus Verdict: CLEAN (Zero malicious signatures detected).\n\nEXECUTIVE PROFILE\nDemonstrated 7+ years building and sustaining high-availability backend services, distributed data flows, and zero-trust cloud infrastructure. Comprehensive mastery of fault-tolerant systems, observability, and container orchestration.`,
          },
          {
            id: `seg-${docId}-2`,
            page_number: 2,
            extraction_method: "DIGITAL_TEXT",
            text: `QUALIFICATIONS GROUNDING & CRITERIA CITATIONS\n• Requirement: 60+ months high-concurrency systems experience -> VERIFIED (84 months continuous professional track record).\n• Requirement: Python & AsyncIO architecture -> VERIFIED (Engineered asynchronous outbox pipeline sustaining 45,000 TPS).\n• Requirement: Distributed consensus & leasing -> VERIFIED (Configured Raft state machine replication and monotonic fencing).`,
          },
        ],
      };

      setDocuments((prev) => [newDoc, ...prev]);
      setCandidates((prev) => [newCand, ...prev]);
      setApplications((prev) => [newApp, ...prev]);
      setHistories((prev) => ({ ...prev, [appId]: [initialEvt] }));
      setSources((prev) => ({ ...prev, [docId]: extractedSource }));

      const targetJob = jobs.find((j) => j.id === jobId);
      if (targetJob) {
        const pack = generateDynamicInterviewPack(newCand, targetJob, newApp);
        setInterviewPacks((prev) => ({ ...prev, [appId]: pack }));
      }

      logActivity({
        actor: "SYSTEM_SCANNER",
        action: "DOCUMENT_INTAKE_SCREENED",
        target_type: "DOCUMENT",
        target_id: docId,
        target_name: file.name,
        details: `Quarantine cleared. Created candidate ${cleanCandidateName} and opened application in In Review (96% Criteria Match). [Demo Data]`,
      });

      setNotice(
        `${file.name} is clean. ${cleanCandidateName} admitted to In Review with 96% criteria fit. Evidence & interview pack generated. [Demo Data]`,
      );
    },
    [jobs, logActivity],
  );

  const toggleChecklistItem = useCallback((employeeId: string, itemId: string) => {
    setHiredEmployees((prev) =>
      prev.map((emp) => {
        if (emp.id !== employeeId) return emp;
        const updatedChecklist = emp.checklist.map((item) =>
          item.id === itemId ? { ...item, completed: !item.completed } : item,
        );
        const allDone = updatedChecklist.every((item) => item.completed);
        const someDone = updatedChecklist.some((item) => item.completed);
        const newStatus: "Provisioning" | "Ready for Day 1" | "Onboarded" = allDone
          ? "Onboarded"
          : someDone
          ? "Ready for Day 1"
          : "Provisioning";
        return {
          ...emp,
          checklist: updatedChecklist,
          onboarding_status: newStatus,
        };
      }),
    );
  }, []);

  const loadVersions = useCallback(
    async (jobId: string): Promise<RequirementVersion[]> => {
      return requirementVersions[jobId] || [
        {
          id: `ver-${jobId}-default`,
          version_number: 1,
          title: "Approved Qualifications Framework v1",
          description: "Role competencies and minimum qualification requirements.",
          published_at: new Date().toISOString(),
          change_summary: "Published baseline standards.",
          criteria: [
            {
              id: `crit-${jobId}-1`,
              criterion_type: "MIN_EXPERIENCE_MONTHS",
              is_required: true,
              source_requirement_text: "Minimum 5 years demonstrated professional domain experience.",
              relevance_rationale: "Ensures autonomous senior execution.",
              configuration: { skill_name: "Domain Expertise" },
            },
          ],
        },
      ];
    },
    [requirementVersions],
  );

  const loadHistory = useCallback(
    async (applicationId: string): Promise<StageEvent[]> => {
      return (
        histories[applicationId] || [
          {
            id: `evt-${applicationId}-init`,
            from_stage: null,
            to_stage: "NEW",
            reason: "Candidate application admitted via intake.",
            actor_role: "RECRUITER",
            created_at: new Date(Date.now() - 48 * 3600000).toISOString(),
          },
        ]
      );
    },
    [histories],
  );

  const loadEligibility = useCallback(
    async (applicationId: string): Promise<{ eligible: boolean; reasons: string[] }> => {
      const app = applications.find((a) => a.id === applicationId);
      if (app?.stage === "REJECTED") {
        return { eligible: false, reasons: ["Does not satisfy minimum 60 months concurrency criteria."] };
      }
      return { eligible: true, reasons: [] };
    },
    [applications],
  );

  const loadSource = useCallback(
    async (documentId: string, _version: number): Promise<SourceView> => {
      return (
        sources[documentId] || {
          document_id: documentId,
          version_number: 1,
          extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
          segments: [
            {
              id: `seg-${documentId}-default`,
              page_number: 1,
              extraction_method: "DIGITAL_TEXT",
              text: "Document extracted through clean quarantine pipeline. All source text verified.",
            },
          ],
        }
      );
    },
    [sources],
  );

  const value = useMemo<Workspace>(
    () => ({
      status,
      me,
      jobs,
      candidates,
      applications,
      documents,
      schedule,
      hiredEmployees,
      interviewers,
      interviewPacks,
      auditLogs,
      notice,
      error,
      can,
      dismissNotice,
      signIn,
      signOut,
      refresh,
      createJob,
      updateJob,
      publishJob,
      closeJob,
      reopenJob,
      createCandidate,
      updateCandidate,
      attest,
      createApplication,
      moveStage,
      addScorecard,
      generateInterviewPack,
      scheduleInterview,
      rescheduleInterview,
      cancelInterview,
      toggleChecklistItem,
      uploadResume,
      logActivity,
      resetDemoData,
      loadVersions,
      loadHistory,
      loadEligibility,
      loadSource,
    }),
    [
      status,
      me,
      jobs,
      candidates,
      applications,
      documents,
      schedule,
      hiredEmployees,
      interviewers,
      interviewPacks,
      auditLogs,
      notice,
      error,
      can,
      dismissNotice,
      signIn,
      signOut,
      refresh,
      createJob,
      updateJob,
      publishJob,
      closeJob,
      reopenJob,
      createCandidate,
      updateCandidate,
      attest,
      createApplication,
      moveStage,
      addScorecard,
      generateInterviewPack,
      scheduleInterview,
      rescheduleInterview,
      cancelInterview,
      toggleChecklistItem,
      uploadResume,
      logActivity,
      resetDemoData,
      loadVersions,
      loadHistory,
      loadEligibility,
      loadSource,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace is missing");
  return value;
}
