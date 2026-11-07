import type {
  Application,
  AuditLog,
  Candidate,
  Interviewer,
  InterviewPack,
  Job,
} from "./state";

export const INITIAL_INTERVIEWERS: Interviewer[] = [
  {
    id: "int-1",
    name: "Dr. Jennifer Vance",
    role: "VP Platform Engineering",
    department: "Platform Infrastructure",
    email: "jennifer.vance@talvora.test",
    availability: "Available",
  },
  {
    id: "int-2",
    name: "Alex Miller",
    role: "Staff Infrastructure Architect",
    department: "Cloud Systems",
    email: "alex.miller@talvora.test",
    availability: "Available",
  },
  {
    id: "int-3",
    name: "Marcus Cole",
    role: "Chief Information Security Officer (CISO)",
    department: "Information Security",
    email: "marcus.cole@talvora.test",
    availability: "Preferred",
  },
  {
    id: "int-4",
    name: "Rachel Adams",
    role: "Head of Product Engineering",
    department: "Product Engineering",
    email: "rachel.adams@talvora.test",
    availability: "Available",
  },
  {
    id: "int-5",
    name: "Dr. Tariq Chen",
    role: "Director of AI Research",
    department: "Core AI Research",
    email: "tariq.chen@talvora.test",
    availability: "Available",
  },
  {
    id: "int-6",
    name: "Dr. Klaus Richter",
    role: "Chief Architect",
    department: "Core Systems",
    email: "klaus.richter@talvora.test",
    availability: "Busy",
  },
];

export const INITIAL_AUDIT_LOGS: AuditLog[] = [
  {
    id: "aud-01",
    timestamp: new Date(Date.now() - 30 * 60000).toISOString(),
    actor: "lead.recruiter@talvora.test",
    action: "STAGE_TRANSITION",
    target_type: "APPLICATION",
    target_id: "app-liam",
    target_name: "Liam O'Connor",
    details: "Promoted to Hired & Onboarding stage. Offer package $230k + equity accepted by candidate.",
  },
  {
    id: "aud-02",
    timestamp: new Date(Date.now() - 2 * 3600000).toISOString(),
    actor: "Dr. Jennifer Vance",
    action: "SCORECARD_SUBMITTED",
    target_type: "APPLICATION",
    target_id: "app-marcus",
    target_name: "Marcus Vance",
    details: "Scorecard verdict: Strong Hire (5.0). Unanimous consensus on Raft consensus and fault injection.",
  },
  {
    id: "aud-03",
    timestamp: new Date(Date.now() - 6 * 3600000).toISOString(),
    actor: "lead.recruiter@talvora.test",
    action: "INTERVIEW_SCHEDULED",
    target_type: "INTERVIEW",
    target_id: "sch-1",
    target_name: "Elena Rostova",
    details: "Scheduled Round 3: System Design & Raft Resilience with Dr. Jennifer Vance for 10:00 AM.",
  },
  {
    id: "aud-04",
    timestamp: new Date(Date.now() - 14 * 3600000).toISOString(),
    actor: "lead.recruiter@talvora.test",
    action: "PACK_GENERATED",
    target_type: "APPLICATION",
    target_id: "app-elena",
    target_name: "Elena Rostova",
    details: "Generated tailored AI Interview Pack targeting Raft consensus & outbox leasing competencies.",
  },
  {
    id: "aud-05",
    timestamp: new Date(Date.now() - 24 * 3600000).toISOString(),
    actor: "lead.recruiter@talvora.test",
    action: "CANDIDATE_ATTESTED",
    target_type: "CANDIDATE",
    target_id: "cand-elena",
    target_name: "Elena Rostova",
    details: "Verified contact address and past employer domain match with Datadog engineering record.",
  },
  {
    id: "aud-06",
    timestamp: new Date(Date.now() - 48 * 3600000).toISOString(),
    actor: "SYSTEM_SCANNER",
    action: "DOCUMENT_QUARANTINE_CLEARED",
    target_type: "DOCUMENT",
    target_id: "doc-elena",
    target_name: "elena_rostova_staff_infra.pdf",
    details: "ClamAV engine pass: 0 malicious signatures found. SHA-256 clean digest written to vault.",
  },
  {
    id: "aud-07",
    timestamp: new Date(Date.now() - 72 * 3600000).toISOString(),
    actor: "Dr. Jennifer Vance",
    action: "REQUISITION_PUBLISHED",
    target_type: "REQUISITION",
    target_id: "job-dist",
    target_name: "Senior Distributed Systems Engineer",
    details: "Published criteria framework snapshot v2 (Added monotonic lease fencing requirement).",
  },
];

export const INITIAL_INTERVIEW_PACKS: Record<string, InterviewPack> = {
  "app-elena": {
    id: "pack-elena",
    application_id: "app-elena",
    candidate_id: "cand-elena",
    job_id: "job-dist",
    generated_at: new Date(Date.now() - 14 * 3600000).toISOString(),
    candidate_summary:
      "Elena brings 9+ years of distributed systems and cloud infrastructure resilience experience from Datadog and CloudScale Core. Exceptional background in active-active multi-region failovers, monotonic lease fencing, and Python AsyncIO outbox architecture.",
    key_strengths: [
      "Designed active-active multi-region failovers sustaining 40M daily transactions with zero split-brain anomalies",
      "Authored high-throughput transactional outbox in Python 3.12 and AsyncIO with monotonic lease fencing (65,000 TPS)",
      "Deep understanding of Raft log compaction, leader election timers, and network partition recovery",
    ],
    areas_to_probe: [
      "Strategies for mitigating asymmetric network partitions where a leader can talk to quorum but not all followers",
      "Trade-offs in tuning Kafka consumer group rebalances under high-partition skew",
    ],
    technical_questions: [
      {
        question:
          "In a multi-region Raft consensus cluster, how do you prevent split-brain state mutations when an asymmetric network partition isolates the leader from half the nodes?",
        expected_answer:
          "Candidate should discuss monotonic epoch fencing, lease read bounds, pre-vote extensions to Raft, and requiring strict quorum confirmation before applying uncommitted log entries.",
        criteria_tested: "Distributed Consensus (Raft / Paxos)",
      },
      {
        question:
          "Explain how you design a transactional outbox publisher with monotonic leasing to prevent duplicate message dispatch across distributed workers.",
        expected_answer:
          "Candidate should explain database transaction commit pairing, outbox table record status transitions, acquiring a distributed lease with an incrementing fencing token, and consumer deduplication keys.",
        criteria_tested: "High-Concurrency Backend & Monotonic Leases",
      },
      {
        question:
          "How do you profile and diagnose an event-loop lag spike in a high-concurrency Python AsyncIO service under 50k requests per second?",
        expected_answer:
          "Candidate should reference asyncio debug mode, custom task instrumentation, py-spy sampling profilers, identifying blocking synchronous system calls (e.g. disk IO or DNS), and offloading to thread pools.",
        criteria_tested: "Python AsyncIO Systems Engineering",
      },
    ],
    behavioral_questions: [
      {
        question:
          "Describe a high-severity production outage where your technical diagnosis contradicted the initial consensus of the incident response team. How did you navigate the situation?",
        evaluation_focus: "Data-driven incident leadership, psychological safety, and clear evidence articulation.",
      },
      {
        question:
          "Tell us about a time you had to reject a feature architectural shortcut proposed by product leadership to protect system durability.",
        evaluation_focus: "Technical conviction, collaborative compromise, and risk framing.",
      },
    ],
    evaluation_rubric: [
      {
        competency: "Distributed Consensus & State Machine Replication",
        level_1_poor: "Confuses consensus with simple master-replica replication; unaware of split-brain failure domains.",
        level_3_acceptable: "Explains standard Raft leader election and log replication correctly.",
        level_5_exceptional: "Demonstrates deep mastery of monotonic fencing tokens, pre-vote phases, and chaos recovery patterns.",
      },
      {
        competency: "Concurrency & Async Architecture",
        level_1_poor: "Relies on coarse global mutexes; introduces deadlocks in simulated exercises.",
        level_3_acceptable: "Writes clean non-blocking async code with proper exception boundary isolation.",
        level_5_exceptional: "Designs lock-free buffer ring pipelines and demonstrates zero-copy packet memory optimizations.",
      },
      {
        competency: "Incident Command & Production Durability",
        level_1_poor: "Panics under pressure; makes unverified live mutations without audit trail.",
        level_3_acceptable: "Follows runbooks methodically and communicates status cleanly to stakeholders.",
        level_5_exceptional: "Exemplary incident commander; isolates root causes in minutes and authors preventative chaos canaries.",
      },
    ],
    is_simulated_ai: true,
  },
  "app-marcus": {
    id: "pack-marcus",
    application_id: "app-marcus",
    candidate_id: "cand-marcus",
    job_id: "job-dist",
    generated_at: new Date(Date.now() - 20 * 3600000).toISOString(),
    candidate_summary:
      "Marcus is an 11-year veteran distributed systems engineer with deep contributions to open-source Paxos and Raft implementations. Principal engineer leading large-scale multi-region event streams at Amazon Web Services.",
    key_strengths: [
      "Open-source maintainer of distributed consensus protocols in C++ and Go",
      "Architected fault-tolerant state-machine replication cluster with zero data loss under simulated chaos partitions",
      "Extensive experience tuning Linux socket buffers and TCP window scaling for low-jitter RPCs",
    ],
    areas_to_probe: [
      "Approaches to managing monotonic lease expiration across unsynchronized NTP clocks",
      "Strategies for multi-tenant rate limiting without locking shared memory structures",
    ],
    technical_questions: [
      {
        question:
          "How do you ensure monotonic consistency when node clock drift exceeds the lease duration window in distributed coordinator systems?",
        expected_answer:
          "Candidate should explain TrueTime or hybrid logical clocks (HLC), monotonic fencing counters passed to storage backends, and server-enforced validation discarding requests with stale lease tokens.",
        criteria_tested: "Monotonic Lease Fencing & Time",
      },
    ],
    behavioral_questions: [
      {
        question: "How do you cultivate engineering rigor in code reviews across a globally distributed team?",
        evaluation_focus: "Constructive feedback culture, standard setting, and technical mentorship.",
      },
    ],
    evaluation_rubric: [
      {
        competency: "Distributed System Primitives",
        level_1_poor: "Lacks understanding of clock drift and partial failures.",
        level_3_acceptable: "Understands vector clocks and lease mechanics.",
        level_5_exceptional: "World-class distributed systems authority with deep peer-reviewed contributions.",
      },
    ],
    is_simulated_ai: true,
  },
};

export function generateDynamicInterviewPack(
  candidate: Candidate,
  job: Job,
  application: Application,
): InterviewPack {
  const roleName = job?.title || "Specialist Engineer";
  const candidateName = candidate?.display_name || "Candidate";
  const company = candidate?.current_company || "Previous Technology Organization";
  const years = candidate?.experience_years || 5;

  return {
    id: `pack-${application.id}-${Date.now().toString(36)}`,
    application_id: application.id,
    candidate_id: candidate.id,
    job_id: job.id,
    generated_at: new Date().toISOString(),
    candidate_summary: `${candidateName} demonstrates ${years}+ years of professional domain mastery, currently operating at ${company}. Their profile closely aligns with the published requirements for ${roleName}, demonstrating proven criteria fit in baseline experience and technical competency.`,
    key_strengths: [
      `Demonstrated track record of technical execution with ${years}+ years relevant domain experience`,
      `Verified professional pedigree at ${company} with strong domain relevance to ${job.department || "Engineering"}`,
      `Screened at ${application.match_score.split("(")[0].trim() || "High Fit"} against published criteria v${job.version}`,
    ],
    areas_to_probe: [
      `Technical depth under high-load stress testing and unexpected failure scenarios`,
      `Experience collaborating across cross-functional product and infrastructure stakeholders`,
    ],
    technical_questions: [
      {
        question: `How would you architect an end-to-end scalable solution for the core challenges of ${roleName} under stringent availability requirements?`,
        expected_answer:
          "Candidate should articulate clear architectural trade-offs, failure domain boundaries, fault isolation, and monitoring telemetry.",
        criteria_tested: `${roleName} Core Competency`,
      },
      {
        question: `Walk through a difficult technical debugging challenge you solved at ${company}. What telemetry or diagnostic tools did you rely upon?`,
        expected_answer:
          "Candidate should explain structured root cause analysis, methodical elimination of hypotheses, and permanent remediation.",
        criteria_tested: "Deep Diagnostic & Problem Solving Skills",
      },
      {
        question: `How do you ensure zero data corruption and idempotency across async transactional workflows?`,
        expected_answer:
          "Candidate should discuss idempotency keys, database transaction semantics, deduplication tables, and distributed locking.",
        criteria_tested: "Data Integrity & Concurrency",
      },
    ],
    behavioral_questions: [
      {
        question:
          "Describe a high-stakes disagreement with senior leadership over engineering architecture. How did you advocate for your recommendation while preserving trust?",
        evaluation_focus: "Principled advocacy, empathy, business context alignment, and team trust.",
      },
      {
        question:
          "How do you balance rapid delivery timelines with the long-term maintainability of mission-critical systems?",
        evaluation_focus: "Technical debt management, pragmatism, and quality standards.",
      },
    ],
    evaluation_rubric: [
      {
        competency: "Domain Technical Architecture",
        level_1_poor: "Struggles with basic system primitives; proposes fragile designs with unaddressed single points of failure.",
        level_3_acceptable: "Delivers solid, conventional architectural patterns meeting all functional requirements.",
        level_5_exceptional: "Demonstrates world-class architectural foresight, defense-in-depth, and mastery of edge cases.",
      },
      {
        competency: "Code Quality & Engineering Craftsmanship",
        level_1_poor: "Writes messy, untested code with unhandled exceptions.",
        level_3_acceptable: "Produces clean, readable, well-tested code following organizational conventions.",
        level_5_exceptional: "Authors elegant, highly performant code with modular abstractions and comprehensive testing.",
      },
      {
        competency: "Collaborative Leadership & Mentorship",
        level_1_poor: "Defensive when challenged; works in isolation.",
        level_3_acceptable: "Collaborates effectively within their direct team and participates productively in code reviews.",
        level_5_exceptional: "Elevates the technical output of the entire organization through exemplary mentorship and clarity.",
      },
    ],
    is_simulated_ai: true,
  };
}
