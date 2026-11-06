import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { pretty, when } from "../format";
import { useWorkspace, type RequirementVersion } from "../state";

export function Vacancies() {
  const { jobs, can, createJob, loadVersions } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const selected = jobs.find((job) => job.id === params.get("id")) ?? jobs[0] ?? null;
  const [versions, setVersions] = useState<RequirementVersion[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [department, setDepartment] = useState("");
  const [location, setLocation] = useState("");
  const [skill, setSkill] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!selected) {
      setVersions([]);
      return;
    }
    let cancelled = false;
    void loadVersions(selected.id)
      .then((rows) => {
        if (!cancelled) setVersions(rows);
      })
      .catch(() => {
        if (!cancelled) setVersions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [loadVersions, selected]);

  async function submit(publish: boolean) {
    setBusy(true);
    try {
      await createJob({ title, description, department, location, skill, publish });
      setTitle("");
      setDescription("");
      setSkill("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead
        kicker="Vacancies"
        title="A rule is a version."
        lede="Drafts screen nobody. Publishing snapshots the criteria and opens the vacancy. Later edits publish again. They do not rewrite the old version."
      />
      <div className="split">
        <div className="menu">
          {jobs.map((job) => (
            <button
              key={job.id}
              aria-pressed={selected?.id === job.id}
              type="button"
              onClick={() => setParams({ id: job.id })}
            >
              <strong>{job.title}</strong>
              <span>
                {pretty(job.status)}
                {job.current_published_version_number
                  ? ` · v${job.current_published_version_number}`
                  : " · unpublished"}
              </span>
            </button>
          ))}
          {jobs.length === 0 ? <p>No vacancies yet.</p> : null}
        </div>
        <article className="inspector">
          {selected ? (
            <>
              <p className="kicker">{pretty(selected.status)}</p>
              <h2>{selected.title}</h2>
              <p>{selected.description}</p>
              <dl className="facts">
                <div>
                  <dt>Department</dt>
                  <dd>{selected.department || "—"}</dd>
                </div>
                <div>
                  <dt>Location</dt>
                  <dd>{selected.location || "—"}</dd>
                </div>
                <div>
                  <dt>Employment</dt>
                  <dd>{pretty(selected.employment_type)}</dd>
                </div>
                <div>
                  <dt>Record version</dt>
                  <dd>{selected.version}</dd>
                </div>
              </dl>
              <h3>Published versions</h3>
              {versions.length === 0 ? <p>No published version. This vacancy is not law.</p> : null}
              <ol className="timeline">
                {versions.map((version) => (
                  <li key={version.id}>
                    <span className="dot" />
                    <div>
                      <strong>
                        Version {version.version_number}
                        <Pill>{when(version.published_at)}</Pill>
                      </strong>
                      <p>{version.change_summary || version.title}</p>
                      <ul>
                        {version.criteria.map((criterion) => (
                          <li key={criterion.id}>
                            {criterion.configuration.skill_name || pretty(criterion.criterion_type)} —{" "}
                            {criterion.source_requirement_text}
                          </li>
                        ))}
                      </ul>
                    </div>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p>Create the first vacancy. It stays a draft until you publish a skill.</p>
          )}
          {can("CREATE_JOB") ? (
            <form
              className="field"
              onSubmit={(event) => {
                event.preventDefault();
                void submit(false);
              }}
            >
              <h3>New vacancy</h3>
              <input
                required
                placeholder="Title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <textarea
                required
                placeholder="What this role is for"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
              <input
                placeholder="Department"
                value={department}
                onChange={(event) => setDepartment(event.target.value)}
              />
              <input
                placeholder="Location"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
              />
              <input
                placeholder="Skill the published rule will require"
                value={skill}
                onChange={(event) => setSkill(event.target.value)}
              />
              <div className="row">
                <button className="btn" type="submit" disabled={busy}>
                  Save draft
                </button>
                {can("PUBLISH_JOB_REQUIREMENTS") ? (
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={busy}
                    onClick={() => void submit(true)}
                  >
                    Publish and open
                  </button>
                ) : null}
              </div>
            </form>
          ) : (
            <p>This session cannot create vacancies.</p>
          )}
        </article>
      </div>
    </>
  );
}
