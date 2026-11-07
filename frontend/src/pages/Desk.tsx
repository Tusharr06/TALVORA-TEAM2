import { Link } from "react-router-dom";
import { PageHead } from "../components/Bits";
import { greeting, pretty } from "../format";
import { useWorkspace } from "../state";

export function Desk() {
  const { me, jobs, candidates, applications } = useWorkspace();
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const drafts = jobs.filter((job) => job.status === "DRAFT");
  const fresh = applications.filter((application) => application.stage === "NEW");
  const unverified = candidates.filter((candidate) => !candidate.is_draft && !candidate.is_contact_verified);

  return (
    <>
      <PageHead
        kicker={me?.role ? pretty(me.role) : "Session"}
        title={`${greeting()}. The record is live.`}
        lede="Counts below come from the API for this organization. A draft vacancy screens nobody. A stage moves only when you write it."
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
      </ol>
      <section className="metrics">
        <Link to="/vacancies">
          <span>Open vacancies</span>
          <b>{openJobs.length}</b>
          <em>{drafts.length} still draft</em>
        </Link>
        <Link to="/pipeline">
          <span>Applications</span>
          <b>{applications.length}</b>
          <em>{fresh.length} still new</em>
        </Link>
        <Link to="/people">
          <span>People</span>
          <b>{candidates.length}</b>
          <em>{unverified.length} contact unverified</em>
        </Link>
        <Link to="/intake">
          <span>Intake</span>
          <b>{openJobs.length}</b>
          <em>open jobs can take a file</em>
        </Link>
      </section>
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
