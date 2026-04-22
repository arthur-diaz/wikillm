/**
 * Shared design primitives — applied consistently across all panels.
 * Match the visual language established in GraphPanel.
 *
 * Tokens (for Tailwind arbitrary values):
 *   bg          #0d1117
 *   bg-panel    #161b22
 *   bg-raised   #1c2129
 *   border      #2a313c
 *   border-hi   #3a424f
 *   text        #e6edf3
 *   text-dim    #8b949e
 *   text-faint  #6e7681
 *   accent      #8b5cf6
 *   source      #f97316
 *   entity      #a78bfa
 *   concept     #34d399
 */
import { useState } from "react";
import { ChevronRight } from "lucide-react";

export const COLORS = {
  source: "#f97316",
  entity: "#a78bfa",
  concept: "#34d399",
} as const;

export const TYPE_LABELS: Record<string, string> = {
  source: "Sources",
  entity: "Entités",
  concept: "Concepts",
};

export const ACCENT = "#8b5cf6";

export function hexToRgba(hex: string, a: number) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16),
    g = parseInt(h.slice(2, 4), 16),
    b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/* ──────────────────────────────────────────────────────────────
   Switch (toggle 30×16)
   ────────────────────────────────────────────────────────────── */
export function Switch({ on, onChange, accent = ACCENT }: { on: boolean; onChange: (v: boolean) => void; accent?: string }) {
  return (
    <div
      onClick={() => onChange(!on)}
      className="relative cursor-pointer shrink-0 transition-colors"
      style={{ width: 30, height: 16, borderRadius: 999, background: on ? accent : "#2a313c" }}
    >
      <div
        className="absolute top-[2px] rounded-full transition-all"
        style={{ width: 12, height: 12, left: on ? 16 : 2, background: on ? "#fff" : "#8b949e" }}
      />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
   Section (accordion)
   ────────────────────────────────────────────────────────────── */
export function Section({
  title, defaultOpen = true, children, headerExtras,
}: { title: string; defaultOpen?: boolean; children: React.ReactNode; headerExtras?: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-[#2a313c] last:border-0">
      <div
        className="flex items-center gap-1.5 px-3 py-2.5 cursor-pointer select-none hover:bg-white/[0.02] text-[12px] font-semibold text-gray-200"
        onClick={() => setOpen(v => !v)}
      >
        <ChevronRight size={10} className="text-gray-500 transition-transform" style={{ transform: open ? "rotate(90deg)" : "none" }} />
        <span className="flex-1">{title}</span>
        {headerExtras}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
   Slider
   ────────────────────────────────────────────────────────────── */
export function Slider({
  label, value, min, max, step, onChange, format, accent = ACCENT,
}: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format?: (v: number) => string; accent?: string }) {
  return (
    <div className="py-2">
      <div className="flex justify-between items-center text-[11px] mb-1.5 text-gray-300">
        <span>{label}</span>
        <span className="text-gray-500 tabular-nums">{format ? format(value) : value}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full h-1 rounded-full bg-[#2a313c] outline-none cursor-pointer appearance-none ui-range"
        style={{ accentColor: accent }}
      />
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
   Card — panel-style container
   ────────────────────────────────────────────────────────────── */
export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border border-[#2a313c] p-4 mb-3 ${className}`}
      style={{ background: "#161b22" }}
    >
      {children}
    </div>
  );
}

/* ──────────────────────────────────────────────────────────────
   Buttons
   ────────────────────────────────────────────────────────────── */
export function BtnPrimary({
  children, onClick, disabled, title, className = "", accent = ACCENT,
}: { children: React.ReactNode; onClick?: () => void; disabled?: boolean; title?: string; className?: string; accent?: string }) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title}
      className={`py-1.5 px-3 rounded-md text-[12px] font-semibold text-white transition-colors disabled:opacity-40 ${className}`}
      style={{ background: accent }}
    >
      {children}
    </button>
  );
}

export function BtnSecondary({
  children, onClick, disabled, title, className = "",
}: { children: React.ReactNode; onClick?: () => void; disabled?: boolean; title?: string; className?: string }) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title}
      className={`py-1.5 px-3 rounded-md text-[12px] font-medium text-gray-300 border border-[#2a313c] bg-[#1c2129] hover:border-[#3a424f] hover:text-gray-100 transition-colors disabled:opacity-40 ${className}`}
    >
      {children}
    </button>
  );
}

export function IconBtn({
  children, onClick, title, active, className = "",
}: { children: React.ReactNode; onClick?: () => void; title?: string; active?: boolean; className?: string }) {
  return (
    <button
      onClick={onClick} title={title}
      className={`w-[30px] h-[30px] grid place-items-center rounded-md border transition-colors ${
        active
          ? "border-[#3a424f] text-[#a78bfa] bg-[#1c2129]"
          : "border-[#2a313c] bg-[#161b22] text-gray-400 hover:text-gray-100 hover:border-[#3a424f]"
      } ${className}`}
    >
      {children}
    </button>
  );
}

/* ──────────────────────────────────────────────────────────────
   Type pill
   ────────────────────────────────────────────────────────────── */
export function TypePill({
  type, active, onClick,
}: { type: "source" | "entity" | "concept"; active: boolean; onClick?: () => void }) {
  const col = COLORS[type];
  return (
    <button
      onClick={onClick}
      className="text-[11px] px-2.5 py-[3px] rounded-full border transition-colors font-medium"
      style={{
        background: active ? hexToRgba(col, 0.15) : "transparent",
        color: active ? col : "#6e7681",
        borderColor: active ? hexToRgba(col, 0.4) : "#2a313c",
      }}
    >
      {TYPE_LABELS[type]}
    </button>
  );
}

/* ──────────────────────────────────────────────────────────────
   Text input
   ────────────────────────────────────────────────────────────── */
export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return (
    <input
      {...rest}
      className={`w-full bg-[#0d1117] border border-[#2a313c] focus:border-[#8b5cf6] rounded-md px-2.5 py-1.5 text-[12px] text-gray-100 placeholder:text-gray-500 outline-none transition-colors ${className}`}
    />
  );
}

/* ──────────────────────────────────────────────────────────────
   Section title (uppercase label)
   ────────────────────────────────────────────────────────────── */
export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-[11px] font-semibold text-gray-500 uppercase tracking-[0.08em] mb-2.5">{children}</h3>;
}

/* ──────────────────────────────────────────────────────────────
   Shared global styles — import once near app root
   ────────────────────────────────────────────────────────────── */
export function UIStyles() {
  return (
    <style>{`
      .ui-range::-webkit-slider-thumb { appearance: none; width: 13px; height: 13px; border-radius: 50%; background: #fff; cursor: pointer; border: 0; box-shadow: 0 1px 3px rgba(0,0,0,.4); }
      .ui-range::-moz-range-thumb { width: 13px; height: 13px; border-radius: 50%; background: #fff; cursor: pointer; border: 0; }
      .ui-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
      .ui-scroll::-webkit-scrollbar-thumb { background: #2a313c; border-radius: 4px; }
      .ui-scroll::-webkit-scrollbar-thumb:hover { background: #3a424f; }
      .ui-scroll::-webkit-scrollbar-track { background: transparent; }
    `}</style>
  );
}
