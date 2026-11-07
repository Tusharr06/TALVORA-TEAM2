import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { greeting, pretty, when } from "../format";
import { useWorkspace } from "../state";

export function Desk() {
  const {
    me,
    jobs,
    candidates,
    applications,
    hiredEmployees,
    schedule,
    interviewers,
    auditLogs,
    scheduleInterview,
    rescheduleInterview,
    cancelInterview,
    resetDemoData,
  } = useWorkspace();

  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const drafts = jobs.filter((job) => job.status === "DRAFT");
  const fresh = applications.filter((application) => application.stage === "NEW");
  const unverified = candidates.filter((candidate) => !candidate.is_draft && !candidate.is_contact_verified);

  // Scheduling Modal State
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [scheduleCandidateId, setScheduleCandidateId] = useState("");
  const [scheduleInterviewerId, setScheduleInterviewerId] = useState("");
  const [scheduleRound, setScheduleRound] = useState("Round 2: Technical Architecture & Systems");
  const [scheduleTime, setScheduleTime] = useState("11:30 AM");
  const [scheduleDate, setScheduleDate] = useState("Today");
  const [rescheduleItemId, setRescheduleItemId] = useState<string | null>(null);
  const [newRescheduleTime, setNewRescheduleTime] = useState("");
  const [busy, setBusy] = useState(false);

  // Funnel Analytics
  const funnelStages = useMemo(() => {
    const counts = {
      NEW: applications.filter((a) => a.stage === "NEW").length,
      IN_REVIEW: applications.filter((a) => a.stage === "IN_REVIEW").length,
      INTERVIEW_APPROVED: applications.filter((a) => a.stage === "INTERVIEW_APPROVED").length,
      INTERVIEWING: applications.filter((a) => a.stage === "INTERVIEWING").length,
      DECISION_PENDING: applications.filter((a) => a.stage === "DECISION_PENDING").length,
      HIRED: applications.filter((a) => a.stage === "HIRED").length,
    };
    const max = Math.max(...Object.values(counts), 1);
    return [
      { key: "NEW", label: "01 New Inbound", count: counts.NEW, pct: Math.round((counts.NEW / max) * 100) },
      { key: "IN_REVIEW", label: "02 In Review", count: counts.IN_REVIEW, pct: Math.round((counts.IN_REVIEW / max) * 100) },
      { key: "INTERVIEW_APPROVED", label: "03 Approved", count: counts.INTERVIEW_APPROVED, pct: Math.round((counts.INTERVIEW_APPROVED / max) * 100) },
      { key: "INTERVIEWING", label: "04 Interviewing", count: counts.INTERVIEWING, pct: Math.round((counts.INTERVIEWING / max) * 100) },
      { key: "DECISION_PENDING", label: "05 Decision", count: counts.DECISION_PENDING, pct: Math.round((counts.DECISION_PENDING / max) * 100) },
      { key: "HIRED", label: "06 Hired & Onboarded", count: counts.HIRED, pct: Math.round((counts.HIRED / max) * 100) },
    ];
  }, [applications]);

  const activeInterviews = schedule.filter((s) => s.status !== "Cancelled");

  return (
    <>
      <PageHead
        kicker={me?.role ? pretty(me.role) : "Session"}
        title={`${greeting()}. The record is live.`}
        lede="Enterprise talent operations console. A draft vacancy screens nobody. A stage moves only when a human writes it."
      />

      <ol className="path">
        <li>
          <Link to="/vacancies">Publish a rule</Link>
        </li>
        <li>
          <Link to="/people">Add a person</Link>
        </li>
        <li>
          <Link to="/pipeline">Open an application</Link>
        </li>
        <li>
          <Link to="/intake">Admit a file</Link>
        </li>
        <li>
          <Link to="/evidence">Review citations</Link>
        </li>
        <li>
          <Link to="/hired">Manage onboarding</Link>
        </li>
      </ol>

      {/* Main Metrics Bar */}
      <section className="metrics" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        <Link to="/vacancies">
          <span>Open vacancies</span>
          <b>{openJobs.length}</b>
          <em>{drafts.length} still draft</em>
        </Link>
        <Link to="/pipeline">
          <span>Active Pipeline</span>
          <b>{applications.length}</b>
          <em>{fresh.length} new intake</em>
        </Link>
        <Link to="/people">
          <span>Candidates</span>
          <b>{candidates.length}</b>
          <em>{unverified.length} unverified contact</em>
        </Link>
        <Link to="/hired">
          <span>Hired & Onboarding</span>
          <b style={{ color: "var(--aurora)" }}>{hiredEmployees.length}</b>
          <em>{hiredEmployees.filter((e) => e.onboarding_status === "Ready for Day 1").length} ready for Day 1</em>
        </Link>
      </section>

      {/* Analytics & Recruitment Funnel Velocity */}
      <section style={{ marginTop: "34px", padding: "20px", border: "1px solid var(--line)", background: "rgba(18, 20, 28, 0.72)", borderRadius: "6px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "10px", marginBottom: "14px" }}>
          <div>
            <h2 style={{ fontSize: "1.35rem" }}>Recruitment Velocity & Funnel Analytics</h2>
            <p style={{ color: "var(--paper-dim)", fontSize: "0.9rem", marginTop: "2px" }}>
              Dynamic conversion analytics calculated across {applications.length} live application files.
            </p>
          </div>
          <div style={{ display: "flex", gap: "16px", fontFamily: "var(--mono)", fontSize: "11px", color: "var(--paper-dim)" }}>
            <span>AVG TIME-TO-HIRE: <strong style={{ color: "var(--aurora)" }}>18.4 Days</strong></span>
            <span>OFFER ACCEPTANCE: <strong style={{ color: "var(--gold)" }}>94.2%</strong></span>
          </div>
        </div>

        <div className="funnel-container">
          {funnelStages.map((stage) => (
            <div key={stage.key} className="funnel-row">
              <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--paper-dim)" }}>
                {stage.label}
              </span>
              <div className="funnel-bar-bg">
                <div
                  className="funnel-bar-fill"
                  style={{
                    width: `${Math.max(stage.pct, 8)}%`,
                    background: stage.key === "HIRED" ? "var(--aurora)" : stage.key === "NEW" ? "var(--copper-2)" : "var(--copper)",
                  }}
                />
              </div>
              <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: "var(--paper)", textAlign: "right" }}>
                {stage.count} {stage.count === 1 ? "app" : "apps"}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* Live Interview Scheduling Console */}
      <section style={{ marginTop: "34px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "10px", marginBottom: "14px" }}>
          <div>
            <h2 style={{ fontSize: "1.35rem" }}>Today's Live Interview Schedule</h2>
            <p style={{ color: "var(--paper-dim)", fontSize: "0.9rem", marginTop: "2px" }}>
              Calendar loops coordinated with verified human panel members.
            </p>
          </div>
          <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            <Pill tone="tone-human">{activeInterviews.length} Loops Scheduled</Pill>
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => setShowScheduleModal(true)}
              style={{ padding: "6px 14px", fontSize: "0.85rem" }}
            >
              + Schedule Interview
            </button>
          </div>
        </div>

        {activeInterviews.length === 0 ? (
          <p className="empty" style={{ border: "1px dashed var(--line)", padding: "16px", borderRadius: "6px" }}>
            No interview loops active today. Use "+ Schedule Interview" to book a technical or executive round.
          </p>
        ) : (
          <div style={{ display: "grid", gap: "10px" }}>
            {activeInterviews.map((item) => (
              <div
                key={item.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "14px 18px",
                  background: "rgba(18, 20, 28, 0.72)",
                  border: "1px solid var(--line)",
                  borderRadius: "6px",
                  gap: "14px",
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: "var(--copper-2)", letterSpacing: "0.08em", whiteSpace: "nowrap" }}>
                    {item.time} ({item.date || "Today"})
                  </span>
                  <div>
                    <strong style={{ display: "block", color: "var(--paper)", fontSize: "0.98rem" }}>
                      {item.candidate_name} · {item.candidate_role}
                    </strong>
                    <span style={{ color: "var(--paper-dim)", fontSize: "0.85rem" }}>
                      {item.round_title} · Panel: <span style={{ color: "var(--paper)" }}>{item.interviewer}</span>
                    </span>
                  </div>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <Pill tone={item.status === "In Progress" ? "tone-open" : "tone-human"}>
                    {item.status}
                  </Pill>

                  {rescheduleItemId === item.id ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <input
                        type="text"
                        placeholder="e.g. 02:30 PM"
                        value={newRescheduleTime}
                        onChange={(e) => setNewRescheduleTime(e.target.value)}
                        style={{
                          background: "#0c0d12",
                          color: "var(--paper)",
                          border: "1px solid var(--line)",
                          padding: "4px 8px",
                          borderRadius: "4px",
                          fontSize: "0.85rem",
                          width: "110px",
                        }}
                      />
                      <button
                        className="btn btn-primary"
                        type="button"
                        style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                        disabled={!newRescheduleTime.trim()}
                        onClick={() => {
                          void rescheduleInterview(item.id, newRescheduleTime.trim());
                          setRescheduleItemId(null);
                          setNewRescheduleTime("");
                        }}
                      >
                        Save
                      </button>
                      <button
                        className="btn btn-ghost"
                        type="button"
                        style={{ padding: "4px 8px", fontSize: "0.8rem" }}
                        onClick={() => setRescheduleItemId(null)}
                      >
                        ✕
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        className="btn btn-ghost"
                        type="button"
                        style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                        onClick={() => {
                          setRescheduleItemId(item.id);
                          setNewRescheduleTime(item.time);
                        }}
                      >
                        Reschedule
                      </button>
                      <button
                        className="btn btn-ghost"
                        type="button"
                        style={{ padding: "4px 10px", fontSize: "0.8rem", color: "var(--rose)" }}
                        onClick={() => {
                          if (window.confirm(`Cancel interview for ${item.candidate_name}?`)) {
                            void cancelInterview(item.id, "Candidate requested schedule release.");
                          }
                        }}
                      >
                        Cancel
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Operational Waiting Queue & Recent Audit Ledger */}
      <div className="split" style={{ marginTop: "34px" }}>
        <section className="ledger">
          <h2 style={{ fontSize: "1.3rem" }}>Waiting on a person</h2>
          <ul className="wait">
            {fresh.slice(0, 4).map((application) => {
              const cand = candidates.find((c) => c.id === application.candidate_id);
              const job = jobs.find((j) => j.id === application.job_id);
              return (
                <li key={application.id}>
                  <Link to={`/pipeline?app=${application.id}`}>
                    <span>New Application · {cand?.display_name || "Applicant"}</span>
                    <p>Stage is still New for {job?.title}. Open the pipeline and make the screening decision yourself.</p>
                  </Link>
                </li>
              );
            })}
            {drafts.slice(0, 3).map((job) => (
              <li key={job.id}>
                <Link to={`/vacancies?id=${job.id}`}>
                  <span>Draft Vacancy · {job.req_code}</span>
                  <p>{job.title} is not law until a requirement version is published.</p>
                </Link>
              </li>
            ))}
            {fresh.length === 0 && drafts.length === 0 ? (
              <li>
                <p>Nothing is waiting. Open a vacancy, admit a person, or advance the pipeline.</p>
              </li>
            ) : null}
          </ul>
        </section>

        {/* Live Immutable Human Audit Trail */}
        <section className="ledger">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "8px" }}>
            <h2 style={{ fontSize: "1.3rem" }}>Talent Operations Audit Log</h2>
            <Pill tone="tone-clean">Immutable Record</Pill>
          </div>
          <p style={{ color: "var(--paper-dim)", fontSize: "0.88rem", marginBottom: "12px" }}>
            Every requisition publish, screening verdict, interview booking, and stage move is recorded under actor identity.
          </p>
          <ul className="notes">
            {auditLogs.slice(0, 6).map((log) => (
              <li key={log.id} style={{ padding: "10px 0", borderTop: "1px solid var(--line)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--copper-2)" }}>
                    {log.action} · <strong style={{ color: "var(--paper)" }}>{log.target_name}</strong>
                  </span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--paper-dim)" }}>
                    {when(log.timestamp)}
                  </span>
                </div>
                <p style={{ fontSize: "0.88rem", color: "var(--paper-dim)", marginTop: "3px", lineHeight: "1.4" }}>
                  {log.details}
                </p>
                <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--aurora)" }}>
                  Actor: {log.actor}
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* Demo State Reset Button */}
      <div style={{ marginTop: "40px", paddingTop: "20px", borderTop: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--paper-dim)", margin: 0 }}>
          TALVORA TALENT OPERATIONS ENGINE · LOCAL PERSISTENCE ACTIVE
        </p>
        <button
          className="btn btn-ghost"
          type="button"
          onClick={() => {
            if (window.confirm("Reset all recruitment operations data back to baseline demo state?")) {
              resetDemoData();
            }
          }}
          style={{ fontSize: "0.8rem", padding: "6px 14px" }}
        >
          Reset Baseline Demo Data
        </button>
      </div>

      {/* Schedule Interview Modal Dialog */}
      {showScheduleModal ? (
        <div className="modal-overlay" onClick={() => setShowScheduleModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <p className="kicker">Live Scheduling Loop</p>
            <h2 style={{ marginBottom: "6px" }}>Book Technical or Panel Interview</h2>
            <p style={{ color: "var(--paper-dim)", fontSize: "0.9rem", marginBottom: "18px" }}>
              Coordinate availability across candidate and internal engineering panel interviewers.
            </p>

            <form
              className="field"
              onSubmit={(e) => {
                e.preventDefault();
                const cand = candidates.find((c) => c.id === scheduleCandidateId);
                const interviewer = interviewers.find((i) => i.id === scheduleInterviewerId);
                if (!cand || !interviewer) return;

                setBusy(true);
                void scheduleInterview({
                  candidate_id: cand.id,
                  candidate_name: cand.display_name || "Candidate",
                  candidate_role: cand.current_title,
                  interviewer: `${interviewer.name} (${interviewer.role})`,
                  round_title: scheduleRound,
                  time: scheduleTime,
                  date: scheduleDate,
                }).finally(() => {
                  setBusy(false);
                  setShowScheduleModal(false);
                });
              }}
            >
              <label>
                Candidate
                <select
                  required
                  value={scheduleCandidateId}
                  onChange={(e) => setScheduleCandidateId(e.target.value)}
                >
                  <option value="">Select Candidate...</option>
                  {candidates.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.display_name} ({c.current_title} @ {c.current_company})
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Interviewer Panel Lead (Verified Availability)
                <select
                  required
                  value={scheduleInterviewerId}
                  onChange={(e) => setScheduleInterviewerId(e.target.value)}
                >
                  <option value="">Select Interviewer...</option>
                  {interviewers.map((i) => (
                    <option key={i.id} value={i.id}>
                      {i.name} — {i.role} [{i.availability}]
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Interview Round Title
                <input
                  required
                  value={scheduleRound}
                  onChange={(e) => setScheduleRound(e.target.value)}
                  placeholder="e.g. Round 3: System Design & Raft Consensus"
                />
              </label>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <label>
                  Scheduled Time
                  <input
                    required
                    value={scheduleTime}
                    onChange={(e) => setScheduleTime(e.target.value)}
                    placeholder="e.g. 11:30 AM"
                  />
                </label>
                <label>
                  Date
                  <input
                    required
                    value={scheduleDate}
                    onChange={(e) => setScheduleDate(e.target.value)}
                    placeholder="e.g. Today / March 12"
                  />
                </label>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "16px" }}>
                <button className="btn btn-ghost" type="button" onClick={() => setShowScheduleModal(false)}>
                  Cancel
                </button>
                <button
                  className="btn btn-primary"
                  type="submit"
                  disabled={busy || !scheduleCandidateId || !scheduleInterviewerId}
                >
                  Confirm & Send Calendar Loop
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
