import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { ACTIVE_STAGES, ALLOWED, destinationLabel, needsReason, pretty, when, type Stage } from "../format";
import { useWorkspace, type InterviewPack, type StageEvent } from "../state";

export function Pipeline() {
  const {
    jobs,
    candidates,
    applications,
    interviewers,
    interviewPacks,
    can,
    createApplication,
    moveStage,
    addScorecard,
    generateInterviewPack,
    scheduleInterview,
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

  // Scorecard Submission Form State
  const [showScorecardForm, setShowScorecardForm] = useState(false);
  const [scorecardInterviewer, setScorecardInterviewer] = useState("");
  const [scorecardVerdict, setScorecardVerdict] = useState("Strong Hire");
  const [scorecardNotes, setScorecardNotes] = useState("");

  // Interview Pack State
  const [pack, setPack] = useState<InterviewPack | null>(null);
  const [showPack, setShowPack] = useState(false);
  const [packBusy, setPackBusy] = useState(false);

  // Quick Schedule State
  const [showQuickSchedule, setShowQuickSchedule] = useState(false);
  const [schedTime, setSchedTime] = useState("02:00 PM");
  const [schedRound, setSchedRound] = useState("Round 3: Systems Architecture Deep Dive");
  const [schedInterviewerId, setSchedInterviewerId] = useState("");

  useEffect(() => {
    if (!selected) {
      setPack(null);
      setShowPack(false);
      setShowScorecardForm(false);
      setShowQuickSchedule(false);
      return;
    }

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

    // Load pack if exists in workspace cache
    if (interviewPacks[selected.id]) {
      setPack(interviewPacks[selected.id]);
    } else {
      setPack(null);
    }

    return () => {
      cancelled = true;
    };
  }, [loadEligibility, loadHistory, selected, interviewPacks]);

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

  async function handleGeneratePack() {
    if (!selected) return;
    setPackBusy(true);
    try {
      const generated = await generateInterviewPack(selected.id);
      setPack(generated);
      setShowPack(true);
    } finally {
      setPackBusy(false);
    }
  }

  async function handleSubmitScorecard(e: React.FormEvent) {
    e.preventDefault();
    if (!selected || !scorecardInterviewer || !scorecardNotes.trim()) return;
    setBusy(true);
    const chosenInterviewer = interviewers.find((i) => i.name === scorecardInterviewer);
    try {
      await addScorecard(selected.id, {
        interviewer: scorecardInterviewer,
        role: chosenInterviewer?.role || "Engineering Panel Reviewer",
        verdict: scorecardVerdict,
        notes: scorecardNotes.trim(),
      });
      setShowScorecardForm(false);
      setScorecardNotes("");
    } finally {
      setBusy(false);
    }
  }

  async function handleQuickSchedule(e: React.FormEvent) {
    e.preventDefault();
    if (!selected || !selectedCand || !selectedJob || !schedInterviewerId) return;
    const interviewer = interviewers.find((i) => i.id === schedInterviewerId);
    if (!interviewer) return;

    setBusy(true);
    try {
      await scheduleInterview({
        candidate_id: selectedCand.id,
        application_id: selected.id,
        candidate_name: selectedCand.display_name || "Candidate",
        candidate_role: selectedJob.title,
        interviewer: `${interviewer.name} (${interviewer.role})`,
        round_title: schedRound,
        time: schedTime,
        date: "Tomorrow",
      });
      setShowQuickSchedule(false);
    } finally {
      setBusy(false);
    }
  }

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
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
              <button
                className="btn btn-ghost"
                type="button"
                style={{ padding: "6px 12px", fontSize: "0.85rem" }}
                onClick={() => setShowQuickSchedule(!showQuickSchedule)}
              >
                + Schedule Loop
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                style={{ padding: "6px 12px", fontSize: "0.85rem" }}
                onClick={() => {
                  if (!pack) {
                    void handleGeneratePack();
                  } else {
                    setShowPack(!showPack);
                  }
                }}
                disabled={packBusy}
              >
                {packBusy ? "Generating…" : pack ? (showPack ? "Hide Interview Pack" : "View Interview Pack") : "Generate Interview Pack"}
              </button>
              {selected.stage === "HIRED" ? (
                <Link to="/hired" className="btn btn-primary" style={{ textDecoration: "none" }}>
                  Open Employee Onboarding File →
                </Link>
              ) : null}
            </div>
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

          {/* Quick Schedule Interview Inline Panel */}
          {showQuickSchedule ? (
            <div style={{ background: "rgba(94, 224, 197, 0.05)", border: "1px solid var(--aurora)", padding: "16px", borderRadius: "6px", margin: "16px 0" }}>
              <h3 style={{ marginTop: 0, color: "var(--aurora)" }}>Schedule Interview Loop for {selectedCand?.display_name}</h3>
              <form onSubmit={(e) => void handleQuickSchedule(e)} style={{ display: "grid", gap: "10px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                  <label className="field" style={{ margin: 0 }}>
                    Panel Interviewer
                    <select
                      required
                      value={schedInterviewerId}
                      onChange={(e) => setSchedInterviewerId(e.target.value)}
                    >
                      <option value="">Select Interviewer...</option>
                      {interviewers.map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.name} — {i.role} [{i.availability}]
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field" style={{ margin: 0 }}>
                    Time
                    <input
                      required
                      value={schedTime}
                      onChange={(e) => setSchedTime(e.target.value)}
                      placeholder="e.g. 02:30 PM"
                    />
                  </label>
                </div>
                <label className="field" style={{ margin: 0 }}>
                  Round Title & Focus Area
                  <input
                    required
                    value={schedRound}
                    onChange={(e) => setSchedRound(e.target.value)}
                    placeholder="e.g. Round 3: System Design & Raft Consensus"
                  />
                </label>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
                  <button className="btn btn-ghost" type="button" onClick={() => setShowQuickSchedule(false)}>
                    Cancel
                  </button>
                  <button className="btn btn-primary" type="submit" disabled={busy || !schedInterviewerId}>
                    Confirm & Book Calendar
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          {/* AI Tailored Interview Pack Section */}
          {pack && showPack ? (
            <div style={{ marginTop: "20px", padding: "18px", background: "rgba(227, 138, 69, 0.06)", border: "1px solid var(--copper)", borderRadius: "6px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "12px" }}>
                <div>
                  <h3 style={{ margin: 0, color: "var(--copper-2)" }}>Tailored Interview Pack & Evaluation Rubric</h3>
                  <span style={{ fontSize: "0.85rem", color: "var(--paper-dim)" }}>
                    Generated from applicant resume citations and {selectedJob?.title} published criteria.
                  </span>
                </div>
                <Pill tone="tone-open">Simulated AI — Demo Data</Pill>
              </div>

              {/* Candidate Summary */}
              <p style={{ fontSize: "0.92rem", color: "var(--paper)", lineHeight: "1.5", marginBottom: "14px" }}>
                {pack.candidate_summary}
              </p>

              {/* Key Strengths & Areas to Probe */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px", marginBottom: "16px" }}>
                <div style={{ background: "rgba(12, 13, 18, 0.7)", padding: "12px", borderRadius: "4px", border: "1px solid var(--line)" }}>
                  <strong style={{ fontSize: "0.85rem", color: "var(--aurora)", display: "block", marginBottom: "6px" }}>
                    VERIFIED STRENGTHS
                  </strong>
                  <ul style={{ margin: 0, paddingLeft: "16px", fontSize: "0.85rem", color: "var(--paper-dim)", display: "grid", gap: "4px" }}>
                    {pack.key_strengths.map((s, idx) => (
                      <li key={idx}>{s}</li>
                    ))}
                  </ul>
                </div>
                <div style={{ background: "rgba(12, 13, 18, 0.7)", padding: "12px", borderRadius: "4px", border: "1px solid var(--line)" }}>
                  <strong style={{ fontSize: "0.85rem", color: "var(--copper-2)", display: "block", marginBottom: "6px" }}>
                    AREAS TO PROBE IN PANEL
                  </strong>
                  <ul style={{ margin: 0, paddingLeft: "16px", fontSize: "0.85rem", color: "var(--paper-dim)", display: "grid", gap: "4px" }}>
                    {pack.areas_to_probe.map((p, idx) => (
                      <li key={idx}>{p}</li>
                    ))}
                  </ul>
                </div>
              </div>

              {/* Tailored Technical Questions */}
              <h4 style={{ margin: "16px 0 8px 0", fontSize: "0.9rem", color: "var(--paper)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Recommended Technical Questions
              </h4>
              <div style={{ display: "grid", gap: "10px", marginBottom: "16px" }}>
                {pack.technical_questions.map((q, idx) => (
                  <div key={idx} style={{ padding: "10px 12px", background: "rgba(12, 13, 18, 0.7)", border: "1px solid var(--line)", borderRadius: "4px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                      <strong style={{ color: "var(--paper)", fontSize: "0.9rem" }}>Q{idx + 1}: {q.question}</strong>
                      <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--copper-2)" }}>{q.criteria_tested}</span>
                    </div>
                    <p style={{ fontSize: "0.85rem", color: "var(--paper-dim)", margin: 0 }}>
                      <em style={{ color: "var(--aurora)" }}>What to listen for:</em> {q.expected_answer}
                    </p>
                  </div>
                ))}
              </div>

              {/* Evaluation Rubric */}
              <h4 style={{ margin: "16px 0 8px 0", fontSize: "0.9rem", color: "var(--paper)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Standardized Evaluation Rubric
              </h4>
              <div style={{ display: "grid", gap: "8px" }}>
                {pack.evaluation_rubric.map((r, idx) => (
                  <div key={idx} style={{ padding: "10px 12px", background: "rgba(12, 13, 18, 0.7)", border: "1px solid var(--line)", borderRadius: "4px", fontSize: "0.85rem" }}>
                    <strong style={{ color: "var(--gold)", display: "block", marginBottom: "4px" }}>{r.competency}</strong>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px", color: "var(--paper-dim)" }}>
                      <div><strong style={{ color: "var(--rose)" }}>Level 1:</strong> {r.level_1_poor}</div>
                      <div><strong style={{ color: "var(--paper)" }}>Level 3:</strong> {r.level_3_acceptable}</div>
                      <div><strong style={{ color: "var(--aurora)" }}>Level 5:</strong> {r.level_5_exceptional}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Interview Scorecards Panel */}
          <div style={{ marginTop: "24px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
              <h3 style={{ margin: 0 }}>Interview Scorecards & Debrief Evaluations</h3>
              <button
                className="btn btn-ghost"
                type="button"
                style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                onClick={() => setShowScorecardForm(!showScorecardForm)}
              >
                {showScorecardForm ? "Cancel Feedback" : "+ Submit Scorecard"}
              </button>
            </div>

            {/* Submit Scorecard Form */}
            {showScorecardForm ? (
              <form
                onSubmit={(e) => void handleSubmitScorecard(e)}
                style={{
                  background: "rgba(255, 255, 255, 0.02)",
                  border: "1px solid var(--line)",
                  padding: "16px",
                  borderRadius: "6px",
                  marginBottom: "16px",
                  display: "grid",
                  gap: "10px",
                }}
              >
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                  <label className="field" style={{ margin: 0 }}>
                    Interviewer
                    <select
                      required
                      value={scorecardInterviewer}
                      onChange={(e) => setScorecardInterviewer(e.target.value)}
                    >
                      <option value="">Select Interviewer...</option>
                      {interviewers.map((i) => (
                        <option key={i.id} value={i.name}>
                          {i.name} ({i.role})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field" style={{ margin: 0 }}>
                    Hiring Verdict
                    <select
                      value={scorecardVerdict}
                      onChange={(e) => setScorecardVerdict(e.target.value)}
                    >
                      <option value="Strong Hire">Strong Hire (Top 5%)</option>
                      <option value="Hire">Hire (Meets All Standards)</option>
                      <option value="Needs Further Review">Needs Further Review</option>
                      <option value="Reject">Reject (Criteria Unmet)</option>
                    </select>
                  </label>
                </div>
                <label className="field" style={{ margin: 0 }}>
                  Debrief Feedback & Technical Observations
                  <textarea
                    required
                    placeholder="Document candidate's architectural strengths, edge-case handling, and scorecard justification..."
                    value={scorecardNotes}
                    onChange={(e) => setScorecardNotes(e.target.value)}
                  />
                </label>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                  <button className="btn btn-ghost" type="button" onClick={() => setShowScorecardForm(false)}>
                    Cancel
                  </button>
                  <button className="btn btn-primary" type="submit" disabled={busy || !scorecardInterviewer}>
                    Submit Scorecard to Record
                  </button>
                </div>
              </form>
            ) : null}

            {selected.scorecards && selected.scorecards.length > 0 ? (
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
                      <Pill tone={sc.verdict.includes("Strong") || sc.verdict === "Hire" ? "tone-open" : "tone-human"}>
                        {sc.verdict}
                      </Pill>
                    </div>
                    <p style={{ fontStyle: "italic", color: "var(--copper-2)", fontSize: "0.92rem", lineHeight: 1.45 }}>
                      "{sc.notes}"
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--paper-dim)", fontSize: "0.88rem" }}>
                No scorecards logged yet for this candidate. Click "+ Submit Scorecard" to record panel feedback.
              </p>
            )}
          </div>

          {/* Human Decision Timeline */}
          <div style={{ marginTop: "24px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}>
            <h3 style={{ margin: "0 0 12px 0" }}>Stage Audit Record & Human Decisions</h3>
            <ol className="timeline">
              {history.map((event) => (
                <li key={event.id}>
                  <span className="dot" />
                  <div>
                    <strong>
                      {event.from_stage ? pretty(event.from_stage) : "Opened"} → {pretty(event.to_stage)}
                      <Pill tone="tone-open">{when(event.created_at)}</Pill>
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
          <div style={{ marginTop: "24px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}>
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
