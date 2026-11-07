import { Link } from "react-router-dom";
import { VoraRing } from "../components/VoraRing";
import { useWorkspace } from "../state";

const TAPE =
  "Quarantine · ClamAV · Clean bucket · Page-level citation · Versioned criteria · Human stage · Reason to reject · No protected traits · Opaque storage keys · ";

const HARNESS = [
  ["Trusted context", "A vacancy screens people only after a requirement version is published."],
  ["Typed contract", "Every write is a validated API command, with a version so two people cannot overwrite each other."],
  ["Guardrail", "Files enter quarantine. Protected traits are not hiring criteria. Storage keys carry no names."],
  ["Accountability", "Reject, withdraw, and reopen require a reason. The actor is a person, recorded on the stage."],
] as const;

export function Gate() {
  const { status, error, signIn } = useWorkspace();

  return (
    <div className="gate">
      <header className="gate-top">
        <Link to="/desk" className="brand">
          <VoraRing variant="mark" />
          <span>Talvora</span>
        </Link>
        <p>Intelligent talent operations</p>
      </header>
      <section className="gate-hero">
        <div className="gate-copy">
          <p className="kicker">Vol. 01 · The desk</p>
          <h1>
            Evidence first.
            <em> Humans decide.</em>
          </h1>
          <p className="lede">
            Resumes enter quarantine. Hiring rules are published versions. A stage changes only when
            a person writes it down. Talvora will show you the sentence, the page, and the name —
            and it will not let a model move the person.
          </p>
          {error ? <p className="banner">{error}</p> : null}
          <div className="gate-actions">
            {status === "ready" ? (
              <Link className="btn btn-primary" to="/desk">
                Enter the desk
              </Link>
            ) : (
              <button
                className="btn btn-primary"
                type="button"
                disabled={status === "loading"}
                onClick={() => void signIn()}
              >
                {status === "loading" ? "Checking the session…" : "Sign in to the desk"}
              </button>
            )}
            <Link className="btn btn-ghost" to="/evidence">
              Open a citation
            </Link>
          </div>
        </div>
        <VoraRing />
      </section>

      <section className="harness" aria-label="The engineering harness">
        {HARNESS.map(([title, body], index) => (
          <article key={title}>
            <span>0{index + 1}</span>
            <strong>{title}</strong>
            <p>{body}</p>
          </article>
        ))}
      </section>

      <section className="verdict" aria-label="What the model is allowed to do">
        <div>
          <p className="kicker">Allowed to read</p>
          <ul>
            <li>The published requirement version</li>
            <li>Text extracted from a clean document</li>
            <li>The stage history a person already wrote</li>
          </ul>
        </div>
        <div>
          <p className="kicker">Not allowed to do</p>
          <ul>
            <li>Move, reject, or hire a candidate</li>
            <li>Rewrite a citation under a quote</li>
            <li>Merge two people because the emails look alike</li>
          </ul>
        </div>
      </section>

      <section className="chambers" aria-label="The recruiting workflow">
        <Link to="/vacancies">
          <span>01 · Criteria</span>
          <strong>Publish the minimum</strong>
          <p>A draft screens nobody. Publish the skill, and that version becomes the bar.</p>
        </Link>
        <Link to="/intake">
          <span>02 · Evidence</span>
          <strong>Quarantine the file</strong>
          <p>A resume is untrusted until the scanner says clean. The key never contains a name.</p>
        </Link>
        <Link to="/evidence">
          <span>03 · Citation</span>
          <strong>Quote the page</strong>
          <p>Source text appears only after extraction. An unfinished run is not a history.</p>
        </Link>
        <Link to="/pipeline">
          <span>04 · Decision</span>
          <strong>Write the stage</strong>
          <p>New, review, interview, decision. A rejection needs a reason. A model is not on this list.</p>
        </Link>
      </section>
      <div className="ticker" aria-hidden="true">
        <div className="ticker-track">
          <span>{TAPE}</span>
          <span>{TAPE}</span>
        </div>
      </div>
    </div>
  );
}
