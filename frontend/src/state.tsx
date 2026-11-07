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
  status: string;
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
  time: string;
  candidate_name: string;
  candidate_role: string;
  interviewer: string;
  round_title: string;
  status: "In Progress" | "Scheduled" | "Completed";
  tone: "interviewing" | "human" | "clean";
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
    "MANAGE_DOCUMENT_INTAKE",
    "READ_DOCUMENTS",
    "READ_SCREENING_EVIDENCE",
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
    candidate_name: "Priya Sharma, Ph.D.",
    candidate_role: "Senior AI Research Scientist",
    interviewer: "Core AI Research Technical Panel (Dr. Chen & Dr. Marcus)",
    round_title: "Round 2: Deterministic LLM Grounding & Retrieval Benchmarks",
    status: "Scheduled",
    tone: "human",
  },
  {
    id: "sch-4",
    time: "04:30 PM",
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
    hiring_notes: "Unanimous Strong Hire verdict across frontend architecture, accessibility, and state synchronization debriefs.",
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
    hiring_notes: "Exemplary track record managing multi-region Kubernetes clusters with zero unscheduled downtime at Uber Core.",
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
        {
          id: "crit-sre-3",
          criterion_type: "SKILL_PRESENT",
          is_required: false,
          source_requirement_text: "Demonstrated incident command leadership for high-impact tier-0 production outages.",
          relevance_rationale: "Ensures disciplined crisis handling and clear post-mortem remediations.",
          configuration: { skill_name: "Incident Command" },
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
        {
          id: "crit-dist-2-4",
          criterion_type: "SKILL_PRESENT",
          is_required: false,
          source_requirement_text: "Experience with AWS SQS, event choreography, and Apache Kafka or RabbitMQ.",
          relevance_rationale: "Enables rapid ramp-up on our transactional outbox infrastructure.",
          configuration: { skill_name: "Event Streaming & Queues" },
        },
      ],
    },
    {
      id: "ver-dist-1",
      version_number: 1,
      title: "Senior Distributed Systems Baseline Criteria v1",
      description: "Core engineering criteria governing distributed transaction and streaming reliability.",
      published_at: new Date(Date.now() - 20 * 86400000).toISOString(),
      change_summary: "Initial publication of distributed systems requirements framework.",
      criteria: [
        {
          id: "crit-dist-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 5 years (60 months) building high-concurrency backend services.",
          relevance_rationale: "Essential for designing distributed consensus and resolving complex concurrency races.",
          configuration: { skill_name: "High-Concurrency Backend" },
        },
      ],
    },
  ],
  "job-ai": [
    {
      id: "ver-ai-1",
      version_number: 1,
      title: "Staff AI Architect Evaluation Framework v1",
      description: "Defines technical criteria for evaluation, RAG pipelines, and model safety.",
      published_at: new Date(Date.now() - 15 * 86400000).toISOString(),
      change_summary: "Published requirement set for AI Architect vacancy.",
      criteria: [
        {
          id: "crit-ai-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 6 years (72 months) in applied machine learning, NLP, or production LLM systems.",
          relevance_rationale: "Required for architectural leadership in complex multi-modal extraction pipelines.",
          configuration: { skill_name: "Machine Learning & RAG" },
        },
        {
          id: "crit-ai-2",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Production mastery of PyTorch, vector databases, and deterministic retrieval pipelines.",
          relevance_rationale: "Powers the citation and evidence grounding mechanisms.",
          configuration: { skill_name: "Deterministic Grounding" },
        },
      ],
    },
  ],
  "job-fe": [
    {
      id: "ver-fe-1",
      version_number: 1,
      title: "Full Stack UI/API Qualifications v1",
      description: "Frontend performance and design system standards.",
      published_at: new Date(Date.now() - 20 * 86400000).toISOString(),
      change_summary: "Approved candidate requirements for Frontend/Fullstack.",
      criteria: [
        {
          id: "crit-fe-1",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Advanced TypeScript, React component lifecycle, and reactive state management.",
          relevance_rationale: "Core stack for the recruiter desk application.",
          configuration: { skill_name: "React & TypeScript" },
        },
        {
          id: "crit-fe-2",
          criterion_type: "SKILL_PRESENT",
          is_required: false,
          source_requirement_text: "Demonstrated compliance with WCAG 2.1 AA accessibility standards.",
          relevance_rationale: "Ensures universal desk usability across keyboard navigation and assistive devices.",
          configuration: { skill_name: "Accessibility (a11y)" },
        },
      ],
    },
  ],
  "job-ta": [
    {
      id: "ver-ta-1",
      version_number: 1,
      title: "Executive Search & People Operations Standards v1",
      description: "Recruiting telemetry, structured debriefing, and leadership compensation frameworks.",
      published_at: new Date(Date.now() - 8 * 86400000).toISOString(),
      change_summary: "Published criteria governing senior talent acquisition operations.",
      criteria: [
        {
          id: "crit-ta-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 6 years (72 months) driving technical or executive talent acquisition.",
          relevance_rationale: "Crucial for engaging staff+ engineering leadership candidates.",
          configuration: { skill_name: "Executive Sourcing" },
        },
        {
          id: "crit-ta-2",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Expertise in ATS compliance, candidate pipeline telemetry, and executive offer negotiation.",
          relevance_rationale: "Ensures ethical hiring velocity and high offer acceptance rates.",
          configuration: { skill_name: "Talent Operations & Negotiation" },
        },
      ],
    },
  ],
  "job-rust": [
    {
      id: "ver-rust-1",
      version_number: 1,
      title: "Systems Performance & Kernel Criteria v1",
      description: "Low-level Linux kernel primitives, memory-mapped I/O, and asynchronous event loops.",
      published_at: new Date(Date.now() - 7 * 86400000).toISOString(),
      change_summary: "Approved baseline for Core Systems Engineering.",
      criteria: [
        {
          id: "crit-rust-1",
          criterion_type: "MIN_EXPERIENCE_MONTHS",
          is_required: true,
          source_requirement_text: "Minimum 6 years (72 months) in systems-level programming in Rust, C, or modern C++.",
          relevance_rationale: "Required for mission-critical zero-copy disk logging.",
          configuration: { skill_name: "Rust & Linux Systems" },
        },
        {
          id: "crit-rust-2",
          criterion_type: "SKILL_PRESENT",
          is_required: true,
          source_requirement_text: "Hands-on experience with io_uring, eBPF telemetry, and lock-free data structures.",
          relevance_rationale: "Directly affects p99 transaction commit latency.",
          configuration: { skill_name: "Kernel Primitives & eBPF" },
        },
      ],
    },
  ],
  "job-draft": [],
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
    id: "cand-aris",
    display_name: "Dr. Aris Thorne",
    email: "aris.thorne@kernelresearch.ch",
    phone: "+41 (22) 767-1190",
    current_company: "HashiCorp Systems",
    current_title: "Staff Systems & Storage Engineer",
    experience_years: 12,
    location: "Zurich, Switzerland",
    education: "Ph.D. Operating Systems, ETH Zurich",
    rating: "★ 4.8 · Strong Hire",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
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
  {
    id: "cand-sarah",
    display_name: "Sarah Jenkins",
    email: "sarah.jenkins@execsearch.global",
    phone: "+1 (917) 440-2391",
    current_company: "Heidrick & Struggles",
    current_title: "Senior Executive Search Consultant",
    experience_years: 8,
    location: "New York, NY",
    education: "B.A. Organizational Behavior, NYU",
    rating: "★ 4.7 · Screen Pending",
    source: "MANUAL_ENTRY",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: false,
    version: 1,
    created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "cand-sophia",
    display_name: "Sophia Patel",
    email: "sophia.patel@algorithmicops.ai",
    phone: "+1 (408) 789-6631",
    current_company: "Anthropic",
    current_title: "Member of Technical Staff (Evaluation)",
    experience_years: 6,
    location: "San Francisco, CA",
    education: "M.S. Artificial Intelligence, Carnegie Mellon",
    rating: "★ 4.7 · Advance to Panel",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 7 * 86400000).toISOString(),
  },
  {
    id: "cand-maya",
    display_name: "Maya Lin",
    email: "maya.lin@frontendcraft.co",
    phone: "+1 (312) 670-8819",
    current_company: "Figma",
    current_title: "Senior Design Systems Engineer",
    experience_years: 6,
    location: "Chicago, IL",
    education: "B.S. Human-Computer Interaction, Northwestern",
    rating: "★ 4.6 · In Review",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
  },
  {
    id: "cand-tariq",
    display_name: "Tariq Al-Mansoor",
    email: "tariq.mansoor@inferencenet.io",
    phone: "+1 (415) 309-8812",
    current_company: "NVIDIA",
    current_title: "Inference Serving Systems Lead",
    experience_years: 7,
    location: "Santa Clara, CA",
    education: "M.S. Electrical & Computer Engineering, Georgia Tech",
    rating: "★ 4.8 · Advance to Panel",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 6 * 86400000).toISOString(),
  },
  {
    id: "cand-kenji",
    display_name: "Kenji Sato",
    email: "kenji.sato@tokyo-cluster.net",
    phone: "+81 (3) 5550-9811",
    current_company: "Netflix",
    current_title: "Senior SRE (Asia-Pacific Region)",
    experience_years: 8,
    location: "Tokyo / Remote",
    education: "B.S. Information Systems, Tokyo University",
    rating: "★ 4.7 · Ready for Loop",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 15 * 86400000).toISOString(),
  },
  {
    id: "cand-amara",
    display_name: "Amara Okafor",
    email: "amara.okafor@cloudscale-africa.io",
    phone: "+44 (20) 7946-0922",
    current_company: "Uber Platform Core",
    current_title: "Staff SRE & Kubernetes Specialist",
    experience_years: 9,
    location: "London, UK",
    education: "M.S. Distributed Systems, Imperial College London",
    rating: "★ 4.8 · Interviewing",
    source: "UPLOAD_INTAKE",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 8 * 86400000).toISOString(),
  },
  {
    id: "cand-alex",
    display_name: "Alex Rivera",
    email: "alex.rivera@systems-infra.io",
    phone: "+1 (512) 990-4123",
    current_company: "Fastly",
    current_title: "Network Systems Engineer",
    experience_years: 3,
    location: "Austin, TX",
    education: "B.S. Computer Engineering, UT Austin",
    rating: "★ 3.4 · Rejected (Experience Minimum)",
    source: "MANUAL_ENTRY",
    is_draft: false,
    current_contact_revision_number: 1,
    is_contact_verified: true,
    version: 1,
    created_at: new Date(Date.now() - 12 * 86400000).toISOString(),
  },
];

const INITIAL_APPLICATIONS: Application[] = [
  {
    id: "app-elena",
    job_id: "job-dist",
    candidate_id: "cand-elena",
    stage: "INTERVIEWING",
    source: "UPLOAD_INTAKE",
    match_score: "98% Match (4 of 4 Criteria Met)",
    target_compensation: "$265,000 Base + 0.15% Equity",
    notes: "Cleared distributed systems architectural screening with perfect score. System design loop in progress.",
    scorecards: [
      {
        interviewer: "Dr. Jennifer Vance",
        role: "VP Platform Engineering",
        verdict: "Strong Hire",
        notes: "Candidate clearly articulated split-brain resolution in Raft consensus. Deep understanding of monotonic lease fencing.",
      },
      {
        interviewer: "Alex Miller",
        role: "Staff SRE Architect",
        verdict: "Strong Hire",
        notes: "Authored clean Python asyncio code for outbox publisher in live exercise in under 20 mins.",
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
    match_score: "100% Match (4 of 4 Criteria Met)",
    target_compensation: "$280,000 Base + 0.20% Equity",
    notes: "Completed 4-stage interview loop. Exceptional Raft consensus algorithm proficiency. HM recommended offer.",
    scorecards: [
      {
        interviewer: "Dr. Jennifer Vance",
        role: "VP Platform Engineering",
        verdict: "Strong Hire",
        notes: "Core open-source contributor to Raft consensus. Exceptional grasp of distributed log storage and compaction.",
      },
      {
        interviewer: "Rachel Adams",
        role: "Head of Product Eng",
        verdict: "Strong Hire",
        notes: "Empathetic technical leader with extensive mentorship experience across large distributed teams.",
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
    match_score: "96% Match (3 of 3 Criteria Met)",
    target_compensation: "$320,000 Base + 0.25% Equity",
    notes: "Cleared Principal SRE debrief with 4 Strong Hire verdicts. Compensation proposal submitted to board.",
    scorecards: [
      {
        interviewer: "Marcus Cole",
        role: "CISO",
        verdict: "Strong Hire",
        notes: "Led zero-trust architecture across 12 sovereign AWS regions at Palantir. Unrivaled security depth.",
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
    match_score: "99% Match (All Criteria Grounded)",
    target_compensation: "$340,000 Base + 0.25% Equity",
    notes: "Demonstrated 8 years of applied NLP & high-throughput inference serving. HM scheduled loop.",
    scorecards: [
      {
        interviewer: "Dr. Tariq Chen",
        role: "Director of AI Research",
        verdict: "Strong Hire",
        notes: "Pioneered cited evidence extraction with exact Unicode offset grounding. Top 1% candidate.",
      },
    ],
    version: 2,
    created_at: new Date(Date.now() - 6 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "app-aris",
    job_id: "job-rust",
    candidate_id: "cand-aris",
    stage: "INTERVIEWING",
    source: "UPLOAD_INTAKE",
    match_score: "97% Match (2 of 2 Criteria Met)",
    target_compensation: "$290,000 Base + 0.18% Equity",
    notes: "Demonstrated deep mastery of io_uring zero-copy networking and lock-free ring buffers.",
    scorecards: [
      {
        interviewer: "Dr. Klaus Richter",
        role: "Chief Architect",
        verdict: "Strong Hire",
        notes: "World-class kernel systems engineer. Live benchmark reduced packet jitter by 96%.",
      },
    ],
    version: 3,
    created_at: new Date(Date.now() - 8 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "app-amara",
    job_id: "job-sre",
    candidate_id: "cand-amara",
    stage: "INTERVIEWING",
    source: "UPLOAD_INTAKE",
    match_score: "95% Match (3 of 3 Criteria Met)",
    target_compensation: "$300,000 Base + 0.20% Equity",
    notes: "Deep multi-tenant Kubernetes experience. Scheduled for live incident response simulation.",
    scorecards: [
      {
        interviewer: "Marcus Cole",
        role: "CISO",
        verdict: "Strong Hire",
        notes: "Solid defense-in-depth thinking. Experienced with eBPF network observability in production.",
      },
    ],
    version: 3,
    created_at: new Date(Date.now() - 8 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
  {
    id: "app-tariq",
    job_id: "job-ai",
    candidate_id: "cand-tariq",
    stage: "INTERVIEW_APPROVED",
    source: "UPLOAD_INTAKE",
    match_score: "94% Match (Criteria Met)",
    target_compensation: "$310,000 Base + 0.20% Equity",
    notes: "High-throughput inference specialist. Approved by VP of AI Research for technical round.",
    scorecards: [],
    version: 2,
    created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
  {
    id: "app-kenji",
    job_id: "job-sre",
    candidate_id: "cand-kenji",
    stage: "INTERVIEW_APPROVED",
    source: "UPLOAD_INTAKE",
    match_score: "92% Match (Criteria Met)",
    target_compensation: "$280,000 Base + 0.15% Equity",
    notes: "Extensive Tokyo region AWS failover architecture experience. Ready for panel.",
    scorecards: [],
    version: 2,
    created_at: new Date(Date.now() - 7 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  },
  {
    id: "app-sophia",
    job_id: "job-ai",
    candidate_id: "cand-sophia",
    stage: "IN_REVIEW",
    source: "UPLOAD_INTAKE",
    match_score: "93% Match (Criteria Met)",
    target_compensation: "$295,000 Base + 0.18% Equity",
    notes: "Recruiter review of multi-agent evaluation benchmark research and citations.",
    scorecards: [],
    version: 1,
    created_at: new Date(Date.now() - 4 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
  },
  {
    id: "app-maya",
    job_id: "job-fe",
    candidate_id: "cand-maya",
    stage: "IN_REVIEW",
    source: "UPLOAD_INTAKE",
    match_score: "91% Match (Criteria Met)",
    target_compensation: "$210,000 Base + 0.10% Equity",
    notes: "Design system and UI accessibility review in progress with Lead Frontend Architect.",
    scorecards: [],
    version: 1,
    created_at: new Date(Date.now() - 3 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 1 * 86400000).toISOString(),
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
  {
    id: "app-sarah",
    job_id: "job-ta",
    candidate_id: "cand-sarah",
    stage: "NEW",
    source: "MANUAL_ENTRY",
    match_score: "90% Match (Criteria Met)",
    target_compensation: "$190,000 Base + Incentive",
    notes: "Inbound executive talent lead inquiry. Requires verification of corporate email.",
    scorecards: [],
    version: 1,
    created_at: new Date(Date.now() - 14 * 3600000).toISOString(),
    updated_at: new Date(Date.now() - 14 * 3600000).toISOString(),
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
        notes: "Exceptional design-engineering craftsmanship and WCAG 2.1 AA accessibility leadership.",
      },
    ],
    version: 2,
    created_at: new Date(Date.now() - 20 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 4 * 3600000).toISOString(),
  },
  {
    id: "app-alex",
    job_id: "job-dist",
    candidate_id: "cand-alex",
    stage: "REJECTED",
    source: "MANUAL_ENTRY",
    match_score: "Failed 60mo Experience Criteria",
    target_compensation: "$180,000 Base",
    notes: "Does not meet mandatory 60 months distributed systems requirement.",
    scorecards: [],
    version: 2,
    created_at: new Date(Date.now() - 11 * 86400000).toISOString(),
    updated_at: new Date(Date.now() - 9 * 86400000).toISOString(),
  },
];

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
  "app-chloe": [
    {
      id: "evt-chloe-1",
      from_stage: null,
      to_stage: "NEW",
      reason: "Intake parse verified. 10 years cloud defense leadership.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 10 * 86400000).toISOString(),
    },
    {
      id: "evt-chloe-2",
      from_stage: "NEW",
      to_stage: "INTERVIEWING",
      reason: "Approved directly to onsite panel by Chief Information Security Officer.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 6 * 86400000).toISOString(),
    },
    {
      id: "evt-chloe-3",
      from_stage: "INTERVIEWING",
      to_stage: "DECISION_PENDING",
      reason: "Cleared 5-round loop unanimously. Executive offer formulation underway.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 12 * 3600000).toISOString(),
    },
  ],
  "app-aris": [
    {
      id: "evt-aris-1",
      from_stage: null,
      to_stage: "NEW",
      reason: "Inbound application parsed from resume.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 8 * 86400000).toISOString(),
    },
    {
      id: "evt-aris-2",
      from_stage: "NEW",
      to_stage: "INTERVIEW_APPROVED",
      reason: "Verified author of open source zero-copy ring buffer crate.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 5 * 86400000).toISOString(),
    },
    {
      id: "evt-aris-3",
      from_stage: "INTERVIEW_APPROVED",
      to_stage: "INTERVIEWING",
      reason: "Kernel programming live session scheduled with Systems Director.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    },
  ],
  "app-priya": [
    {
      id: "evt-priya-1",
      from_stage: null,
      to_stage: "NEW",
      reason: "Inbound application parsed from resume.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 6 * 86400000).toISOString(),
    },
    {
      id: "evt-priya-2",
      from_stage: "NEW",
      to_stage: "INTERVIEW_APPROVED",
      reason: "Strong citation evidence on distributed model inference and Unicode offset grounding.",
      actor_role: "HIRING_MANAGER",
      created_at: new Date(Date.now() - 2 * 86400000).toISOString(),
    },
  ],
  "app-liam": [
    {
      id: "evt-liam-1",
      from_stage: "DECISION_PENDING",
      to_stage: "HIRED",
      reason: "Candidate signed offer letter. Background screening verified.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 4 * 3600000).toISOString(),
    },
  ],
  "app-alex": [
    {
      id: "evt-alex-1",
      from_stage: "IN_REVIEW",
      to_stage: "REJECTED",
      reason: "Missing required distributed transactions and consensus experience.",
      actor_role: "RECRUITER",
      created_at: new Date(Date.now() - 9 * 86400000).toISOString(),
    },
  ],
};

const INITIAL_DOCUMENTS: HeldDocument[] = [
  {
    id: "doc-elena",
    filename: "Elena_Rostova_Staff_SRE_CV.pdf",
    candidate_name: "Elena Rostova",
    filesize: "482 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
  {
    id: "doc-marcus",
    filename: "Marcus_Vance_Principal_Distributed_Systems.pdf",
    candidate_name: "Marcus Vance",
    filesize: "524 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
  {
    id: "doc-priya",
    filename: "Priya_Sharma_Staff_AI_Architect.pdf",
    candidate_name: "Priya Sharma, Ph.D.",
    filesize: "612 KB",
    version: 1,
    status: "CLEAN",
    problem_code: null,
  },
  {
    id: "doc-aris",
    filename: "Dr_Aris_Thorne_Kernel_Systems_Engineer.pdf",
    candidate_name: "Dr. Aris Thorne",
    filesize: "394 KB",
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
        text: "NOTABLE PRODUCTION ACCOMPLISHMENTS\nStaff Infrastructure Architect — Datadog / CloudScale Core (2021 – Present)\n• Designed and deployed multi-region active-active database failover across AWS us-east-1 and eu-west-1, eliminating split-brain anomalies during chaos simulation testing.\n• Authored high-throughput transactional outbox service in Python 3.12 and AsyncIO with monotonic lease fencing, sustaining 65,000 TPS under simulated network partitions.\n• Implemented automated credential rotation and secret-less container identity with HashiCorp Vault and SPIFFE/SPIRE.\n\nSenior Distributed Systems Engineer — DataStream Core (2017 – 2021)\n• Scaled Apache Kafka and RabbitMQ pipelines to ingest 1.2 billion events daily with sub-10ms p99 processing latency.\n• Mentored 14 backend and infrastructure engineers in concurrent memory management and distributed debugging.",
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
        text: "MARCUS VANCE — Principal Distributed Systems Engineer\nSeattle, WA · marcus.vance@distributedkernel.net · +1 (206) 555-0182\n\nEXECUTIVE BIO\n11+ years optimizing consensus protocols, transactional outboxes, and high-concurrency low-latency network primitives. Core open-source contributor to Paxos and Raft implementations. Principal engineer leading large-scale multi-region event streams at Amazon Web Services.\n\nTECHNICAL SPECIALIZATION\n• Systems Primitives: Python, C++, Go, AsyncIO, Linux kernel internals, TCP/IP socket tuning\n• Storage & Queues: PostgreSQL ACID internals, RocksDB, Apache Kafka, AWS SQS, DynamoDB Streams\n• Resilience: Chaos Engineering, Fault Injection, Network Partition Recovery, Monotonic Leases",
      },
      {
        id: "seg-marcus-2",
        page_number: 2,
        extraction_method: "DIGITAL_TEXT",
        text: "SELECTED CONTRIBUTIONS\nPrincipal Engineer — Distributed Kernel Labs / AWS Core (2019 – Present)\n• Architected fault-tolerant state-machine replication cluster with zero data loss under simulated chaos partitions across 3 AWS Availability Zones.\n• Optimized Python ASGI query execution engine reducing CPU cache misses by 28% under 50k RPS load.\n• Designed automated canary verification harnesses and crash recovery testing protocols.",
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
        text: "PRIYA SHARMA, Ph.D. — Senior AI Research Scientist & LLM Systems Architect\nPalo Alto, CA · priya.sharma@mlfoundations.org · +1 (650) 441-9872\n\nSPECIALIZATION\nApplied AI research architect focusing on bounded hallucination mitigation, deterministic grounding with exact Unicode offsets, and low-latency inference serving for LLM agents. Ph.D. from UC Berkeley AI Research Lab.\n\nRESEARCH & INDUSTRY EXPERIENCE\nLead AI Architect — Google DeepMind / ML Foundations Org (2020 – Present)\n• Invented deterministic cited-retrieval pipeline pairing PyTorch vector search with source span verification, reducing citation mismatch error to 0.01%.\n• Published 4 peer-reviewed papers at NeurIPS and ICML on trustworthy agentic reasoning and evaluation metrics.\n• Scaled model serving infrastructure on NVIDIA Triton and vLLM delivering 400 tokens/sec across 200 concurrent user sessions.",
      },
    ],
  },
  "doc-aris": {
    document_id: "doc-aris",
    version_number: 1,
    extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
    segments: [
      {
        id: "seg-aris-1",
        page_number: 1,
        extraction_method: "DIGITAL_TEXT",
        text: "DR. ARIS THORNE — Staff Systems & Kernel Performance Engineer\nZurich, Switzerland · aris.thorne@kernelresearch.ch · +41 (22) 767-1190\n\nOVERVIEW\nPh.D. in Computer Science (Distributed Operating Systems). Over 12 years developing low-overhead Linux kernel tracing, lock-free concurrency rings, and zero-copy io_uring socket drivers in Rust and C++ at HashiCorp Systems.\n\nKEY ACHIEVEMENTS\n• Reduced p99 packet parsing jitter from 4.2ms to 120 microseconds using custom eBPF filters.\n• Co-maintainer of open-source Rust asynchronous disk journal crate, adopted by top financial exchanges for sub-millisecond durable writes.",
      },
    ],
  },
};

type Workspace = {
  status: Status;
  me: Me | null;
  jobs: Job[];
  candidates: Candidate[];
  applications: Application[];
  documents: HeldDocument[];
  schedule: InterviewScheduleItem[];
  hiredEmployees: HiredEmployee[];
  notice: string | null;
  error: string | null;
  can: (capability: string) => boolean;
  dismissNotice: () => void;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  createJob: (input: {
    title: string;
    description: string;
    department: string;
    location: string;
    skill: string;
    publish: boolean;
  }) => Promise<void>;
  createCandidate: (input: { display_name: string; email: string }) => Promise<void>;
  attest: (candidate: Candidate, notes: string) => Promise<void>;
  createApplication: (jobId: string, candidateId: string) => Promise<void>;
  moveStage: (application: Application, target: string, reason: string) => Promise<void>;
  toggleChecklistItem: (employeeId: string, itemId: string) => void;
  uploadResume: (jobId: string, file: File) => Promise<void>;
  loadVersions: (jobId: string) => Promise<RequirementVersion[]>;
  loadHistory: (applicationId: string) => Promise<StageEvent[]>;
  loadEligibility: (applicationId: string) => Promise<{ eligible: boolean; reasons: string[] }>;
  loadSource: (documentId: string, version: number) => Promise<SourceView>;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("ready");
  const [me, setMe] = useState<Me | null>(HARDCODED_ME);
  const [jobs, setJobs] = useState<Job[]>(INITIAL_JOBS);
  const [candidates, setCandidates] = useState<Candidate[]>(INITIAL_CANDIDATES);
  const [applications, setApplications] = useState<Application[]>(INITIAL_APPLICATIONS);
  const [documents, setDocuments] = useState<HeldDocument[]>(INITIAL_DOCUMENTS);
  const [schedule] = useState<InterviewScheduleItem[]>(INITIAL_SCHEDULE);
  const [hiredEmployees, setHiredEmployees] = useState<HiredEmployee[]>(INITIAL_HIRED_EMPLOYEES);
  const [requirementVersions, setRequirementVersions] = useState<Record<string, RequirementVersion[]>>(INITIAL_REQUIREMENT_VERSIONS);
  const [histories, setHistories] = useState<Record<string, StageEvent[]>>(INITIAL_STAGE_HISTORIES);
  const [sources, setSources] = useState<Record<string, SourceView>>(INITIAL_SOURCES);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const can = useCallback((_capability: string) => true, []);

  const dismissNotice = useCallback(() => setNotice(null), []);

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
      setNotice(`${input.title} is ${input.publish ? "published and open" : "saved as draft"}.`);
    },
    [],
  );

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
      setNotice(`${input.display_name} is on the record.`);
    },
    [],
  );

  const attest = useCallback(
    async (candidate: Candidate, _notes: string) => {
      setCandidates((prev) =>
        prev.map((c) => (c.id === candidate.id ? { ...c, is_contact_verified: true, version: c.version + 1 } : c)),
      );
      setNotice(`Attestation recorded for ${candidate.display_name || candidate.email}. Verified address.`);
    },
    [],
  );

  const createApplication = useCallback(
    async (jobId: string, candidateId: string) => {
      const cand = candidates.find((c) => c.id === candidateId);
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
      setNotice(`Application opened at New for ${cand?.display_name || "candidate"}.`);
    },
    [candidates],
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
    [candidates, jobs],
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

  const uploadResume = useCallback(
    async (_jobId: string, file: File) => {
      const docId = `doc-custom-${Date.now().toString(36)}`;
      const newDoc: HeldDocument = {
        id: docId,
        filename: file.name,
        candidate_name: file.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " "),
        filesize: `${Math.round(file.size / 1024) || 380} KB`,
        version: 1,
        status: "CLEAN",
        problem_code: null,
      };
      setDocuments((prev) => [newDoc, ...prev]);
      setSources((prev) => ({
        ...prev,
        [docId]: {
          document_id: docId,
          version_number: 1,
          extraction: { status: "SUCCEEDED", problem_code: null, error_detail: null },
          segments: [
            {
              id: `seg-${docId}-1`,
              page_number: 1,
              extraction_method: "DIGITAL_TEXT",
              text: `INTAKE EXTRACTION RECORD — ${file.name.toUpperCase()}\n\nVerified Document Digest: SHA-256 clean receipt.\nClamAV Virus Verdict: CLEAN (Zero signatures detected).\n\nCANDIDATE BACKGROUND & RESUME SUMMARY:\nSpecialist credentials admitted through Talvora Quarantine pipeline.\nAll extracted sections parsed into source-addressable text spans ready for criteria evaluation.`,
            },
          ],
        },
      }));
      setNotice(`${file.name} is clean. Admitted to Evidence desk.`);
    },
    [],
  );

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
      return histories[applicationId] || [
        {
          id: `evt-${applicationId}-init`,
          from_stage: null,
          to_stage: "NEW",
          reason: "Candidate application admitted via intake.",
          actor_role: "RECRUITER",
          created_at: new Date(Date.now() - 48 * 3600000).toISOString(),
        },
      ];
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
      notice,
      error,
      can,
      dismissNotice,
      signIn,
      signOut,
      refresh,
      createJob,
      createCandidate,
      attest,
      createApplication,
      moveStage,
      toggleChecklistItem,
      uploadResume,
      loadVersions,
      loadHistory,
      loadEligibility,
      loadSource,
    }),
    [
      applications,
      can,
      candidates,
      createApplication,
      createCandidate,
      createJob,
      dismissNotice,
      documents,
      error,
      hiredEmployees,
      jobs,
      loadEligibility,
      loadHistory,
      loadSource,
      loadVersions,
      me,
      moveStage,
      notice,
      refresh,
      schedule,
      signIn,
      signOut,
      status,
      toggleChecklistItem,
      uploadResume,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace is missing");
  return value;
}
