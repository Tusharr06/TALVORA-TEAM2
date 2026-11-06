import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { ACTIVE_STAGES, ALLOWED, destinationLabel, needsReason, pretty, when, type Stage } from "../format";
import { useWorkspace, type StageEvent } from "../state";

export function Pipeline() {
  const {
    jobs,
    candidates,
    applications,
    can,
    createApplication,
    moveStage,
    loadHistory,
    loadEligibility,
  } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const selected = applications.find((item) => item.id === params.get("app")) ?? null;
  const [history, setHistory] = useState<StageEvent[]>([]);
  const [eligibility, setEligibility] = useState<string>("");
  const [reason, setReason] = useState("");
  const [jobId, setJobId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    void loadHistory(selected.id)
      .then((rows) => {
        if (!cancelled) setHistory(rows);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      });
    void loadEligibility(selected.id)
      .then((result) => {
        if (cancelled) return;
        setEligibility(
          result.eligible ? "Eligible to interview." : result.reasons.join(" ") || "Not eligible.",
        );
      })
      .catch(() => {
        if (!cancelled) setEligibility("");
      });
    return () => {
      cancelled = true;
    };
  }, [loadEligibility, loadHistory, selected]);

  const candidateName = (id: string) =>
    candidates.find((candidate) => candidate.id === id)?.display_name || "Unnamed";
  const jobTitle = (id: string) => jobs.find((job) => job.id === id)?.title || "Vacancy";

  return (
    <>
      <PageHead
        kicker="Pipeline"
        title="A person writes the stage."
        lede="The buttons follow the allowed matrix. The API still refuses a move your role cannot make, and it refuses a stale version."
      />
      <div className="board-scroll">
        <div className="board">
          {ACTIVE_STAGES.map((stage) => (
            <section key={stage}>
              <h2>{pretty(stage)}</h2>
              {applications
                .filter((application) => application.stage === stage)
                .map((application) => (
                  <button
                    key={application.id}
                    className={`ticket${selected?.id === application.id ? " is-on" : ""}`}
                    type="button"
                    onClick={() => setParams({ app: application.id })}
                  >
                    <strong>{candidateName(application.candidate_id)}</strong>
                    <span>{jobTitle(application.job_id)}</span>
                  </button>
                ))}
            </section>
          ))}
        </div>
      </div>
      {selected ? (
        <article className="inspector">
          <p className="kicker">{pretty(selected.stage)}</p>
          <h2>{candidateName(selected.candidate_id)}</h2>
          <p>
            {jobTitle(selected.job_id)} · record v{selected.version}
          </p>
          {eligibility ? <p>{eligibility}</p> : null}
          <ol className="timeline">
            {history.map((event) => (
              <li key={event.id}>
                <span className="dot" />
                <div>
                  <strong>
                    {event.from_stage ? pretty(event.from_stage) : "Opened"} → {pretty(event.to_stage)}
                    <Pill>{when(event.created_at)}</Pill>
                  </strong>
                  <p>
                    {pretty(event.actor_role)}
                    {event.reason ? ` · ${event.reason}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ol>
          {can("TRANSITION_STAGE") ? (
            <div className="choices">
              {(ALLOWED[selected.stage as Stage] ?? []).map((target) => {
                const ask = needsReason(selected.stage as Stage, target);
                return (
                  <button
                    key={target}
                    className="btn"
                    type="button"
                    disabled={busy || (ask && reason.trim().length === 0)}
                    onClick={() => {
                      setBusy(true);
                      void moveStage(selected, target, reason).finally(() => {
                        setBusy(false);
                        setReason("");
                      });
                    }}
                  >
                    {destinationLabel(selected.stage as Stage, target)}
                  </button>
                );
              })}
            </div>
          ) : (
            <p>This session cannot move stages.</p>
          )}
          {selected.stage !== "HIRED" && can("TRANSITION_STAGE") ? (
            <label className="field">
              Reason, when the move demands one
              <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
            </label>
          ) : null}
        </article>
      ) : null}
      {can("CREATE_APPLICATION") ? (
        <form
          className="field"
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            void createApplication(jobId, candidateId).finally(() => setBusy(false));
          }}
        >
          <h3>Open an application</h3>
          <select required value={jobId} onChange={(event) => setJobId(event.target.value)}>
            <option value="">Vacancy</option>
            {jobs
              .filter((job) => job.status === "OPEN")
              .map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
                </option>
              ))}
          </select>
          <select
            required
            value={candidateId}
            onChange={(event) => setCandidateId(event.target.value)}
          >
            <option value="">Person</option>
            {candidates.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.display_name || candidate.email || "Unnamed"}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            Open at New
          </button>
        </form>
      ) : null}
    </>
  );
}
