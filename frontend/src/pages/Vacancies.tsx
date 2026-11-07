import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { pretty, when } from "../format";
import { useWorkspace, type RequirementVersion } from "../state";

export function Vacancies() {
  const { jobs, can, createJob, updateJob, publishJob, closeJob, reopenJob, loadVersions } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const selected = jobs.find((job) => job.id === params.get("id")) ?? jobs[0] ?? null;
  const [versions, setVersions] = useState<RequirementVersion[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [department, setDepartment] = useState("");
  const [location, setLocation] = useState("");
  const [skill, setSkill] = useState("");
  const [busy, setBusy] = useState(false);

  // Edit State for Selected Job
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editDept, setEditDept] = useState("");
  const [editLoc, setEditLoc] = useState("");
  const [editSalary, setEditSalary] = useState("");

  // Publish New Criteria Version Modal/Form
  const [showPublishVersion, setShowPublishVersion] = useState(false);
  const [versionSkill, setVersionSkill] = useState("");
  const [versionReq, setVersionReq] = useState("");

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

    // Populate edit buffers
    setEditTitle(selected.title);
    setEditDesc(selected.description);
    setEditDept(selected.department || "");
    setEditLoc(selected.location || "");
    setEditSalary(selected.salary_range || "");
    setIsEditing(false);
    setShowPublishVersion(false);

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
      setDepartment("");
      setLocation("");
      setSkill("");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveEdit() {
    if (!selected) return;
    setBusy(true);
    try {
      await updateJob(selected.id, {
        title: editTitle,
        description: editDesc,
        department: editDept,
        location: editLoc,
        salary_range: editSalary,
      });
      setIsEditing(false);
    } finally {
      setBusy(false);
    }
  }

  async function handlePublishNewVersion() {
    if (!selected) return;
    setBusy(true);
    try {
      await publishJob(selected.id, versionSkill, versionReq);
      setShowPublishVersion(false);
      setVersionSkill("");
      setVersionReq("");
      const rows = await loadVersions(selected.id);
      setVersions(rows);
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
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "8px" }}>
                <p className="kicker">{pretty(selected.status)} · {selected.req_code}</p>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                    onClick={() => setIsEditing(!isEditing)}
                  >
                    {isEditing ? "Cancel Edit" : "Edit Requisition"}
                  </button>

                  {selected.status === "OPEN" ? (
                    <button
                      className="btn btn-ghost"
                      type="button"
                      style={{ padding: "4px 10px", fontSize: "0.8rem", color: "var(--rose)" }}
                      onClick={() => {
                        if (window.confirm(`Close requisition ${selected.title}?`)) {
                          void closeJob(selected.id, "Position fulfilled or paused.");
                        }
                      }}
                    >
                      Close Vacancy
                    </button>
                  ) : null}

                  {selected.status === "CLOSED" ? (
                    <button
                      className="btn btn-primary"
                      type="button"
                      style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                      onClick={() => void reopenJob(selected.id)}
                    >
                      Reopen Vacancy
                    </button>
                  ) : null}

                  {selected.status === "DRAFT" || selected.status === "OPEN" ? (
                    <button
                      className="btn btn-primary"
                      type="button"
                      style={{ padding: "4px 10px", fontSize: "0.8rem" }}
                      onClick={() => setShowPublishVersion(!showPublishVersion)}
                    >
                      {selected.current_published_version_number ? "+ Publish Version" : "Publish & Open"}
                    </button>
                  ) : null}
                </div>
              </div>

              {isEditing ? (
                <div style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--line)", padding: "16px", borderRadius: "6px", margin: "14px 0" }}>
                  <h3 style={{ marginTop: 0 }}>Edit Requisition Parameters</h3>
                  <div style={{ display: "grid", gap: "10px" }}>
                    <label className="field" style={{ margin: 0 }}>
                      Title
                      <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
                    </label>
                    <label className="field" style={{ margin: 0 }}>
                      Description
                      <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} />
                    </label>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                      <label className="field" style={{ margin: 0 }}>
                        Department
                        <input value={editDept} onChange={(e) => setEditDept(e.target.value)} />
                      </label>
                      <label className="field" style={{ margin: 0 }}>
                        Location
                        <input value={editLoc} onChange={(e) => setEditLoc(e.target.value)} />
                      </label>
                    </div>
                    <label className="field" style={{ margin: 0 }}>
                      Compensation Band
                      <input value={editSalary} onChange={(e) => setEditSalary(e.target.value)} />
                    </label>
                    <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "8px" }}>
                      <button className="btn btn-ghost" type="button" onClick={() => setIsEditing(false)}>
                        Cancel
                      </button>
                      <button className="btn btn-primary" type="button" disabled={busy} onClick={() => void handleSaveEdit()}>
                        Save Changes
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <>
                  <h2>{selected.title}</h2>
                  <p style={{ marginTop: "6px", lineHeight: "1.5" }}>{selected.description}</p>
                </>
              )}

              {/* Publish New Criteria Version Form */}
              {showPublishVersion ? (
                <div style={{ background: "rgba(227, 138, 69, 0.06)", border: "1px solid var(--copper)", padding: "16px", borderRadius: "6px", margin: "16px 0" }}>
                  <h3 style={{ marginTop: 0, color: "var(--copper-2)" }}>
                    Publish New Criteria Snapshot v{(selected.current_published_version_number || 0) + 1}
                  </h3>
                  <p style={{ fontSize: "0.85rem", color: "var(--paper-dim)", marginBottom: "12px" }}>
                    Publishing seals this requirement set into governance. Existing candidate citations evaluate against this snapshot.
                  </p>
                  <div style={{ display: "grid", gap: "10px" }}>
                    <input
                      required
                      placeholder="Core Skill or Specialization (e.g. Distributed Consensus & Raft)"
                      value={versionSkill}
                      onChange={(e) => setVersionSkill(e.target.value)}
                      style={{ background: "#0c0d12", color: "var(--paper)", border: "1px solid var(--line)", padding: "8px 10px", borderRadius: "4px" }}
                    />
                    <textarea
                      required
                      placeholder="Source requirement standard text (e.g. Minimum 5 years high-concurrency systems experience with monotonic leasing)."
                      value={versionReq}
                      onChange={(e) => setVersionReq(e.target.value)}
                      style={{ background: "#0c0d12", color: "var(--paper)", border: "1px solid var(--line)", padding: "8px 10px", borderRadius: "4px", minHeight: "70px" }}
                    />
                    <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                      <button className="btn btn-ghost" type="button" onClick={() => setShowPublishVersion(false)}>
                        Cancel
                      </button>
                      <button
                        className="btn btn-primary"
                        type="button"
                        disabled={busy || !versionSkill.trim() || !versionReq.trim()}
                        onClick={() => void handlePublishNewVersion()}
                      >
                        Publish & Snapshot Criteria
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

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
                  <dt>Hiring Manager</dt>
                  <dd>{selected.hiring_manager}</dd>
                </div>
                <div>
                  <dt>Compensation Band</dt>
                  <dd style={{ color: "var(--aurora)", fontFamily: "var(--mono)" }}>{selected.salary_range}</dd>
                </div>
                <div>
                  <dt>Employment</dt>
                  <dd>{pretty(selected.employment_type)}</dd>
                </div>
                <div>
                  <dt>Record Version</dt>
                  <dd>v{selected.version}</dd>
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
                        <Pill tone="tone-open">{when(version.published_at)}</Pill>
                      </strong>
                      <p>{version.change_summary || version.title}</p>
                      <ul>
                        {version.criteria.map((criterion) => (
                          <li key={criterion.id}>
                            <strong style={{ color: "var(--paper)" }}>
                              {criterion.configuration.skill_name || pretty(criterion.criterion_type)}
                            </strong>{" "}
                            — {criterion.source_requirement_text}
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
              style={{ marginTop: "28px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}
              onSubmit={(event) => {
                event.preventDefault();
                void submit(false);
              }}
            >
              <h3 style={{ marginTop: 0 }}>New vacancy</h3>
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
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
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
              </div>
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
