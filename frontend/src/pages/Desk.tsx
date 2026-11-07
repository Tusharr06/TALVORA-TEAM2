import { Link } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { greeting, pretty } from "../format";
import { useWorkspace } from "../state";

export function Desk() {
  const { me, jobs, candidates, applications, hiredEmployees, schedule } = useWorkspace();
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const drafts = jobs.filter((job) => job.status === "DRAFT");
  const fresh = applications.filter((application) => application.stage === "NEW");
  const unverified = candidates.filter((candidate) => !candidate.is_draft && !candidate.is_contact_verified);

  return (
    <>
      <PageHead
        kicker={me?.role ? pretty(me.role) : "Session"}
        title={`${greeting()}. The record is live.`}
        lede="Enterprise talent operations console. A draft vacancy screens nobody. A stage moves only when you write it."
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
          <Link to="/hired">Manage onboarding</Link>
        </li>
      </ol>
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

      {/* Today's Live Interview Loops */}
      {schedule && schedule.length > 0 ? (
        <section style={{ marginTop: "34px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "12px" }}>
            <h2 style={{ fontSize: "1.4rem" }}>Today's Live Interview Schedule</h2>
            <Pill tone="tone-human">4 Loops Active</Pill>
          </div>
          <div style={{ display: "grid", gap: "10px" }}>
            {schedule.map((item) => (
              <div
                key={item.id}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "14px 16px",
                  background: "rgba(18, 20, 28, 0.72)",
                  border: "1px solid var(--line)",
                  borderRadius: "6px",
                  gap: "12px",
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: "var(--copper-2)", letterSpacing: "0.08em" }}>
                    {item.time}
                  </span>
                  <div>
                    <strong style={{ display: "block", color: "var(--paper)", fontSize: "0.98rem" }}>
                      {item.candidate_name} · {item.candidate_role}
                    </strong>
                    <span style={{ color: "var(--paper-dim)", fontSize: "0.85rem" }}>
                      {item.round_title} · Panel: {item.interviewer}
                    </span>
                  </div>
                </div>
                <Pill tone={item.tone === "interviewing" ? "tone-open" : "tone-human"}>
                  {item.status}
                </Pill>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="ledger">
        <h2>Waiting on a person</h2>
        <ul className="wait">
          {fresh.slice(0, 5).map((application) => (
            <li key={application.id}>
              <Link to={`/pipeline?app=${application.id}`}>
                <span>New application</span>
                <p>Stage is still New. Open the pipeline and write the next one yourself.</p>
              </Link>
            </li>
          ))}
          {drafts.slice(0, 3).map((job) => (
            <li key={job.id}>
              <Link to={`/vacancies?id=${job.id}`}>
                <span>Draft vacancy</span>
                <p>{job.title} is not law until a requirement version is published.</p>
              </Link>
            </li>
          ))}
          {fresh.length === 0 && drafts.length === 0 ? (
            <li>
              <p>Nothing is waiting. Open a vacancy, add a person, or leave the pipeline alone.</p>
            </li>
          ) : null}
        </ul>
      </section>
    </>
  );
}
