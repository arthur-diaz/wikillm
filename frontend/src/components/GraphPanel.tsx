import { useCallback, useEffect, useRef, useState } from "react";
import { Crosshair, Search, X, RotateCcw, ChevronRight, Menu } from "lucide-react";

/* ─────────────────────────────────────────────────────────────────────────
   Types — identical to your current shape
   ───────────────────────────────────────────────────────────────────────── */
type RawNode = { id: string; label: string; type: string; src: number };
type SimNode = RawNode & {
  x: number; y: number;
  vx: number; vy: number;
  fx: number | null; fy: number | null;
};
type RawLink = { source: string; target: string };
type SimLink = { source: SimNode; target: SimNode; key: string; bidirectional: boolean };

/* ─────────────────────────────────────────────────────────────────────────
   Constants
   ───────────────────────────────────────────────────────────────────────── */
const COLORS: Record<string, string> = {
  source: "#f97316",
  entity: "#a78bfa",
  concept: "#34d399",
};
const TYPE_LABELS: Record<string, string> = {
  source: "Sources",
  entity: "Entités",
  concept: "Concepts",
};

/* ─────────────────────────────────────────────────────────────────────────
   Helpers
   ───────────────────────────────────────────────────────────────────────── */
function hexToRgba(hex: string, a: number) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16),
    g = parseInt(h.slice(2, 4), 16),
    b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}
function escapeText(s: string) {
  return s.length > 28 ? s.slice(0, 26) + "…" : s;
}

/* ─────────────────────────────────────────────────────────────────────────
   Component
   ───────────────────────────────────────────────────────────────────────── */
type Props = { onOpenPage?: (slug: string) => void };

export default function GraphPanel({ onOpenPage }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(true);

  /* UI state (only what needs React re-render) */
  const [panelOpen, setPanelOpen] = useState(true);
  const [sections, setSections] = useState({
    filters: true, groups: false, display: true, forces: false,
  });
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({
    source: true, entity: true, concept: true,
    orphans: true, existing: false,
  });
  const [display, setDisplay] = useState({
    arrows: false, fadeThreshold: 0.45, nodeScale: 1, linkScale: 1, animate: false,
  });
  const [forces, setForces] = useState({
    center: 0.003, repulsion: 10, linkStrength: 0.04, linkDistance: 100,
  });
  const [accent, setAccent] = useState("#8b5cf6");
  const [groupVisible, setGroupVisible] = useState({ source: true, entity: true, concept: true });
  const [hoverInfo, setHoverInfo] = useState<{ label: string; type: string; src: number; degree: number } | null>(null);

  const onOpenPageRef = useRef(onOpenPage);
  useEffect(() => { onOpenPageRef.current = onOpenPage; }, [onOpenPage]);

  /* Mutable sim + interaction state (no re-render) */
  const S = useRef({
    raw: { nodes: [] as RawNode[], links: [] as RawLink[] },
    nodes: [] as SimNode[], links: [] as SimLink[],
    inDeg: {} as Record<string, number>, adj: {} as Record<string, Set<string>>,
    transform: { tx: 0, ty: 0, scale: 1 },
    drag: null as null | { node: SimNode; moved: boolean },
    pan: null as null | { x0: number; y0: number; tx0: number; ty0: number },
    hovered: null as string | null,
    W: 0, H: 0, DPR: 1,
    // live mirrors of React state for the render loop
    query: "", filters, display, forces, accent, groupVisible,
  });
  // mirror React state → ref each render
  S.current.query = query;
  S.current.filters = filters;
  S.current.display = display;
  S.current.forces = forces;
  S.current.accent = accent;
  S.current.groupVisible = groupVisible;

  /* ──────────────────────────────────────────────────────────────────────
     BUILD (rebuilds sim nodes from raw, preserving positions)
     ────────────────────────────────────────────────────────────────────── */
  const build = useCallback(() => {
    const s = S.current;
    const f = s.filters;
    const { nodes: rawNodes, links: rawLinks } = s.raw;

    const anyIn: Record<string, number> = {}, anyOut: Record<string, number> = {};
    for (const l of rawLinks) {
      anyOut[l.source] = (anyOut[l.source] || 0) + 1;
      anyIn[l.target] = (anyIn[l.target] || 0) + 1;
    }

    const prev = new Map(s.nodes.map(n => [n.id, n]));
    const allowed = new Set<string>();
    for (const n of rawNodes) {
      if (!(f as Record<string, boolean>)[n.type]) continue;
      if (!(s.groupVisible as Record<string, boolean>)[n.type]) continue;
      const degree = (anyIn[n.id] || 0) + (anyOut[n.id] || 0);
      if (!f.orphans && degree === 0) continue;
      if (f.existing && n.src === 0 && n.type !== "source") continue;
      allowed.add(n.id);
    }

    s.nodes = rawNodes.filter(n => allowed.has(n.id)).map(n => {
      const p = prev.get(n.id);
      return {
        ...n,
        x: p?.x ?? s.W * 0.2 + Math.random() * s.W * 0.6,
        y: p?.y ?? s.H * 0.2 + Math.random() * s.H * 0.6,
        vx: p?.vx ?? 0, vy: p?.vy ?? 0,
        fx: p?.fx ?? null, fy: p?.fy ?? null,
      };
    });
    const byId = new Map(s.nodes.map(n => [n.id, n]));
    // Deduplicate: collapse A→B and B→A into a single undirected link (flagged bidirectional).
    const pairMap = new Map<string, { a: string; b: string; bi: boolean }>();
    for (const l of rawLinks) {
      if (!byId.has(l.source) || !byId.has(l.target)) continue;
      if (l.source === l.target) continue;
      const [a, b] = l.source < l.target ? [l.source, l.target] : [l.target, l.source];
      const k = a + "|" + b;
      const existing = pairMap.get(k);
      if (existing) existing.bi = true;
      else pairMap.set(k, { a, b, bi: false });
    }
    s.links = Array.from(pairMap.values()).map(({ a, b, bi }) => ({
      source: byId.get(a)!, target: byId.get(b)!,
      key: a + "→" + b,
      bidirectional: bi,
    }));

    s.inDeg = {}; s.adj = {};
    for (const n of s.nodes) s.adj[n.id] = new Set();
    for (const l of s.links) {
      s.inDeg[l.target.id] = (s.inDeg[l.target.id] || 0) + 1;
      s.adj[l.source.id].add(l.target.id);
      s.adj[l.target.id].add(l.source.id);
    }
    if (prev.size === 0) simulate(200);
  }, []);

  /* ──────────────────────────────────────────────────────────────────────
     SIMULATION
     ────────────────────────────────────────────────────────────────────── */
  const simulate = (iters = 1) => {
    const s = S.current;
    const { repulsion, center, linkStrength, linkDistance } = s.forces;
    const { nodes, links, W, H } = s;
    for (let k = 0; k < iters; k++) {
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d2 = dx * dx + dy * dy + 1;
          const f = -repulsion / d2;
          a.vx += f * dx; a.vy += f * dy;
          b.vx -= f * dx; b.vy -= f * dy;
        }
      }
      for (const l of links) {
        const a = l.source, b = l.target;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d - linkDistance) * linkStrength;
        const fx = (f * dx) / d, fy = (f * dy) / d;
        if (a.fx === null) { a.vx += fx; a.vy += fy; }
        if (b.fx === null) { b.vx -= fx; b.vy -= fy; }
      }
      for (const n of nodes) {
        if (n.fx !== null) { n.x = n.fx; n.y = n.fy!; n.vx = 0; n.vy = 0; continue; }
        n.vx = (n.vx + (W / 2 - n.x) * center) * 0.85;
        n.vy = (n.vy + (H / 2 - n.y) * center) * 0.85;
        n.x += n.vx; n.y += n.vy;
      }
    }
  };

  /* ──────────────────────────────────────────────────────────────────────
     DRAW
     ────────────────────────────────────────────────────────────────────── */
  const nodeRadius = (n: SimNode) => {
    const s = S.current;
    const deg = s.inDeg[n.id] || 0;
    return Math.max(3.5, Math.min(14, 4 + deg * 1.5)) * s.display.nodeScale;
  };

  const draw = () => {
    const canvas = canvasRef.current; if (!canvas) return;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    const s = S.current;
    ctx.clearRect(0, 0, s.W, s.H);

    const { tx, ty, scale } = s.transform;
    ctx.save();
    ctx.translate(tx, ty);
    ctx.scale(scale, scale);

    const q = s.query.trim().toLowerCase();
    let hiSet: Set<string> | null = null;
    if (s.hovered) {
      hiSet = new Set([s.hovered, ...(s.adj[s.hovered] || [])]);
    } else if (q) {
      hiSet = new Set(s.nodes.filter(n => n.label.toLowerCase().includes(q)).map(n => n.id));
    }

    /* Links */
    ctx.lineWidth = s.display.linkScale / scale;
    for (const l of s.links) {
      const active = !hiSet || (hiSet.has(l.source.id) && hiSet.has(l.target.id));
      ctx.strokeStyle = active ? "rgba(139,148,158,0.65)" : "rgba(139,148,158,0.08)";
      const mx = (l.source.x + l.target.x) / 2 + (l.target.y - l.source.y) * 0.08;
      const my = (l.source.y + l.target.y) / 2 - (l.target.x - l.source.x) * 0.08;
      ctx.beginPath();
      ctx.moveTo(l.source.x, l.source.y);
      ctx.quadraticCurveTo(mx, my, l.target.x, l.target.y);
      ctx.stroke();

      if (s.display.arrows && active) {
        const size = 4 / scale;
        ctx.fillStyle = ctx.strokeStyle as string;
        // Arrow at target
        {
          const dx = l.target.x - mx, dy = l.target.y - my;
          const d = Math.hypot(dx, dy) + 0.01;
          const ux = dx / d, uy = dy / d;
          const r = nodeRadius(l.target) + 1.5;
          const ax = l.target.x - ux * r, ay = l.target.y - uy * r;
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(ax - ux * size * 2 - uy * size, ay - uy * size * 2 + ux * size);
          ctx.lineTo(ax - ux * size * 2 + uy * size, ay - uy * size * 2 - ux * size);
          ctx.closePath();
          ctx.fill();
        }
        // Arrow at source too if bidirectional
        if (l.bidirectional) {
          const dx = l.source.x - mx, dy = l.source.y - my;
          const d = Math.hypot(dx, dy) + 0.01;
          const ux = dx / d, uy = dy / d;
          const r = nodeRadius(l.source) + 1.5;
          const ax = l.source.x - ux * r, ay = l.source.y - uy * r;
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(ax - ux * size * 2 - uy * size, ay - uy * size * 2 + ux * size);
          ctx.lineTo(ax - ux * size * 2 + uy * size, ay - uy * size * 2 - ux * size);
          ctx.closePath();
          ctx.fill();
        }
      }
    }

    /* Nodes */
    const showLabels = scale > s.display.fadeThreshold;
    for (const n of s.nodes) {
      const active = !hiSet || hiSet.has(n.id);
      const r = nodeRadius(n);
      ctx.globalAlpha = active ? 1 : 0.18;

      if (s.hovered === n.id) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 6 / scale, 0, Math.PI * 2);
        ctx.fillStyle = hexToRgba(s.accent, 0.22);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = COLORS[n.type] || "#8b949e";
      ctx.fill();
      ctx.lineWidth = 1 / scale;
      ctx.strokeStyle = "rgba(0,0,0,0.25)";
      ctx.stroke();

      if (showLabels) {
        const labelFade = Math.min(1, (scale - s.display.fadeThreshold) / 0.3);
        ctx.globalAlpha = (active ? 1 : 0.2) * labelFade;
        ctx.fillStyle = "#c9d1d9";
        ctx.font = `${10 / scale}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textBaseline = "middle";
        ctx.fillText(escapeText(n.label), n.x + r + 4 / scale, n.y);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  };

  /* ──────────────────────────────────────────────────────────────────────
     RAF LOOP
     ────────────────────────────────────────────────────────────────────── */
  useEffect(() => {
    const tick = () => {
      const s = S.current;
      if (s.display.animate || s.drag) simulate(1);
      draw();
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, []);

  /* ──────────────────────────────────────────────────────────────────────
     RESIZE
     ────────────────────────────────────────────────────────────────────── */
  useEffect(() => {
    const canvas = canvasRef.current, wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d")!;
    const resize = () => {
      const r = wrap.getBoundingClientRect();
      const DPR = Math.max(1, window.devicePixelRatio || 1);
      S.current.W = r.width;
      S.current.H = r.height;
      S.current.DPR = DPR;
      canvas.width = r.width * DPR;
      canvas.height = r.height * DPR;
      canvas.style.width = r.width + "px";
      canvas.style.height = r.height + "px";
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  /* ──────────────────────────────────────────────────────────────────────
     FETCH DATA
     ────────────────────────────────────────────────────────────────────── */
  useEffect(() => {
    fetch("/api/graph-data")
      .then(r => r.json())
      .then((data: { nodes: RawNode[]; links: RawLink[] }) => {
        S.current.raw = data;
        setLoading(false);
        build();
        fitToView();
      })
      .catch(() => setLoading(false));
  }, [build]);

  /* Rebuild when filter-ish state changes */
  useEffect(() => {
    if (!loading) build();
  }, [loading, filters, groupVisible, build]);

  /* ──────────────────────────────────────────────────────────────────────
     INTERACTION
     ────────────────────────────────────────────────────────────────────── */
  const screenToWorld = (x: number, y: number) => {
    const { tx, ty, scale } = S.current.transform;
    return { x: (x - tx) / scale, y: (y - ty) / scale };
  };
  const pick = (x: number, y: number) => {
    const p = screenToWorld(x, y);
    const s = S.current;
    for (let i = s.nodes.length - 1; i >= 0; i--) {
      const n = s.nodes[i];
      const r = nodeRadius(n) + 3;
      if ((n.x - p.x) ** 2 + (n.y - p.y) ** 2 <= r * r) return n;
    }
    return null;
  };

  const fitToView = useCallback(() => {
    const s = S.current;
    if (!s.nodes.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of s.nodes) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
    }
    const pad = 80;
    const bw = maxX - minX + pad * 2, bh = maxY - minY + pad * 2;
    const scale = Math.min(s.W / bw, s.H / bh, 2);
    s.transform.scale = scale;
    s.transform.tx = (s.W - bw * scale) / 2 - (minX - pad) * scale;
    s.transform.ty = (s.H - bh * scale) / 2 - (minY - pad) * scale;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return;
    const getXY = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const onMove = (e: MouseEvent) => {
      const s = S.current; const { x, y } = getXY(e);
      if (s.drag) {
        const p = screenToWorld(x, y);
        s.drag.node.x = p.x; s.drag.node.y = p.y;
        s.drag.node.fx = p.x; s.drag.node.fy = p.y;
        s.drag.moved = true;
        return;
      }
      if (s.pan) {
        s.transform.tx = s.pan.tx0 + (x - s.pan.x0);
        s.transform.ty = s.pan.ty0 + (y - s.pan.y0);
        return;
      }
      const n = pick(x, y);
      const id = n ? n.id : null;
      if (id !== s.hovered) {
        s.hovered = id;
        canvas.style.cursor = n ? "pointer" : "grab";
        setHoverInfo(n ? { label: n.label, type: n.type, src: n.src, degree: s.adj[n.id]?.size || 0 } : null);
      }
    };
    const onDown = (e: MouseEvent) => {
      const s = S.current; const { x, y } = getXY(e);
      const n = pick(x, y);
      if (n) s.drag = { node: n, moved: false };
      else s.pan = { x0: x, y0: y, tx0: s.transform.tx, ty0: s.transform.ty };
      canvas.style.cursor = "grabbing";
    };
    const onUp = () => {
      const s = S.current;
      if (s.drag) {
        s.drag.node.fx = null; s.drag.node.fy = null;
        if (!s.drag.moved) onOpenPageRef.current?.(s.drag.node.id);
      }
      s.drag = null; s.pan = null;
      canvas.style.cursor = "grab";
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const s = S.current; const { x, y } = getXY(e);
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const ns = Math.max(0.08, Math.min(6, s.transform.scale * factor));
      s.transform.tx = x - (x - s.transform.tx) * (ns / s.transform.scale);
      s.transform.ty = y - (y - s.transform.ty) * (ns / s.transform.scale);
      s.transform.scale = ns;
    };
    const onDbl = () => fitToView();

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDbl);
    return () => {
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", onDbl);
    };
  }, [fitToView]);

  const resetFilters = () => {
    setQuery("");
    setFilters({ source: true, entity: true, concept: true, orphans: true, existing: false });
    setGroupVisible({ source: true, entity: true, concept: true });
  };

  /* ──────────────────────────────────────────────────────────────────────
     RENDER
     ────────────────────────────────────────────────────────────────────── */
  const toggleSwitch = (on: boolean, onClick: () => void) => (
    <div
      onClick={onClick}
      className="relative cursor-pointer shrink-0 transition-colors"
      style={{
        width: 30, height: 16, borderRadius: 999,
        background: on ? accent : "#2a313c",
      }}
    >
      <div
        className="absolute top-[2px] rounded-full transition-all"
        style={{
          width: 12, height: 12,
          left: on ? 16 : 2,
          background: on ? "#fff" : "#8b949e",
        }}
      />
    </div>
  );

  const Section = ({ id, title, children, headerExtras }: { id: keyof typeof sections; title: string; children: React.ReactNode; headerExtras?: React.ReactNode }) => (
    <div className="border-b border-[#2a313c] last:border-0">
      <div
        className="flex items-center gap-1.5 px-3 py-2.5 cursor-pointer select-none hover:bg-white/[0.02] text-[12px] font-semibold text-gray-200"
        onClick={() => setSections(s => ({ ...s, [id]: !s[id] }))}
      >
        <ChevronRight size={10} className="text-gray-500 transition-transform" style={{ transform: sections[id] ? "rotate(90deg)" : "none" }} />
        <span className="flex-1">{title}</span>
        {headerExtras}
      </div>
      {sections[id] && <div className="px-3 pb-3">{children}</div>}
    </div>
  );

  const Slider = ({ label, value, min, max, step, onChange, format }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format?: (v: number) => string }) => (
    <div className="py-2">
      <div className="flex justify-between items-center text-[11px] mb-1.5 text-gray-300">
        <span>{label}</span>
        <span className="text-gray-500 tabular-nums">{format ? format(value) : value}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full h-1 rounded-full bg-[#2a313c] outline-none cursor-pointer appearance-none graph-range"
        style={{ accentColor: accent }}
      />
    </div>
  );

  return (
    <div ref={wrapRef} className="relative w-full h-full overflow-hidden" style={{ background: "#0d1117" }}>
      <style>{`
        .graph-range::-webkit-slider-thumb { appearance: none; width: 13px; height: 13px; border-radius: 50%; background: #fff; cursor: pointer; border: 0; box-shadow: 0 1px 3px rgba(0,0,0,.4); }
        .graph-range::-moz-range-thumb { width: 13px; height: 13px; border-radius: 50%; background: #fff; cursor: pointer; border: 0; }
      `}</style>

      <canvas ref={canvasRef} className="block w-full h-full" style={{ cursor: "grab" }} />

      {/* Type pills — top-left */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-1.5">
        {(["source", "entity", "concept"] as const).map(t => {
          const active = filters[t];
          const col = COLORS[t];
          return (
            <button
              key={t}
              onClick={() => setFilters(f => ({ ...f, [t]: !f[t] }))}
              className="text-[11px] px-2.5 py-[3px] rounded-full border transition-colors font-medium"
              style={{
                background: active ? hexToRgba(col, 0.15) : "transparent",
                color: active ? col : "#6e7681",
                borderColor: active ? hexToRgba(col, 0.4) : "#2a313c",
              }}
            >
              {TYPE_LABELS[t]}
            </button>
          );
        })}
      </div>

      {/* Hover tooltip */}
      {hoverInfo && (
        <div className="absolute top-11 left-3 z-10 rounded-lg border border-[#2a313c] px-3 py-2 text-xs pointer-events-none max-w-[280px]" style={{ background: "#161b22", boxShadow: "0 10px 30px rgba(0,0,0,.45)" }}>
          <div className="font-semibold text-gray-100 flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: COLORS[hoverInfo.type] }} />
            {hoverInfo.label}
          </div>
          <div className="text-gray-500 text-[11px] mt-0.5">
            {TYPE_LABELS[hoverInfo.type]} · {hoverInfo.degree} lien{hoverInfo.degree > 1 ? "s" : ""}
            {hoverInfo.src > 0 && ` · ${hoverInfo.src} source${hoverInfo.src > 1 ? "s" : ""}`}
          </div>
        </div>
      )}

      {/* Help line — bottom-left */}
      <div className="absolute bottom-3 left-3 z-10 flex gap-3 items-center text-[11px] text-gray-500">
        <span><kbd className="px-1.5 py-[1px] rounded border border-[#2a313c] bg-[#161b22] font-mono text-[10px] text-gray-400">scroll</kbd> zoomer</span>
        <span><kbd className="px-1.5 py-[1px] rounded border border-[#2a313c] bg-[#161b22] font-mono text-[10px] text-gray-400">drag</kbd> déplacer</span>
        <span><kbd className="px-1.5 py-[1px] rounded border border-[#2a313c] bg-[#161b22] font-mono text-[10px] text-gray-400">dbl-clic</kbd> recentrer</span>
      </div>

      {/* Toolbar (when panel hidden) */}
      {!panelOpen && (
        <div className="absolute top-3 right-3 z-10 flex gap-1.5">
          <button onClick={fitToView} title="Recentrer" className="w-[30px] h-[30px] grid place-items-center rounded-md border border-[#2a313c] bg-[#161b22] text-gray-400 hover:text-gray-100 hover:border-[#3a424f]">
            <Crosshair size={14} />
          </button>
          <button onClick={() => setPanelOpen(true)} title="Afficher le panneau" className="w-[30px] h-[30px] grid place-items-center rounded-md border border-[#2a313c] bg-[#161b22] text-gray-400 hover:text-gray-100 hover:border-[#3a424f]">
            <Menu size={14} />
          </button>
        </div>
      )}

      {/* Side panel */}
      {panelOpen && (
        <aside
          className="absolute top-3 right-3 w-[290px] rounded-xl border border-[#2a313c] overflow-y-auto z-20"
          style={{
            background: "#161b22",
            maxHeight: "calc(100% - 24px)",
            boxShadow: "0 10px 30px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.3)",
          }}
        >
          <div className="sticky top-0 z-[2] flex items-center gap-1.5 px-3 py-2.5 border-b border-[#2a313c]" style={{ background: "#161b22" }}>
            <ChevronRight size={10} className="text-gray-500 rotate-90" />
            <h2 className="flex-1 text-[13px] font-semibold">Filtres</h2>
            <button onClick={resetFilters} title="Réinitialiser" className="p-1 rounded text-gray-500 hover:text-gray-200 hover:bg-[#1c2129]">
              <RotateCcw size={12} />
            </button>
            <button onClick={() => setPanelOpen(false)} title="Masquer" className="p-1 rounded text-gray-500 hover:text-gray-200 hover:bg-[#1c2129]">
              <X size={12} />
            </button>
          </div>

          {/* Search */}
          <div className="px-3 pt-3">
            <div className="flex items-center gap-2 bg-[#0d1117] border border-[#2a313c] rounded-md px-2.5 py-1.5">
              <Search size={13} className="text-gray-500 shrink-0" />
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Rechercher des nœuds..."
                className="flex-1 bg-transparent outline-none text-[12px] text-gray-100 placeholder:text-gray-500"
              />
            </div>
          </div>

          {/* Filtres */}
          <Section id="filters" title="Filtres">
            {(["source", "entity", "concept"] as const).map((t, i) => (
              <div key={t} className={`flex items-center justify-between py-2 ${i > 0 ? "border-t border-white/[0.04]" : ""}`}>
                <div className="text-[12px] text-gray-200">{TYPE_LABELS[t]}</div>
                {toggleSwitch(filters[t], () => setFilters(f => ({ ...f, [t]: !f[t] })))}
              </div>
            ))}
            <div className="flex items-center justify-between py-2 border-t border-white/[0.04]">
              <div className="text-[12px] text-gray-200">Orphelins <span className="block text-gray-500 text-[11px] font-normal mt-0.5">Nœuds sans lien</span></div>
              {toggleSwitch(filters.orphans, () => setFilters(f => ({ ...f, orphans: !f.orphans })))}
            </div>
            <div className="flex items-center justify-between py-2 border-t border-white/[0.04]">
              <div className="text-[12px] text-gray-200">Fichiers existants uniquement <span className="block text-gray-500 text-[11px] font-normal mt-0.5">Masquer références non résolues</span></div>
              {toggleSwitch(filters.existing, () => setFilters(f => ({ ...f, existing: !f.existing })))}
            </div>
          </Section>

          {/* Groupes */}
          <Section id="groups" title="Groupes">
            <div className="text-[11px] text-gray-500 mb-1.5">Couleur par type</div>
            <div className="grid grid-cols-4 gap-1.5 pb-2">
              {(["source", "entity", "concept"] as const).map(t => (
                <div
                  key={t}
                  onClick={() => setGroupVisible(g => ({ ...g, [t]: !g[t] }))}
                  className="aspect-square rounded-md cursor-pointer transition-transform hover:scale-110"
                  title={TYPE_LABELS[t]}
                  style={{ background: COLORS[t], border: `2px solid ${groupVisible[t] ? "#fff" : "transparent"}` }}
                />
              ))}
            </div>
            <div className="text-[11px] text-gray-500 mt-2 mb-1.5">Accent</div>
            <div className="grid grid-cols-6 gap-1.5">
              {["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#ef4444"].map(c => (
                <div
                  key={c}
                  onClick={() => setAccent(c)}
                  className="aspect-square rounded-md cursor-pointer hover:scale-110 transition-transform"
                  style={{ background: c, border: `2px solid ${accent === c ? "#fff" : "transparent"}` }}
                />
              ))}
            </div>
          </Section>

          {/* Afficher */}
          <Section id="display" title="Afficher">
            <div className="flex items-center justify-between py-2">
              <div className="text-[12px] text-gray-200">Flèches</div>
              {toggleSwitch(display.arrows, () => setDisplay(d => ({ ...d, arrows: !d.arrows })))}
            </div>
            <Slider label="Seuil de fondu du texte" value={display.fadeThreshold} min={0.1} max={1.5} step={0.05} onChange={v => setDisplay(d => ({ ...d, fadeThreshold: v }))} format={v => v.toFixed(2)} />
            <Slider label="Taille des nœuds" value={display.nodeScale} min={0.3} max={2.5} step={0.1} onChange={v => setDisplay(d => ({ ...d, nodeScale: v }))} format={v => v.toFixed(1)} />
            <Slider label="Épaisseur des liens" value={display.linkScale} min={0.3} max={3} step={0.1} onChange={v => setDisplay(d => ({ ...d, linkScale: v }))} format={v => v.toFixed(1)} />
            <button
              onClick={() => setDisplay(d => ({ ...d, animate: !d.animate }))}
              className="w-full mt-2 py-1.5 rounded-md text-[12px] font-semibold transition-colors"
              style={{
                background: display.animate ? "#1c2129" : accent,
                color: display.animate ? "#8b949e" : "#fff",
                border: display.animate ? "1px solid #3a424f" : "0",
              }}
            >
              {display.animate ? "Mettre en pause" : "Animer"}
            </button>
          </Section>

          {/* Forces */}
          <Section id="forces" title="Forces">
            <Slider label="Force de centre" value={forces.center} min={0} max={0.02} step={0.001} onChange={v => setForces(f => ({ ...f, center: v }))} format={v => v.toFixed(3)} />
            <Slider label="Répulsion" value={forces.repulsion} min={0} max={20} step={0.01} onChange={v => setForces(f => ({ ...f, repulsion: v }))} format={v => Math.round(v).toString()} />
            <Slider label="Force des liens" value={forces.linkStrength} min={0} max={0.2} step={0.005} onChange={v => setForces(f => ({ ...f, linkStrength: v }))} format={v => v.toFixed(3)} />
            <Slider label="Distance des liens" value={forces.linkDistance} min={30} max={300} step={5} onChange={v => setForces(f => ({ ...f, linkDistance: v }))} format={v => Math.round(v).toString()} />
          </Section>
        </aside>
      )}

      {loading && (
        <p className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-gray-500 text-sm">Chargement…</p>
      )}
    </div>
  );
}
