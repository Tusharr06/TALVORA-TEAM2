import { NavLink, Outlet } from "react-router-dom";
import { actor } from "../data";
import { pretty } from "../format";
import { useWorkspace } from "../state";
import type { Role } from "../format";
import { VoraRing } from "./VoraRing";

const LINKS = [
  ["Desk", "/desk"],
  ["Vacancies", "/vacancies"],
  ["Pipeline", "/pipeline"],
  ["People", "/people"],
  ["Intake", "/intake"],
  ["Evidence", "/evidence"],
] as const;

const ROLES: Role[] = ["RECRUITER", "HIRING_MANAGER", "INTERVIEWER", "ADMIN", "OWNER"];

export function Shell() {
  const { role, setRole, notice, dismissNotice, reset } = useWorkspace();

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
          <p className="who">
            {actor.name}
            <span>
              {actor.org} · {actor.team}
            </span>
          </p>
          <label className="role">
            Preview as
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
              aria-label="Preview the desk as a role"
            >
              {ROLES.map((item) => (
                <option key={item} value={item}>
                  {pretty(item)}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-ghost" type="button" onClick={reset}>
            Reset desk
          </button>
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
        <Outlet />
        <p className="colophon">
          Operator preview in this browser. The FastAPI service listens on localhost port 8000.
        </p>
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
