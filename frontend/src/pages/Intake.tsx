import { useEffect, useState } from "react";
import { PageHead, Locked, Pill } from "../components/Bits";
import { jobs } from "../data";
import { bytes, pretty } from "../format";
import { useWorkspace } from "../state";

const CHAMBERS = [
  {
    id: "hold",
    title: "Holding",
    copy: "Reserved, then quarantined. The object key is opaque.",
    statuses: ["RESERVED", "QUARANTINED"],
  },
  {
    id: "scan",
    title: "Scanner",
    copy: "ClamAV has the bytes. Extraction waits.",
    statuses: ["SCANNING"],
  },
  {
    id: "verdict",
    title: "Verdict",
    copy: "Clean may leave. Blocked never becomes evidence.",
    statuses: ["CLEAN", "BLOCKED", "FAILED", "CANCELED"],
  },
] as const;

export function Intake() {
  const { intake, can, admit, stepIntake, role } = useWorkspace();
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const [jobId, setJobId] = useState(openJobs[0]?.id ?? "");
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (role === "INTERVIEWER") return;
    const timer = window.setInterval(stepIntake, 1600);
    return () => window.clearInterval(timer);
  }, [role, stepIntake]);

  if (!can("READ_DOCUMENTS")) {
    return (
      <Locked
        title="Interviewers do not enter the airlock."
        body="This role can read an application and file feedback. It cannot admit resumes, watch a scan, or open document bytes."
      />
    );
  }

  function take(files: FileList | null) {
    if (!files) return;
    setError(admit(jobId, files));
  }

  return (
    <div>
      <PageHead
        kicker="Intake · quarantine airlock"
        title="Guilty until the scanner says clean."
        lede="A batch binds to one open vacancy. Each file becomes a draft candidate only for that item — Talvora does not guess that two files are the same person. PDF and DOCX can pass. Macros, executables, and odd types are refused."
      />
      {can("MANAGE_DOCUMENT_INTAKE") ? (
        <div className="intake-controls">
          <label className="field">
            Open vacancy
            <select value={jobId} onChange={(event) => setJobId(event.target.value)}>
              {openJobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
                </option>
              ))}
            </select>
          </label>
          <label
            className={over ? "drop is-over" : "drop"}
            onDragOver={(event) => {
              event.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setOver(false);
              take(event.dataTransfer.files);
            }}
          >
            <input
              type="file"
              multiple
              onChange={(event) => {
                take(event.target.files);
                event.target.value = "";
              }}
            />
            <strong>Drop resumes into quarantine</strong>
            <span>PDF and DOCX continue. A macro or an executable is caught at the scan.</span>
          </label>
        </div>
      ) : (
        <p className="banner">You can watch the airlock. This role does not admit new files.</p>
      )}
      {error ? <p className="form-error">{error}</p> : null}
      <p className="muted refuse-line">Paused and draft vacancies are absent on purpose. They cannot receive a batch.</p>
      <div className="airlock">
        {CHAMBERS.map((chamber) => (
          <section key={chamber.id} className="chamber-col" aria-label={chamber.title}>
            <header>
              <h2>{chamber.title}</h2>
              <p>{chamber.copy}</p>
            </header>
            {intake
              .filter((item) => (chamber.statuses as readonly string[]).includes(item.status))
              .map((item) => (
                <article key={item.id} className={`slip tone-${item.status.toLowerCase()}`}>
                  <header>
                    <strong>{item.filename}</strong>
                    <Pill tone={item.status === "CLEAN" ? "clean" : item.status === "BLOCKED" ? "blocked" : "human"}>
                      {pretty(item.status)}
                    </Pill>
                  </header>
                  <p>{item.who}</p>
                  <p className="hash">
                    {item.batch} · {bytes(item.bytes)} · key {item.id.replace("item-", "").slice(0, 8)}
                  </p>
                  {item.problem ? <p className="form-error">{item.problem}</p> : null}
                </article>
              ))}
          </section>
        ))}
      </div>
    </div>
  );
}
