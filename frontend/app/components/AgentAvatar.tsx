/**
 * The agent's face: the Fadenstack "stack" -- three plates, the rose middle one
 * is the thread, and two eyes peek out from under the top plate. Geometry and
 * colours from brand/avatar/make_agent_avatar.py, drawn inline so it follows
 * the mode without a request.
 *
 * Up to 32 px the small cut is drawn (bigger eyes, the top plate raised, no
 * loose thread end), where the full cut's eyes would run together. Like the
 * mark, it never takes the accent.
 *
 * "mono" is the silhouette in currentColor (icon buttons, single-colour
 * places): each eye is a ring with a pupil, and overlaps are transparent gaps
 * (masks), so it stays right on any background.
 */
import { useId, type ReactNode } from "react";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";

export interface AgentAvatarProps {
  /** Rendered width and height in px. */
  size?: number;
  /** A custom agent's own thread colour; Fadenstack's own agents keep the rose. */
  color?: string;
  /** Announced name; omit when text next to it already says it. */
  title?: string;
  variant?: "color" | "mono";
}

const COLORS = {
  light: { plate: "#1C2436", side: "#0A0E1A", thread: "#E2325A", threadSide: "#A8143E", hollow: "rgba(10,14,26,.38)" },
  dark: { plate: "#F3F4F6", side: "#AEB7C6", thread: "#FF4D6D", threadSide: "#C21E48", hollow: "rgba(10,14,26,.55)" },
};
const EYE = "#FFFFFF";
const PUPIL = "#0A0E1A";

const CUTS = {
  full: { view: "21 30 158 158", lid: 62, eyes: [88, 112], rx: 8, ry: 9.5, outline: 2, pupil: 4.2, glint: 1.4, ring: 3.6, mpupil: 4.6, gap: 4 },
  small: { view: "19.5 25 161 161", lid: 56, eyes: [84, 116], rx: 11, ry: 12.5, outline: 3, pupil: 5.8, glint: 1.9, ring: 6, mpupil: 6.4, gap: 6 },
};
const EYE_Y = 104;
const TAIL = "M152 128 C168 132 176 148 168 160";

const poly = (p: [number, number][]) => `M${p.map(([x, y]) => `${x} ${y}`).join(" L")} Z`;

/** A plate centred at y = cy: its top face and its two visible sides. */
function plate(cy: number) {
  return {
    top: poly([[100, cy - 29], [158, cy], [100, cy + 29], [42, cy]]),
    left: poly([[42, cy], [100, cy + 29], [100, cy + 38], [42, cy + 9]]),
    right: poly([[100, cy + 29], [158, cy], [158, cy + 9], [100, cy + 38]]),
    outline: poly([[42, cy], [100, cy - 29], [158, cy], [158, cy + 9], [100, cy + 38], [42, cy + 9]]),
  };
}

function Plate({ cy, top, side }: { cy: number; top: string; side: string }) {
  const p = plate(cy);
  const solid = (fill: string) => ({ fill, stroke: fill, strokeWidth: 3, strokeLinejoin: "round" as const });
  return (
    <>
      <path d={p.left} {...solid(side)} />
      <path d={p.right} {...solid(side)} />
      <path d={p.top} {...solid(top)} />
    </>
  );
}

export default function AgentAvatar({ size = 56, color, title, variant = "color" }: AgentAvatarProps) {
  const { palette } = useTheme();
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const c = COLORS[palette.mode === "dark" ? "dark" : "light"];
  const thread = color ?? c.thread;
  const threadSide = color ? `color-mix(in srgb, ${color} 72%, #000)` : c.threadSide;
  const small = size <= 32;
  const k = small ? CUTS.small : CUTS.full;
  const a11y = title ? { role: "img", "aria-label": title } : { "aria-hidden": true };

  if (variant === "mono") {
    const bottom = plate(146), middle = plate(122), lid = plate(k.lid);
    const all = <rect x={0} y={0} width={200} height={200} fill="#fff" />;
    const grow = { fill: "#000", stroke: "#000", strokeWidth: k.gap * 2, strokeLinejoin: "round" as const };
    const mask = (suffix: string, children: ReactNode) => (
      <mask id={`${id}${suffix}`} maskUnits="userSpaceOnUse" x={0} y={0} width={200} height={200}>{all}{children}</mask>
    );
    return (
      <Box component="svg" viewBox={k.view} width={size} height={size} data-agent-avatar="mono" {...a11y}
        sx={{ display: "block", flexShrink: 0, overflow: "visible" }}>
        <defs>
          {mask("b", <>
            <path d={middle.outline} {...grow} />
            {!small && <path d={TAIL} fill="none" stroke="#000" strokeWidth={6 + k.gap * 2} strokeLinecap="round" />}
          </>)}
          {mask("m", <>
            <path d={lid.outline} {...grow} />
            {k.eyes.map((x) => (
              <ellipse key={x} cx={x} cy={EYE_Y} rx={k.rx + k.ring / 2 + k.gap} ry={k.ry + k.ring / 2 + k.gap} fill="#000" />
            ))}
          </>)}
          {mask("e", <path d={lid.outline} {...grow} />)}
        </defs>
        <g fill="currentColor" stroke="currentColor" strokeLinejoin="round">
          <path d={bottom.outline} strokeWidth={3} mask={`url(#${id}b)`} />
          {!small && <path d={TAIL} fill="none" strokeWidth={6} strokeLinecap="round" />}
          <path d={middle.outline} strokeWidth={3} mask={`url(#${id}m)`} />
          <g mask={`url(#${id}e)`}>
            {k.eyes.map((x) => (
              <g key={x}>
                <ellipse cx={x} cy={EYE_Y} rx={k.rx} ry={k.ry} fill="none" strokeWidth={k.ring} />
                <circle cx={x + 1} cy={EYE_Y - 1.5} r={k.mpupil} stroke="none" />
              </g>
            ))}
          </g>
          <path d={lid.outline} strokeWidth={3} />
        </g>
      </Box>
    );
  }

  return (
    <Box
      component="svg"
      viewBox={k.view}
      width={size}
      height={size}
      data-agent-avatar={small ? "small" : "full"}
      {...a11y}
      sx={{ display: "block", flexShrink: 0, overflow: "visible" }}
    >
      <Plate cy={146} top={c.plate} side={c.side} />
      {!small && <path d={TAIL} fill="none" stroke={thread} strokeWidth={6} strokeLinecap="round" />}
      <Plate cy={122} top={thread} side={threadSide} />
      {!small && <ellipse cx={100} cy={110} rx={34} ry={11} fill={c.hollow} />}
      {k.eyes.map((x) => (
        <g key={x}>
          <ellipse cx={x} cy={EYE_Y} rx={k.rx} ry={k.ry} fill={EYE} stroke={PUPIL} strokeWidth={k.outline} />
          <circle cx={x + 1} cy={EYE_Y - 1.5} r={k.pupil} fill={PUPIL} />
          <circle cx={x + 2.6} cy={EYE_Y - 1.5 - k.pupil * 0.45} r={k.glint} fill={EYE} />
        </g>
      ))}
      <Plate cy={k.lid} top={c.plate} side={c.side} />
    </Box>
  );
}
