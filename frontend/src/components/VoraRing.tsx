export function VoraRing({ variant = "hero" }: { variant?: "hero" | "mark" }) {
  if (variant === "mark") {
    return (
      <svg className="mark" viewBox="0 0 64 64" aria-hidden="true">
        <g className="spin">
          <circle className="ring-stroke" cx="32" cy="32" r="22" />
          <circle className="ring-stroke dim" cx="32" cy="32" r="14" />
        </g>
        <circle className="core-dot" cx="32" cy="32" r="3.5" />
      </svg>
    );
  }

  return (
    <svg
      className="vora-hero"
      viewBox="0 0 500 500"
      role="img"
      aria-label="Hiring machinery orbits a still center. Humans decide."
    >
      <defs>
        <path
          id="vora-circle"
          d="M250,250 m-196,0 a196,196 0 1,1 392,0 a196,196 0 1,1 -392,0"
        />
      </defs>
      <g className="spin">
        <circle className="ring-stroke" cx="250" cy="250" r="196" />
        <circle className="ring-stroke dim" cx="250" cy="250" r="150" />
        <text className="ring-type">
          <textPath href="#vora-circle">
            QUARANTINE · SCAN · EXTRACT · CITE · VERSION THE RULE · WRITE THE STAGE · NO PROTECTED
            TRAITS ·
          </textPath>
        </text>
      </g>
      <text className="core-label" x="250" y="242" textAnchor="middle">
        HUMAN
      </text>
      <text className="core-sub" x="250" y="278" textAnchor="middle">
        decides
      </text>
    </svg>
  );
}
