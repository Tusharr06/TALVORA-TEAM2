import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHead, Pill } from "../components/Bits";
import { useWorkspace } from "../state";

export function Hired() {
  const { hiredEmployees, toggleChecklistItem } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");

  const selectedId = params.get("emp") || hiredEmployees[0]?.id || "";
  const selected = hiredEmployees.find((e) => e.id === selectedId) || hiredEmployees[0] || null;

  const filteredEmployees = useMemo(() => {
    return hiredEmployees.filter((emp) => {
      if (statusFilter !== "all" && emp.onboarding_status !== statusFilter) return false;
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchName = emp.display_name.toLowerCase().includes(q);
        const matchRole = emp.role_title.toLowerCase().includes(q);
        const matchDept = emp.department.toLowerCase().includes(q);
        const matchCode = emp.employee_code.toLowerCase().includes(q);
        if (!matchName && !matchRole && !matchDept && !matchCode) return false;
      }
      return true;
    });
  }, [hiredEmployees, statusFilter, searchQuery]);

  const totalHired = hiredEmployees.length;
  const readyCount = hiredEmployees.filter((e) => e.onboarding_status === "Ready for Day 1").length;
  const provisioningCount = hiredEmployees.filter((e) => e.onboarding_status === "Provisioning").length;

  const totalTasks = selected?.checklist.length || 0;
  const completedTasks = selected?.checklist.filter((t) => t.completed).length || 0;
  const progressPercent = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

  const categories = ["Compliance & Legal", "IT & Security", "People & Team"] as const;

  return (
    <>
      <PageHead
        kicker="07 Hired & Onboarding"
        title="A hire is a beginning."
        lede="The pipeline has concluded; the employee record begins. Manage equipment provisioning, security clearances, and Day 1 welcome checklists."
      />

      {/* Talent Acquisition Onboarding Metrics */}
      <section className="metrics" style={{ marginBottom: "28px" }}>
        <div className="metric-box">
          <span>Total Recruited Hires</span>
          <b style={{ color: "var(--paper)" }}>{totalHired}</b>
          <em>Corporate records active</em>
        </div>
        <div className="metric-box">
          <span>Ready For Day 1</span>
          <b style={{ color: "var(--aurora)" }}>{readyCount}</b>
          <em>Hardware & credentials cleared</em>
        </div>
        <div className="metric-box">
          <span>In Provisioning</span>
          <b style={{ color: "var(--copper-2)" }}>{provisioningCount}</b>
          <em>Security & logistics in transit</em>
        </div>
        <div className="metric-box">
          <span>Next Cohort Start</span>
          <b style={{ color: "var(--gold)" }}>Mar 16</b>
          <em>Spring 2026 Engineering Loop</em>
        </div>
      </section>

      {/* Search & Filter Controls */}
      <div className="filters" style={{ display: "flex", flexWrap: "wrap", gap: "12px", alignItems: "center", marginBottom: "24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <label htmlFor="hired-filter" style={{ fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)" }}>
            Status:
          </label>
          <select
            id="hired-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{
              background: "#0c0d12",
              color: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              padding: "7px 12px",
              fontSize: "0.9rem",
            }}
          >
            <option value="all">All Hires ({hiredEmployees.length})</option>
            <option value="Ready for Day 1">Ready for Day 1 ({readyCount})</option>
            <option value="Provisioning">Provisioning ({provisioningCount})</option>
            <option value="Onboarded">Onboarded</option>
          </select>
        </div>

        <div style={{ flex: "1 1 240px", maxWidth: "380px" }}>
          <input
            type="search"
            placeholder="Search employee, ID, or department..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: "100%",
              background: "#0c0d12",
              color: "var(--paper)",
              border: "1px solid var(--line)",
              borderRadius: "8px",
              padding: "7px 12px",
              fontSize: "0.9rem",
            }}
          />
        </div>

        <span style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.1em", color: "var(--paper-dim)" }}>
          {filteredEmployees.length} of {hiredEmployees.length} Employee Files
        </span>
      </div>

      {/* Split Directory Layout */}
      <div className="split">
        {/* Left Employee Roster */}
        <aside className="menu" aria-label="Hired Employee Directory">
          {filteredEmployees.length === 0 ? (
            <p className="empty">No matching employee records.</p>
          ) : (
            filteredEmployees.map((emp) => {
              const isSelected = selected?.id === emp.id;
              const isReady = emp.onboarding_status === "Ready for Day 1";
              return (
                <button
                  key={emp.id}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => setParams({ emp: emp.id })}
                  style={{
                    textAlign: "left",
                    padding: "16px",
                    borderBottom: "1px solid var(--line)",
                    display: "grid",
                    gap: "6px",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--copper-2)", letterSpacing: "0.08em", whiteSpace: "nowrap" }}>
                      {emp.employee_code}
                    </span>
                    <Pill tone={isReady ? "tone-open" : "tone-human"}>
                      {emp.onboarding_status}
                    </Pill>
                  </div>
                  <strong style={{ fontSize: "1.05rem", color: "var(--paper)", display: "block" }}>
                    {emp.display_name}
                  </strong>
                  <em style={{ fontSize: "0.85rem", color: "var(--paper-dim)", display: "block", lineHeight: "1.35" }}>
                    {emp.role_title}
                  </em>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "4px" }}>
                    <span style={{ fontSize: "0.78rem", color: "var(--aurora)", fontFamily: "var(--mono)" }}>
                      Starts: {emp.start_date.replace(/^[A-Za-z]+,\s*/, "")}
                    </span>
                    <span style={{ fontSize: "0.76rem", color: "var(--paper-dim)", fontFamily: "var(--mono)" }}>
                      {emp.checklist.filter((c) => c.completed).length}/{emp.checklist.length} tasks
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </aside>

        {/* Right Inspector & Interactive Checklist */}
        {selected ? (
          <article className="inspector">
            {/* Header Profile */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
              <div>
                <p className="kicker">Corporate Employee Dossier</p>
                <h2>{selected.display_name}</h2>
                <p style={{ color: "var(--copper-2)", fontSize: "1.1rem", marginTop: "4px", fontWeight: 500 }}>
                  {selected.role_title} · {selected.department}
                </p>
                <p style={{ color: "var(--paper-dim)", fontSize: "0.9rem", marginTop: "2px" }}>
                  Employee ID: <strong style={{ color: "var(--paper)" }}>{selected.employee_code}</strong> · Hiring Authority: <strong>{selected.hiring_manager}</strong>
                </p>
              </div>
              <div style={{ textAlign: "right", display: "grid", gap: "6px", justifyItems: "end" }}>
                <Pill tone={selected.onboarding_status === "Ready for Day 1" ? "tone-open" : "tone-human"}>
                  {selected.onboarding_status}
                </Pill>
                <span style={{ fontFamily: "var(--mono)", fontSize: "11px", color: "var(--gold)" }}>
                  Target Start: {selected.start_date.replace(/^[A-Za-z]+,\s*/, "")}
                </span>
              </div>
            </div>

            {/* Facts Grid */}
            <dl
              className="facts"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                gap: "16px 20px",
                marginTop: "20px",
                padding: "16px",
                background: "rgba(255, 255, 255, 0.02)",
                border: "1px solid var(--line)",
                borderRadius: "6px",
              }}
            >
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Corporate Email
                </dt>
                <dd style={{ fontFamily: "var(--mono)", color: "var(--aurora)", margin: 0, fontSize: "0.92rem", wordBreak: "break-all" }}>
                  {selected.corporate_email}
                </dd>
              </div>
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Personal Contact
                </dt>
                <dd style={{ color: "var(--paper)", margin: 0, fontSize: "0.92rem" }}>
                  {selected.personal_email}
                  <div style={{ color: "var(--paper-dim)", fontSize: "0.85rem", marginTop: "2px" }}>{selected.phone}</div>
                </dd>
              </div>
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Work Location
                </dt>
                <dd style={{ color: "var(--paper)", margin: 0, fontSize: "0.92rem" }}>
                  {selected.location}
                </dd>
              </div>
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Agreed Compensation
                </dt>
                <dd style={{ fontFamily: "var(--mono)", color: "var(--copper-2)", margin: 0, fontSize: "0.92rem" }}>
                  {selected.compensation_package}
                </dd>
              </div>
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Target Start Date
                </dt>
                <dd style={{ color: "var(--gold)", margin: 0, fontSize: "0.95rem", fontWeight: 500 }}>
                  {selected.start_date}
                </dd>
              </div>
              <div>
                <dt style={{ fontFamily: "var(--mono)", fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--paper-dim)", marginBottom: "4px" }}>
                  Recruiter Attestation
                </dt>
                <dd style={{ color: "var(--paper-dim)", margin: 0, fontSize: "0.92rem" }}>
                  lead.recruiter@talvora.test
                </dd>
              </div>
            </dl>

            {/* Onboarding Checklist Progress Header */}
            <div style={{ marginTop: "24px", borderTop: "1px solid var(--line)", paddingTop: "18px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                <h3 style={{ margin: 0 }}>Onboarding & Provisioning Protocol</h3>
                <span style={{ fontFamily: "var(--mono)", fontSize: "12px", color: progressPercent === 100 ? "var(--aurora)" : "var(--copper-2)" }}>
                  {completedTasks} of {totalTasks} Tasks Completed ({progressPercent}%)
                </span>
              </div>

              {/* Progress Bar */}
              <div style={{ width: "100%", height: "6px", background: "rgba(255, 255, 255, 0.08)", borderRadius: "3px", overflow: "hidden", marginBottom: "20px" }}>
                <div
                  style={{
                    width: `${progressPercent}%`,
                    height: "100%",
                    background: progressPercent === 100 ? "var(--aurora)" : "var(--copper)",
                    transition: "width 0.3s ease",
                  }}
                />
              </div>

              {/* Interactive Checklist by Category */}
              <div style={{ display: "grid", gap: "16px" }}>
                {categories.map((cat) => {
                  const tasks = selected.checklist.filter((t) => t.category === cat);
                  if (tasks.length === 0) return null;
                  return (
                    <div key={cat} style={{ background: "rgba(255, 255, 255, 0.02)", border: "1px solid var(--line)", borderRadius: "6px", padding: "14px 16px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                        <span style={{ fontFamily: "var(--mono)", fontSize: "11px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--copper-2)" }}>
                          {cat}
                        </span>
                        <span style={{ fontFamily: "var(--mono)", fontSize: "10px", color: "var(--paper-dim)" }}>
                          {tasks.filter((t) => t.completed).length} / {tasks.length} Cleared
                        </span>
                      </div>

                      <div style={{ display: "grid", gap: "8px" }}>
                        {tasks.map((task) => (
                          <label
                            key={task.id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "12px",
                              cursor: "pointer",
                              padding: "8px 10px",
                              borderRadius: "4px",
                              background: task.completed ? "rgba(94, 224, 197, 0.04)" : "transparent",
                              transition: "background 0.2s ease",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={task.completed}
                              onChange={() => toggleChecklistItem(selected.id, task.id)}
                              style={{ width: "16px", height: "16px", cursor: "pointer", accentColor: "var(--copper)" }}
                            />
                            <div style={{ flex: 1, display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
                              <span
                                style={{
                                  fontSize: "0.92rem",
                                  color: task.completed ? "var(--paper)" : "var(--paper-dim)",
                                }}
                              >
                                {task.label}
                              </span>
                              <span
                                style={{
                                  fontFamily: "var(--mono)",
                                  fontSize: "11px",
                                  color: task.completed ? "var(--aurora)" : "var(--paper-dim)",
                                  flexShrink: 0,
                                }}
                              >
                                {task.completed ? "✓ " : ""}{task.due_date}
                              </span>
                            </div>
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Recruiter Handoff & Governance Seal */}
            <div style={{ marginTop: "24px", borderTop: "1px solid var(--line)", paddingTop: "18px", display: "grid", gridTemplateColumns: "1fr auto", gap: "16px", alignItems: "center" }}>
              <div>
                <h3 style={{ margin: "0 0 6px 0" }}>Talent Operations Audit Handoff</h3>
                <p style={{ fontSize: "0.92rem", color: "var(--paper-dim)", lineHeight: 1.5 }}>
                  {selected.hiring_notes}
                </p>
                <p style={{ fontSize: "0.82rem", color: "var(--copper-2)", marginTop: "6px", fontFamily: "var(--mono)" }}>
                  Recruiter Sign-Off: lead.recruiter@talvora.test · Immutable Human Record
                </p>
              </div>
              <div className="seal">
                HUMAN
                <br />
                HIRED
                <br />
                TALVORA
              </div>
            </div>
          </article>
        ) : (
          <p className="empty">No employee selected.</p>
        )}
      </div>
    </>
  );
}
