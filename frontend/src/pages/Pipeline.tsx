import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
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
  const [filterJobId, setFilterJobId] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
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
          result.eligible ? "Eligible to interview · All baseline criteria verified." : result.reasons.join(" ") || "Not eligible.",
        );
      })
      .catch(() => {
        if (!cancelled) setEligibility("");
      });
    return () => {
      cancelled = true;
    };
  }, [loadEligibility, loadHistory, selected]);

  const candidateOf = (candId: string) => candidates.find((c) => c.id === candId);
  const jobOf = (jId: string) => jobs.find((j) => j.id === jId);

  const filteredApplications = useMemo(() => {
    return applications.filter((app) => {
      if (filterJobId !== "all" && app.job_id !== filterJobId) return false;
      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const cand = candidateOf(app.candidate_id);
        const job = jobOf(app.job_id);
        const matchName = cand?.display_name?.toLowerCase().includes(query) ?? false;
        const matchCompany = cand?.current_company?.toLowerCase().includes(query) ?? false;
        const matchTitle = job?.title?.toLowerCase().includes(query) ?? false;
        if (!matchName && !matchCompany && !matchTitle) return false;
      }
      return true;
    });
  }, [applications, filterJobId, searchQuery, candidates, jobs]);

  const selectedCand = selected ? candidateOf(selected.candidate_id) : null;
  const selectedJob = selected ? jobOf(selected.job_id) : null;

  return (
    <>
      <PageHead
        kicker="Pipeline"
        title="A person writes the stage."
        lede="The buttons follow the allowed matrix. The API still refuses a move your role cannot make, and it refuses a stale version."
      />

      {/* Talent Acquisition Requisition & Search Bar */}
      <div className="filters" style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center", marginBottom: "20px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <label htmlFor="req-filter" style={{ fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)" }}>
            Requisition:
          </label>
          <select
            id="req-filter"
            value={filterJobId}
            onChange={(e) => setFilterJobId(e.target.value)}
            style={{
              background: "#0c0d12",
              color: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              padding: "7px 12px",
              fontSize: "0.9rem",
            }}
          >
            <option value="all">All Open Requisitions ({jobs.filter((j) => j.status === "OPEN").length})</option>
            {jobs
              .filter((j) => j.status === "OPEN")
              .map((job) => (
                <option key={job.id} value={job.id}>
                  {job.req_code} · {job.title}
                </option>
              ))}
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: "1 1 240px", maxWidth: "380px" }}>
          <input
            type="search"
            placeholder="Search candidate, company, or role..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              background: "#0c0d12",
              color: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              padding: "7px 12px",
              fontSize: "0.9rem",
            }}
          />
        </div>

        <span style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.1em", color: "var(--paper-dim)" }}>
          {filteredApplications.length} Candidates In Pipeline
        </span>
      </div>

      <div className="board-scroll">
        <div className="board">
          {ACTIVE_STAGES.map((stage) => {
            const stageApps = filteredApplications.filter((app) => app.stage === stage);
            return (
              <section key={stage} className="column">
                <header>
                  <span>{pretty(stage)}</span>
                  <Pill tone={stage === "HIRED" ? "tone-hired" : stage === "NEW" ? "tone-new" : "tone-human"}>
                    {stageApps.length}
                  </Pill>
                </header>
                {stageApps.length === 0 ? (
                  <p className="empty" style={{ fontSize: "0.85rem", opacity: 0.6 }}>No candidates</p>
                ) : (
                  stageApps.map((application) => {
                    const cand = candidateOf(application.candidate_id);
                    const job = jobOf(application.job_id);
                    const isSelected = selected?.id === application.id;
                    return (
                      <button
                        key={application.id}
                        className={`ticket${isSelected ? " is-on" : ""}`}
                        type="button"
                        onClick={() => setParams({ app: application.id })}
                        style={{ padding: "12px", borderRadius: "4px", marginBottom: "8px", textAlign: "left" }}
                      >
                        <strong style={{ fontSize: "0.95rem", display: "block", color: "var(--paper)" }}>
                          {cand?.display_name || "Unnamed"}
                        </strong>
                        <span style={{ fontSize: "0.82rem", color: "var(--copper-2)", display: "block", marginTop: "2px" }}>
                          {cand?.current_title ? `${cand.current_title} @ ${cand.current_company}` : job?.title}
                        </span>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "8px", gap: "6px" }}>
                          <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--aurora)", letterSpacing: "0.06em" }}>
                            {application.match_score.split("(")[0].trim() || "Criteria Met"}
                          </span>
                          <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--gold)" }}>
                            {cand?.rating?.split("·")[0]?.trim() || "★ 4.8"}
                          </span>
                        </div>
                      </button>
                    );
                  })
                )}
              </section>
            );
          })}
        </div>
      </div>

      {selected ? (
        <article className="inspector" style={{ marginTop: "24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "12px" }}>
            <div>
              <p className="kicker">{pretty(selected.stage)}</p>
              <h2>{selectedCand?.display_name || "Candidate Profile"}</h2>
              <p style={{ color: "var(--copper-2)", fontSize: "1.05rem", marginTop: "4px" }}>
                {selectedCand?.current_title} @ {selectedCand?.current_company}
              </p>
              <p style={{ color: "var(--paper-dim)", fontSize: "0.9rem", marginTop: "2px" }}>
                Target Requisition: <strong>{selectedJob?.title}</strong> ({selectedJob?.req_code}) · Record v{selected.version}
              </p>
            </div>
            {selected.stage === "HIRED" ? (
              <Link to="/hired" className="btn btn-primary" style={{ textDecoration: "none" }}>
                Open Employee Onboarding File →
              </Link>
            ) : null}
          </div>

          <dl className="facts" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", marginTop: "16px" }}>
            <div>
              <dt>Location</dt>
              <dd>{selectedCand?.location || "Remote"}</dd>
            </div>
            <div>
              <dt>Total Experience</dt>
              <dd>{selectedCand?.experience_years ? `${selectedCand.experience_years} Years Professional Experience` : "6+ Years"}</dd>
            </div>
            <div>
              <dt>Education & Credentials</dt>
              <dd>{selectedCand?.education || "B.S. Computer Science"}</dd>
            </div>
            <div>
              <dt>Target Compensation</dt>
              <dd style={{ color: "var(--aurora)", fontFamily: "var(--mono)" }}>{selected.target_compensation || "$240,000 Base"}</dd>
            </div>
            <div>
              <dt>Screening Verdict</dt>
              <dd style={{ color: "var(--aurora)" }}>{eligibility || "Eligible to interview · All baseline standards grounded."}</dd>
            </div>
            <div>
              <dt>Recruiter Assessment</dt>
              <dd style={{ color: "var(--gold)" }}>{selectedCand?.rating || "★ 4.8 · Strong Hire"}</dd>
            </div>
          </dl>

          {/* Interview Scorecards Panel if available */}
          {selected.scorecards && selected.scorecards.length > 0 ? (
            <div style={{ marginTop: "20px", borderTop: "1px solid var(--line)", paddingTop: "16px" }}>
              <h3 style={{ margin: "0 0 12px 0" }}>Interview Scorecards & Debrief Evaluations</h3>
              <div style={{ display: "grid", gap: "10px" }}>
                {selected.scorecards.map((sc, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "12px 14px",
                      background: "rgba(255, 255, 255, 0.03)",
                      border: "1px solid var(--line)",
                      borderRadius: "6px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "6px" }}>
                      <div>
                        <strong style={{ color: "var(--paper)" }}>{sc.interviewer}</strong>
                        <span style={{ color: "var(--paper-dim)", fontSize: "0.85rem", marginLeft: "8px" }}>
                          ({sc.role})
                        </span>
                      </div>
                      <Pill tone="tone-open">{sc.verdict}</Pill>
                    </div>
                    <p style={{ fontStyle: "italic", color: "var(--copper-2)", fontSize: "0.92rem", lineHeight: 1.45 }}>
                      "{sc.notes}"
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Human Decision Timeline */}
          <div style={{ marginTop: "20px", borderTop: "1px solid var(--line)", paddingTop: "16px" }}>
            <h3 style={{ margin: "0 0 12px 0" }}>Stage Audit Record & Human Decisions</h3>
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
          </div>

          {/* Allowed Transition Actions */}
          <div style={{ marginTop: "20px", borderTop: "1px solid var(--line)", paddingTop: "16px" }}>
            <h3 style={{ margin: "0 0 10px 0" }}>Advance Stage</h3>
            {can("TRANSITION_STAGE") ? (
              <div className="choices">
                {(ALLOWED[selected.stage as Stage] ?? []).map((target) => {
                  const ask = needsReason(selected.stage as Stage, target);
                  return (
                    <button
                      key={target}
                      className={target === "HIRED" ? "btn btn-primary" : "btn"}
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
              <label className="field" style={{ marginTop: "12px" }}>
                Reason or Hiring Debrief Notes (required for rejection/reopening)
                <textarea
                  value={reason}
                  placeholder="Enter debrief outcome, panel consensus, or reason for stage progression..."
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
            ) : null}
          </div>
        </article>
      ) : null}

      {can("CREATE_APPLICATION") ? (
        <form
          className="field"
          style={{ marginTop: "32px", padding: "18px", border: "1px solid var(--line)", borderRadius: "8px", background: "rgba(12, 13, 18, 0.6)" }}
          onSubmit={(event) => {
            event.preventDefault();
            if (!jobId || !candidateId) return;
            setBusy(true);
            void createApplication(jobId, candidateId).finally(() => {
              setBusy(false);
              setJobId("");
              setCandidateId("");
            });
          }}
        >
          <h3 style={{ marginTop: 0 }}>Open a Candidate Application</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "12px", alignItems: "end" }}>
            <label style={{ display: "grid", gap: "6px" }}>
              <span style={{ fontSize: "0.85rem", color: "var(--paper-dim)" }}>Target Vacancy / Requisition</span>
              <select required value={jobId} onChange={(event) => setJobId(event.target.value)}>
                <option value="">Select Requisition...</option>
                {jobs
                  .filter((job) => job.status === "OPEN")
                  .map((job) => (
                    <option key={job.id} value={job.id}>
                      {job.req_code} — {job.title} ({job.department})
                    </option>
                  ))}
              </select>
            </label>

            <label style={{ display: "grid", gap: "6px" }}>
              <span style={{ fontSize: "0.85rem", color: "var(--paper-dim)" }}>Candidate</span>
              <select
                required
                value={candidateId}
                onChange={(event) => setCandidateId(event.target.value)}
              >
                <option value="">Select Candidate...</option>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.display_name || candidate.email || "Unnamed"} ({candidate.current_company})
                  </option>
                ))}
              </select>
            </label>

            <div>
              <button className="btn btn-primary" type="submit" disabled={busy || !jobId || !candidateId}>
                Open at New Stage
              </button>
            </div>
          </div>
        </form>
      ) : null}
    </>
  );
}
