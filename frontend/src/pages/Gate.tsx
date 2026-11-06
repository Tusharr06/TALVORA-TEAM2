import { Link } from "react-router-dom";
import { VoraRing } from "../components/VoraRing";
import { useWorkspace } from "../state";

const TAPE =
  "Quarantine · ClamAV · Clean bucket · Page-level citation · Versioned criteria · Human stage · Reason to reject · No protected traits · Opaque storage keys · ";

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
      <section className="chambers" aria-label="How a hire is proven">
        <Link to="/intake">
          <span>01</span>
          <strong>Quarantine the file</strong>
          <p>Every resume is untrusted until the scanner says clean. The storage key never contains a name.</p>
        </Link>
        <Link to="/vacancies">
          <span>02</span>
          <strong>Version the rule</strong>
          <p>Eligibility is a published version. Change the bar and you publish again. You do not edit history.</p>
        </Link>
        <Link to="/pipeline">
          <span>03</span>
          <strong>Write the stage</strong>
          <p>New, review, interview, decision. Rejection needs a reason. A model is not on this list.</p>
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
