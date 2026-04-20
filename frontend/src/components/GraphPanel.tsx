import { useEffect, useRef, useState } from "react";

type Node = { id: string; label: string; type: string; src: number; x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null };
type Link = { source: string; target: string };
type GraphData = { nodes: Node[]; links: Link[] };

const COLORS: Record<string, string> = { source: "#f97316", entity: "#a78bfa", concept: "#34d399" };
const R: Record<string, number> = { source: 5, entity: 7, concept: 6 };

export default function GraphPanel() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [info, setInfo] = useState<Node | null>(null);
  const [data, setData] = useState<GraphData | null>(null);

  useEffect(() => {
    fetch("/api/graph-data").then(r => r.json()).then(setData);
  }, []);

  useEffect(() => {
    if (!data || !svgRef.current) return;
    const svg = svgRef.current;
    const W = svg.clientWidth || 800;
    const H = svg.clientHeight || 600;

    // Init positions
    const nodes: Node[] = data.nodes.map(n => ({
      ...n,
      x: Math.random() * W,
      y: Math.random() * H,
      vx: 0, vy: 0, fx: null, fy: null,
    }));

    const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
    const links = data.links.map(l => ({ source: nodeById[l.source] ?? l.source, target: nodeById[l.target] ?? l.target }));

    // Simple force simulation
    const simulate = () => {
      for (let iter = 0; iter < 300; iter++) {
        // Repulsion
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const dx = (nodes[j].x ?? 0) - (nodes[i].x ?? 0);
            const dy = (nodes[j].y ?? 0) - (nodes[i].y ?? 0);
            const d2 = dx * dx + dy * dy + 1;
            const f = -200 / d2;
            nodes[i].vx = (nodes[i].vx ?? 0) + f * dx;
            nodes[i].vy = (nodes[i].vy ?? 0) + f * dy;
            nodes[j].vx = (nodes[j].vx ?? 0) - f * dx;
            nodes[j].vy = (nodes[j].vy ?? 0) - f * dy;
          }
        }
        // Attraction (links)
        for (const lnk of links) {
          const s = lnk.source as Node, t = lnk.target as Node;
          if (!s.x || !t.x) continue;
          const dx = t.x - s.x, dy = t.y - s.y;
          const d = Math.sqrt(dx * dx + dy * dy) + 0.1;
          const f = (d - 80) * 0.05;
          s.vx = (s.vx ?? 0) + f * dx / d; s.vy = (s.vy ?? 0) + f * dy / d;
          t.vx = (t.vx ?? 0) - f * dx / d; t.vy = (t.vy ?? 0) - f * dy / d;
        }
        // Centering
        for (const n of nodes) {
          n.vx = ((n.vx ?? 0) + (W / 2 - (n.x ?? 0)) * 0.003) * 0.85;
          n.vy = ((n.vy ?? 0) + (H / 2 - (n.y ?? 0)) * 0.003) * 0.85;
          n.x = Math.max(20, Math.min(W - 20, (n.x ?? W / 2) + (n.vx ?? 0)));
          n.y = Math.max(20, Math.min(H - 20, (n.y ?? H / 2) + (n.vy ?? 0)));
        }
      }
    };
    simulate();

    // Render SVG
    svg.innerHTML = "";
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svg.appendChild(g);

    for (const lnk of links) {
      const s = lnk.source as Node, t = lnk.target as Node;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(s.x)); line.setAttribute("y1", String(s.y));
      line.setAttribute("x2", String(t.x)); line.setAttribute("y2", String(t.y));
      line.setAttribute("stroke", "#30363d"); line.setAttribute("stroke-width", "1");
      g.appendChild(line);
    }

    for (const n of nodes) {
      const grp = document.createElementNS("http://www.w3.org/2000/svg", "g");
      grp.setAttribute("cursor", "pointer");

      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String(n.x)); circle.setAttribute("cy", String(n.y));
      circle.setAttribute("r", String(R[n.type] ?? 5));
      circle.setAttribute("fill", COLORS[n.type] ?? "#888");
      grp.appendChild(circle);

      const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", String((n.x ?? 0) + (R[n.type] ?? 5) + 4));
      text.setAttribute("y", String((n.y ?? 0) + 4));
      text.setAttribute("fill", "#c9d1d9"); text.setAttribute("font-size", "10");
      text.textContent = n.label.length > 22 ? n.label.slice(0, 20) + "…" : n.label;
      grp.appendChild(text);

      grp.addEventListener("mouseenter", () => setInfo(n));
      grp.addEventListener("mouseleave", () => setInfo(null));
      g.appendChild(grp);
    }
  }, [data]);

  return (
    <div className="relative w-full h-full">
      <svg ref={svgRef} className="w-full h-full" style={{ background: "#0d1117" }} />
      {info && (
        <div className="absolute top-4 left-4 rounded-lg border border-gray-700 px-3 py-2 text-xs" style={{ background: "#161b22" }}>
          <p className="font-medium text-gray-200">{info.label}</p>
          <p className="text-gray-500">{info.type} · {info.src} source(s)</p>
        </div>
      )}
      {!data && <p className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-gray-600 text-sm">Chargement…</p>}
    </div>
  );
}
