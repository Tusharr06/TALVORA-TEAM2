import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  BATCH_ITEM_LIMIT,
  applicationSeed,
  candidateSeed,
  intakeSeed,
  jobById,
  type Application,
  type Candidate,
  type IntakeItem,
  type IntakeStatus,
} from "./data";
import {
  ALLOWED,
  ROLE_CAPS,
  needsReason,
  pretty,
  type Cap,
  type Role,
  type Stage,
} from "./format";

const STORAGE_KEY = "talvora-desk-preview";

type Persisted = {
  role: Role;
  candidates: Candidate[];
  applications: Application[];
  intake: IntakeItem[];
  reviewedRuns: string[];
  dismissedDupes: string[];
};

type Workspace = {
  role: Role;
  setRole: (role: Role) => void;
  candidates: Candidate[];
  applications: Application[];
  intake: IntakeItem[];
  reviewedRuns: string[];
  dismissedDupes: string[];
  notice: string | null;
  dismissNotice: () => void;
  can: (cap: Cap) => boolean;
  transition: (id: string, to: Stage, reason: string) => string | null;
  attest: (candidateId: string, note: string) => string | null;
  feedback: (id: string, body: string) => string | null;
  admit: (jobId: string, files: FileList | File[]) => string | null;
  stepIntake: () => void;
  ackRun: (id: string) => void;
  dismissDupe: (id: string) => void;
  reset: () => void;
};

const WorkspaceContext = createContext<Workspace | null>(null);

function readPersisted(): Persisted | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<Persisted>;
    if (!data.role || !(data.role in ROLE_CAPS)) return null;
    if (!Array.isArray(data.candidates) || !Array.isArray(data.applications) || !Array.isArray(data.intake)) {
      return null;
    }
    return {
      role: data.role,
      candidates: data.candidates,
      applications: data.applications,
      intake: data.intake,
      reviewedRuns: Array.isArray(data.reviewedRuns) ? data.reviewedRuns : [],
      dismissedDupes: Array.isArray(data.dismissedDupes) ? data.dismissedDupes : [],
    };
  } catch {
    return null;
  }
}

function advance(item: IntakeItem): IntakeItem {
  const next: Partial<Record<IntakeStatus, IntakeStatus>> = {
    RESERVED: "QUARANTINED",
    QUARANTINED: "SCANNING",
    SCANNING: item.verdict === "CLEAN" ? "CLEAN" : "BLOCKED",
  };
  const status = next[item.status];
  if (!status) return item;
  return { ...item, status };
}

function judge(file: File): Pick<IntakeItem, "verdict" | "problem" | "mediaType"> {
  const name = file.name.toLowerCase();
  if (file.size === 0) {
    return { verdict: "BLOCKED", problem: "EMPTY_FILE", mediaType: file.type || "application/octet-stream" };
  }
  if (file.size > 8_000_000) {
    return { verdict: "BLOCKED", problem: "FILE_TOO_LARGE", mediaType: file.type || "application/octet-stream" };
  }
  if (name.endsWith(".exe") || name.endsWith(".js") || name.includes("payload")) {
    return {
      verdict: "BLOCKED",
      problem: "EMBEDDED_EXECUTABLE_BLOCKED",
      mediaType: file.type || "application/octet-stream",
    };
  }
  if (name.includes("macro") || name.endsWith(".docm")) {
    return {
      verdict: "BLOCKED",
      problem: "MACRO_CONTENT_BLOCKED",
      mediaType: "application/vnd.ms-word.document.macroEnabled.12",
    };
  }
  if (name.endsWith(".pdf")) {
    return { verdict: "CLEAN", problem: null, mediaType: "application/pdf" };
  }
  if (name.endsWith(".docx")) {
    return {
      verdict: "CLEAN",
      problem: null,
      mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    };
  }
  return {
    verdict: "BLOCKED",
    problem: "UNSUPPORTED_MEDIA_TYPE",
    mediaType: file.type || "application/octet-stream",
  };
}

function labelFrom(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  return base.slice(0, 48) || "Untitled draft";
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<Role>(() => readPersisted()?.role ?? "RECRUITER");
  const [candidates, setCandidates] = useState<Candidate[]>(
    () => readPersisted()?.candidates ?? structuredClone(candidateSeed),
  );
  const [applications, setApplications] = useState<Application[]>(
    () => readPersisted()?.applications ?? structuredClone(applicationSeed),
  );
  const [intake, setIntake] = useState<IntakeItem[]>(
    () => readPersisted()?.intake ?? structuredClone(intakeSeed),
  );
  const [reviewedRuns, setReviewedRuns] = useState<string[]>(() => readPersisted()?.reviewedRuns ?? []);
  const [dismissedDupes, setDismissedDupes] = useState<string[]>(
    () => readPersisted()?.dismissedDupes ?? [],
  );
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const payload: Persisted = {
      role,
      candidates,
      applications,
      intake,
      reviewedRuns,
      dismissedDupes,
    };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }, [role, candidates, applications, intake, reviewedRuns, dismissedDupes]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const stepIntake = useCallback(() => {
    setIntake((items) => {
      let changed = false;
      const next = items.map((item) => {
        const stepped = advance(item);
        if (stepped.status !== item.status) changed = true;
        return stepped;
      });
      return changed ? next : items;
    });
  }, []);

  const can = (cap: Cap) => ROLE_CAPS[role].includes(cap);

  const value = useMemo<Workspace>(() => {
    return {
      role,
      setRole,
      candidates,
      applications,
      intake,
      reviewedRuns,
      dismissedDupes,
      notice,
      dismissNotice: () => setNotice(null),
      can,
      transition: (id, to, reason) => {
        if (!can("TRANSITION_STAGE")) {
          return "This preview role cannot write stages. Interviewers file feedback instead.";
        }
        const current = applications.find((item) => item.id === id);
        if (!current) return "That application is not on the desk.";
        if (!ALLOWED[current.stage].includes(to)) {
          return "That move is not in the allowed stage matrix.";
        }
        const trimmed = reason.trim();
        if (needsReason(current.stage, to) && trimmed.length < 12) {
          return "Write a full reason. Rejection, withdrawal, and reopening are stored with one.";
        }
        const event = {
          id: `evt-${crypto.randomUUID()}`,
          from: current.stage,
          to,
          actor: `Mira Sen · ${pretty(role)}`,
          reason: trimmed.length ? trimmed : null,
          at: new Date().toISOString(),
        };
        setApplications((items) =>
          items.map((item) =>
            item.id === id
              ? {
                  ...item,
                  stage: to,
                  version: item.version + 1,
                  attention: null,
                  history: [...item.history, event],
                }
              : item,
          ),
        );
        setNotice(`Stage written by ${event.actor}. Record version ${current.version + 1}.`);
        return null;
      },
      attest: (candidateId, note) => {
        if (!can("VERIFY_CANDIDATE_CONTACT")) {
          return "This role does not attest contact. A recruiter, admin, or owner does.";
        }
        const candidate = candidates.find((item) => item.id === candidateId);
        if (!candidate) return "That candidate is not on the desk.";
        const latest = candidate.contacts[candidate.contacts.length - 1];
        if (!latest) return "There is no contact revision to attest.";
        if (candidate.verification?.revision === latest.version) {
          return "The latest revision is already attested.";
        }
        const trimmed = note.trim();
        if (trimmed.length < 8) {
          return "Add a short note. An attestation without context is not a record.";
        }
        setCandidates((items) =>
          items.map((item) =>
            item.id === candidateId
              ? {
                  ...item,
                  verification: {
                    revision: latest.version,
                    source: "RECRUITER_ATTESTATION",
                    actor: "Mira Sen",
                    note: trimmed,
                    at: new Date().toISOString(),
                  },
                }
              : item,
          ),
        );
        setNotice(
          `Contact attested for ${candidate.name}. This is not proof they own the mailbox.`,
        );
        return null;
      },
      feedback: (id, body) => {
        if (!can("SUBMIT_FEEDBACK")) return "This role cannot file feedback.";
        const trimmed = body.trim();
        if (trimmed.length < 8) return "Write the note you actually want on the record.";
        const author = `Mira Sen · ${pretty(role)}`;
        setApplications((items) =>
          items.map((item) =>
            item.id === id
              ? {
                  ...item,
                  feedback: [
                    ...item.feedback,
                    { id: `fb-${crypto.randomUUID()}`, author, body: trimmed, at: new Date().toISOString() },
                  ],
                }
              : item,
          ),
        );
        setNotice("Feedback filed. It does not move the stage.");
        return null;
      },
      stepIntake,
      admit: (jobId, files) => {
        if (!can("MANAGE_DOCUMENT_INTAKE")) {
          return "This role does not admit files. Interviewers do not see document bytes.";
        }
        const job = jobById(jobId);
        if (!job || job.status !== "OPEN") {
          return "Intake only binds to an open vacancy. Paused and draft roles take no files.";
        }
        const list = Array.from(files);
        if (list.length === 0) return "Choose a file to quarantine.";
        if (intake.length + list.length > BATCH_ITEM_LIMIT) {
          return `This preview batch stops at ${BATCH_ITEM_LIMIT} files.`;
        }
        const created: IntakeItem[] = list.map((file) => {
          const judged = judge(file);
          return {
            id: `item-${crypto.randomUUID()}`,
            batch: "Live drop · 06 Nov",
            jobId,
            filename: file.name,
            mediaType: judged.mediaType,
            bytes: file.size,
            status: "RESERVED",
            verdict: judged.verdict,
            problem: judged.problem,
            who: `${labelFrom(file.name)} · draft`,
          };
        });
        setIntake((items) => [...created, ...items]);
        setNotice(
          created.length === 1
            ? "File reserved in quarantine. A clean scan is the only way out."
            : `${created.length} files reserved in quarantine.`,
        );
        return null;
      },
      ackRun: (id) => {
        setReviewedRuns((items) => (items.includes(id) ? items : [...items, id]));
        setNotice("Reviewed. The quote underneath this revision stays put.");
      },
      dismissDupe: (id) => {
        setDismissedDupes((items) => (items.includes(id) ? items : [...items, id]));
        setNotice("Kept separate. Nothing was merged.");
      },
      reset: () => {
        sessionStorage.removeItem(STORAGE_KEY);
        setRole("RECRUITER");
        setCandidates(structuredClone(candidateSeed));
        setApplications(structuredClone(applicationSeed));
        setIntake(structuredClone(intakeSeed));
        setReviewedRuns([]);
        setDismissedDupes([]);
        setNotice("Desk reset to the Friday fixture.");
      },
    };
  }, [role, candidates, applications, intake, reviewedRuns, dismissedDupes, notice, stepIntake]);

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): Workspace {
  const value = useContext(WorkspaceContext);
  if (!value) {
    throw new Error("Talvora workspace is missing its provider.");
  }
  return value;
}
