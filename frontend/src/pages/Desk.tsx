import { Link } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { duplicatePairs, jobs, runs } from "../data";
import { ACTIVE_STAGES, greeting, pretty, type Stage } from "../format";
import { useWorkspace } from "../state";

export function Desk() {
  const { applications, candidates, intake, reviewedRuns, dismissedDupes, role } = useWorkspace();
  const moving = applications.filter((item) => ACTIVE_STAGES.includes(item.stage));
  const openJobs = jobs.filter((job) => job.status === "OPEN");
  const inAirlock = intake.filter(
    (item) => item.status === "RESERVED" || item.status === "QUARANTINED" || item.status === "SCANNING",
  );
  const reviewRun = runs.find((run) => run.status === "NEEDS_REVIEW" && !reviewedRuns.includes(run.id));
  const quote = runs[0]?.fragments[0];
  const segment = runs[0]?.segments.find((item) => item.id === quote?.segmentId);

  const waiting: { href: string; kicker: string; text: string }[] = [];
  for (const app of applications) {
    if (!app.attention || !ACTIVE_STAGES.includes(app.stage)) continue;
    const person = candidates.find((item) => item.id === app.candidateId);
    waiting.push({
      href: `/pipeline?app=${app.id}`,
      kicker: person?.name ?? app.serial,
      text: app.attention,
    });
  }
  for (const person of candidates) {
    const latest = person.contacts[person.contacts.length - 1];
    if (!latest) continue;
    const verified = person.verification?.revision === latest.version;
    const active = applications.some(
      (item) => item.candidateId === person.id && ACTIVE_STAGES.includes(item.stage),
    );
    if (!verified && active && latest.source === "RESUME_EXTRACTED_UNVERIFIED") {
      waiting.push({
        href: `/people?id=${person.id}`,
        kicker: person.name,
        text: "Email was read off a resume and has not been attested. Scheduling should not trust it yet.",
      });
    }
  }
  if (inAirlock.length) {
    waiting.push({
      href: "/intake",
      kicker: "Quarantine",
      text: `${inAirlock.length} file${inAirlock.length === 1 ? "" : "s"} still between the door and a clean bucket.`,
    });
  }
  if (reviewRun) {
    const person = candidates.find((item) => item.id === reviewRun.candidateId);
    waiting.push({
      href: `/evidence?run=${reviewRun.id}`,
      kicker: person?.name ?? "Extraction",
      text: reviewRun.warning ?? "This read needs a person.",
    });
  }
  for (const pair of duplicatePairs) {
    if (dismissedDupes.includes(pair.id)) continue;
    waiting.push({
      href: `/people?id=${pair.a}`,
      kicker: "Lookalikes",
      text: pair.reason,
    });
  }

  const ledger = applications
    .flatMap((app) =>
      app.history.map((event) => ({
        ...event,
        serial: app.serial,
        name: candidates.find((item) => item.id === app.candidateId)?.name ?? app.serial,
      })),
    )
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 6);

  return (
    <div className="desk">
      <PageHead
        kicker={`${greeting()} · ${pretty(role)}`}
        title="The desk is holding still."
        lede={`${moving.length} applications are in motion across ${openJobs.length} open vacancies. Nothing in that count will change unless someone with the capability writes it.`}
      />
      <section className="metrics" aria-label="Desk signals">
        <Link to="/vacancies">
          <span>Open vacancies</span>
          <b>{openJobs.length}</b>
          <em>Published rules only</em>
        </Link>
        <Link to="/pipeline">
          <span>In motion</span>
          <b>{moving.length}</b>
          <em>Hired stays hired</em>
        </Link>
        <Link to="/intake">
          <span>In the airlock</span>
          <b>{inAirlock.length}</b>
          <em>Not extractable yet</em>
        </Link>
        <Link to={reviewRun ? `/evidence?run=${reviewRun.id}` : "/evidence"}>
          <span>Reads to judge</span>
          <b>{reviewRun ? 1 : 0}</b>
          <em>Low-confidence OCR</em>
        </Link>
      </section>

      <div className="desk-grid">
        <section>
          <h2>Waiting on a person</h2>
          <ul className="wait">
            {waiting.map((item) => (
              <li key={item.href + item.kicker}>
                <Link to={item.href}>
                  <span>{item.kicker}</span>
                  <p>{item.text}</p>
                </Link>
              </li>
            ))}
          </ul>
        </section>
        <section className="desk-side">
          {quote && segment ? (
            <blockquote className="pull">
              <p>“{quote.quote}”</p>
              <footer>
                <Link to="/evidence?run=run-adeel">Adeel Rahman · {segment.locator}</Link>
                <span className="hash">{quote.hash}</span>
              </footer>
            </blockquote>
          ) : null}
          <h2>Current</h2>
          <div className="current">
            {ACTIVE_STAGES.map((stage) => (
              <Link key={stage} to="/pipeline">
                <span>{pretty(stage)}</span>
                <b>{count(applications, stage)}</b>
              </Link>
            ))}
          </div>
        </section>
      </div>

      <section className="ledger">
        <h2>Latest stage writes</h2>
        <ol>
          {ledger.map((event) => (
            <li key={event.id}>
              <div>
                <strong>{event.name}</strong>
                <span className="hash">{event.serial}</span>
              </div>
              <p>
                {event.from ? (
                  <>
                    <Pill tone={event.from.toLowerCase()}>{pretty(event.from)}</Pill>
                    <span aria-hidden="true"> → </span>
                  </>
                ) : (
                  <span>Opened at </span>
                )}
                <Pill tone={event.to.toLowerCase()}>{pretty(event.to)}</Pill>
              </p>
              <p className="muted">
                {event.actor}
                {event.reason ? ` · ${event.reason}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function count(applications: { stage: Stage }[], stage: Stage): number {
  return applications.filter((item) => item.stage === stage).length;
}
