import { NavLink, Outlet } from "react-router-dom";
import { pretty } from "../format";
import { useWorkspace } from "../state";
import { VoraRing } from "./VoraRing";

const LINKS = [
  ["Desk", "/desk"],
  ["Vacancies", "/vacancies"],
  ["Pipeline", "/pipeline"],
  ["People", "/people"],
  ["Intake", "/intake"],
  ["Evidence", "/evidence"],
  ["Hired", "/hired"],
] as const;

export function Shell() {
  const { status, me, notice, error, dismissNotice, signIn, signOut, refresh } = useWorkspace();

  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink to="/" className="brand">
          <VoraRing variant="mark" />
          <span>Talvora</span>
        </NavLink>
        <p className="law">Models may read. They may not move.</p>
        <div className="top-actions">
          {me ? (
            <p className="who">
              {me.email}
              <span>{me.role ? pretty(me.role) : "No organization yet"}</span>
            </p>
          ) : (
            <p className="who">
              Signed out
              <span>Session required</span>
            </p>
          )}
          {me ? (
            <button className="btn btn-ghost" type="button" onClick={() => void signOut()}>
              Sign out
            </button>
          ) : (
            <button className="btn btn-primary" type="button" onClick={() => void signIn()}>
              Sign in
            </button>
          )}
        </div>
      </header>
      <nav className="nav" aria-label="Desk">
        {LINKS.map(([label, to], index) => (
          <NavLink key={to} to={to}>
            <span className="idx">{String(index + 1).padStart(2, "0")}</span>
            {label}
          </NavLink>
        ))}
      </nav>
      <main id="main" className="main">
        {error && status !== "down" ? <p className="banner">{error}</p> : null}
        {status === "loading" ? <p className="lede">Opening the session…</p> : null}
        {status === "signed-out" ? (
          <section className="session-gate">
            <p className="kicker">Session</p>
            <h1>The desk keeps a human session.</h1>
            <p className="lede">
              Sign in through the local identity bridge. Talvora will open an organization for you
              if you do not have one, then the vacancy, pipeline, and intake routes are the live API.
            </p>
            <button className="btn btn-primary" type="button" onClick={() => void signIn()}>
              Sign in
            </button>
          </section>
        ) : null}
        {status === "down" ? (
          <section className="session-gate">
            <p className="kicker">API</p>
            <h1>{error ? "The desk could not open the record." : "The service is not answering."}</h1>
            <p className="lede">
              {error ??
                "Start Postgres, apply migrations, and run the API on port 8000. This desk talks to it through the same origin."}
            </p>
            <button className="btn btn-primary" type="button" onClick={() => void refresh()}>
              Try again
            </button>
          </section>
        ) : null}
        {status === "ready" ? <Outlet /> : null}
        <p className="colophon">Live session. API proxied from this origin to port 8000.</p>
      </main>
      {notice ? (
        <div className="toast" role="status">
          <p>{notice}</p>
          <button className="btn" type="button" onClick={dismissNotice}>
            Close
          </button>
        </div>
      ) : null}
    </div>
  );
}
