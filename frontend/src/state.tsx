import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, api, clearCsrf, startLogin } from "./api";

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
  title: string;
  description: string;
  department: string | null;
  location: string | null;
  employment_type: string;
  status: string;
  current_published_version_number: number | null;
  version: number;
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
  source: string;
  is_draft: boolean;
  current_contact_revision_number: number;
  is_contact_verified: boolean;
  version: number;
  created_at: string;
};

export type Application = {
  id: string;
  job_id: string;
  candidate_id: string;
  stage: string;
  source: string;
  notes: string | null;
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

type Page<T> = { items: T[]; next_cursor: string | null; total_count: number };

type Status = "loading" | "signed-out" | "ready" | "down";

const DOC_KEY = "talvora-live-documents";
let refreshInFlight: Promise<void> | null = null;

function loadHeld(): HeldDocument[] {
  try {
    const raw = sessionStorage.getItem(DOC_KEY);
    return raw ? (JSON.parse(raw) as HeldDocument[]) : [];
  } catch {
    return [];
  }
}

function explain(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

type Workspace = {
  status: Status;
  me: Me | null;
  jobs: Job[];
  candidates: Candidate[];
  applications: Application[];
  documents: HeldDocument[];
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
  uploadResume: (jobId: string, file: File) => Promise<void>;
  loadVersions: (jobId: string) => Promise<RequirementVersion[]>;
  loadHistory: (applicationId: string) => Promise<StageEvent[]>;
  loadEligibility: (applicationId: string) => Promise<{ eligible: boolean; reasons: string[] }>;
  loadSource: (documentId: string, version: number) => Promise<SourceView>;
};

const WorkspaceContext = createContext<Workspace | null>(null);

async function ensureTenant(me: Me): Promise<Me> {
  if (me.active_organization_id) return me;
  let organizationId = me.organizations[0]?.id;
  if (!organizationId) {
    const created = await api<{ id: string }>("/api/v1/organizations", {
      method: "POST",
      body: JSON.stringify({
        name: "Talvora",
        slug: `talvora-${crypto.randomUUID().slice(0, 8)}`,
      }),
    });
    organizationId = created.id;
  }
  await api(`/api/v1/organizations/${organizationId}/select`, { method: "POST" });
  clearCsrf();
  return api<Me>("/api/v1/auth/me");
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [documents, setDocuments] = useState<HeldDocument[]>(loadHeld);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    sessionStorage.setItem(DOC_KEY, JSON.stringify(documents));
  }, [documents]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const remember = useCallback((item: HeldDocument) => {
    setDocuments((current) => [item, ...current.filter((doc) => doc.id !== item.id)].slice(0, 12));
  }, []);

  const refresh = useCallback(async () => {
    if (refreshInFlight) return refreshInFlight;
    const run = async () => {
      setError(null);
      try {
        let profile = await api<Me>("/api/v1/auth/me");
        if (!profile.active_organization_id) profile = await ensureTenant(profile);
        setMe(profile);
        const [jobPage, candidatePage, applicationPage] = await Promise.all([
          api<Page<Job>>("/api/v1/jobs?limit=100"),
          api<Page<Candidate>>("/api/v1/candidates?limit=100"),
          api<Page<Application>>("/api/v1/applications?limit=100"),
        ]);
        setJobs(jobPage.items);
        setCandidates(candidatePage.items);
        setApplications(applicationPage.items);
        setStatus("ready");
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          setMe(null);
          setJobs([]);
          setCandidates([]);
          setApplications([]);
          setStatus("signed-out");
          return;
        }
        setError(explain(caught));
        setStatus("down");
      }
    };
    refreshInFlight = run().finally(() => {
      refreshInFlight = null;
    });
    return refreshInFlight;
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = useCallback(
    async (work: () => Promise<string>) => {
      try {
        const message = await work();
        setNotice(message);
        await refresh();
      } catch (caught) {
        setNotice(explain(caught));
      }
    },
    [refresh],
  );

  const value = useMemo<Workspace>(
    () => ({
      status,
      me,
      jobs,
      candidates,
      applications,
      documents,
      notice,
      error,
      can: (capability) => Boolean(me?.capabilities.includes(capability)),
      dismissNotice: () => setNotice(null),
      signIn: async () => {
        try {
          await startLogin("/desk");
        } catch (caught) {
          setError(explain(caught));
          setStatus("down");
        }
      },
      signOut: async () => {
        try {
          await api("/api/v1/auth/logout", { method: "POST" });
        } catch {
          /* The cookie is cleared locally even if the revoke call fails. */
        }
        clearCsrf();
        setMe(null);
        setJobs([]);
        setCandidates([]);
        setApplications([]);
        setStatus("signed-out");
      },
      refresh,
      createJob: (input) =>
        act(async () => {
          const draft = input.skill.trim()
            ? [
                {
                  criterion_type: "SKILL_PRESENT",
                  is_required: true,
                  source_requirement_text: `${input.skill.trim()} is required for this vacancy.`,
                  relevance_rationale: "Published by the operator as the hiring rule.",
                  configuration: { criterion_type: "SKILL_PRESENT", skill_name: input.skill.trim() },
                },
              ]
            : undefined;
          const created = await api<Job>("/api/v1/jobs", {
            method: "POST",
            body: JSON.stringify({
              title: input.title.trim(),
              description: input.description.trim(),
              department: input.department.trim() || null,
              location: input.location.trim() || null,
              ...(draft ? { draft_requirements: draft } : {}),
            }),
          });
          if (input.publish) {
            if (!draft) throw new ApiError(400, "Publish needs one skill. Drafts can wait.");
            await api(`/api/v1/jobs/${created.id}/publish`, {
              method: "POST",
              body: JSON.stringify({
                expected_version: created.version,
                change_summary: "Initial published rule.",
              }),
            });
            return `${created.title} is open. That rule is now law.`;
          }
          return `${created.title} is a draft. It screens nobody until you publish.`;
        }),
      createCandidate: (input) =>
        act(async () => {
          const created = await api<{
            candidate: Candidate;
            duplicate_suggestions: unknown[];
          }>("/api/v1/candidates", {
            method: "POST",
            body: JSON.stringify({
              display_name: input.display_name.trim(),
              email: input.email.trim(),
              source: "MANUAL_ENTRY",
              is_draft: false,
            }),
          });
          const suggestions = created.duplicate_suggestions;
          const extra = suggestions?.length
            ? " A lookalike was suggested. Nobody was merged."
            : "";
          return `${created.candidate.display_name ?? "Candidate"} is on the record.${extra}`;
        }),
      attest: (candidate, notes) =>
        act(async () => {
          if (!candidate.email) throw new ApiError(400, "There is no address to attest.");
          await api(`/api/v1/candidates/${candidate.id}/contact-verifications`, {
            method: "POST",
            body: JSON.stringify({
              contact_revision_number: candidate.current_contact_revision_number,
              verified_address: candidate.email,
              verification_source: "RECRUITER_ATTESTATION",
              notes: notes.trim(),
              expected_version: candidate.version,
            }),
          });
          return "Attestation recorded. This is not proof the mailbox is theirs.";
        }),
      createApplication: (jobId, candidateId) =>
        act(async () => {
          await api("/api/v1/applications", {
            method: "POST",
            body: JSON.stringify({
              job_id: jobId,
              candidate_id: candidateId,
              source: "MANUAL_ENTRY",
            }),
          });
          return "Application opened at New. A person still has to move it.";
        }),
      moveStage: (application, target, reason) =>
        act(async () => {
          await api(`/api/v1/applications/${application.id}/stage-transitions`, {
            method: "POST",
            body: JSON.stringify({
              target_stage: target,
              reason: reason.trim() || null,
              expected_version: application.version,
            }),
          });
          return "Stage written by you. The model did not move this person.";
        }),
      uploadResume: (jobId, file) =>
        act(async () => {
          const batch = await api<{ id: string }>("/api/v1/upload-batches", {
            method: "POST",
            body: JSON.stringify({ job_id: jobId }),
          });
          const body = new FormData();
          body.set("file", file);
          body.set("new_candidate_is_draft", "true");
          const item = await api<{
            id: string;
            status: string;
            document_id: string | null;
            declared_filename: string | null;
            problem_code: string | null;
            problem_detail: string | null;
          }>(`/api/v1/upload-batches/${batch.id}/items`, { method: "POST", body });
          let latest = item;
          for (let attempt = 0; attempt < 6; attempt += 1) {
            if (["CLEAN", "BLOCKED", "FAILED", "CANCELED"].includes(latest.status)) break;
            await new Promise((resolve) => window.setTimeout(resolve, 1500));
            const detail = await api<{ items: typeof latest[] }>(`/api/v1/upload-batches/${batch.id}`);
            latest = detail.items.find((row) => row.id === item.id) ?? latest;
          }
          if (latest.document_id) {
            remember({
              id: latest.document_id,
              filename: latest.declared_filename ?? file.name,
              version: 1,
              status: latest.status,
              problem_code: latest.problem_code,
            });
          }
          if (latest.problem_detail) return `${file.name}: ${latest.problem_detail}`;
          return `${file.name} is ${latest.status.toLowerCase().replaceAll("_", " ")}.`;
        }),
      loadVersions: (jobId) => api<RequirementVersion[]>(`/api/v1/jobs/${jobId}/requirement-versions`),
      loadHistory: (applicationId) =>
        api<StageEvent[]>(`/api/v1/applications/${applicationId}/stage-history`),
      loadEligibility: (applicationId) =>
        api<{ eligible: boolean; reasons: string[] }>(
          `/api/v1/applications/${applicationId}/interview-eligibility`,
        ),
      loadSource: (documentId, version) =>
        api<SourceView>(`/api/v1/documents/${documentId}/versions/${version}/source`),
    }),
    [act, applications, candidates, documents, error, jobs, me, notice, refresh, remember, status],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace is missing");
  return value;
}
