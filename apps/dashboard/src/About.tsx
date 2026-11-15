import {useEffect, useMemo, useRef, useState} from 'react';
import {QRCodeSVG} from 'qrcode.react';
import {Nav, studentLink} from './Nav';
import {Logo} from './Logo';
import {Tag} from './Panels';
import './about.css';

/* ---------------- diagram model ---------------- */

type Lane = 'live' | 'trends';
type NodeKind = 'std' | 'human' | 'out';

interface DNode {
  id: string;
  x: number; y: number; w: number; h: number;
  title: string;
  sub: string[];
  kind?: NodeKind;
  lane: Lane;
}

const NODES: DNode[] = [
  {id: 'cctv', x: 30, y: 107, w: 130, h: 90, title: 'CCTV feeds', sub: ['campus cameras', 'sampled frames'], lane: 'live'},
  {id: 'detector', x: 200, y: 107, w: 170, h: 90, title: 'Detector agent', sub: ['YOLO11n · ByteTrack · rules', 'pose only on flagged tracks'], lane: 'live'},
  {id: 'debate', x: 410, y: 87, w: 200, h: 130, title: 'Debate', sub: [], lane: 'live'},
  {id: 'verifier', x: 650, y: 107, w: 150, h: 90, title: 'Verifier agent', sub: ['Qwen3-VL', 'narrow yes/no check'], lane: 'live'},
  {id: 'admin', x: 840, y: 107, w: 140, h: 90, title: 'Admin approval', sub: ['HUMAN IN THE LOOP', 'approve or dismiss'], kind: 'human', lane: 'live'},
  {id: 'dispatch', x: 1020, y: 107, w: 150, h: 90, title: 'Dispatch agent', sub: ['Claude picks units by', 'position/ETA, urgency,', 'message text'], lane: 'live'},
  {id: 'alert', x: 1230, y: 40, w: 180, h: 70, title: 'Alert agent', sub: ['DPS units + app users', 'within 200 m'], kind: 'out', lane: 'live'},
  {id: 'intercept', x: 1230, y: 125, w: 180, h: 70, title: 'Interception agent', sub: ['escape-heading projection,', 'choke points, safe-haven route'], kind: 'out', lane: 'live'},
  {id: 'report', x: 1230, y: 210, w: 180, h: 70, title: 'Report agent', sub: ['incident report'], kind: 'out', lane: 'live'},

  {id: 'history', x: 30, y: 485, w: 150, h: 80, title: 'Past incidents', sub: ['DPS log, geocoded'], lane: 'trends'},
  {id: 'analyst', x: 230, y: 485, w: 170, h: 80, title: 'Trend analyst', sub: ['hotspots, time-of-day', 'patterns'], lane: 'trends'},
  {id: 'ambassador', x: 470, y: 405, w: 250, h: 70, title: 'Ambassador planner', sub: ['covers the most past risk', 'patrol and ambassador posts'], lane: 'trends'},
  {id: 'saferoute', x: 470, y: 490, w: 250, h: 70, title: 'Safe-route agent', sub: ['risk-weighted Dijkstra', 'on OSM walkways'], lane: 'trends'},
  {id: 'whatif', x: 470, y: 575, w: 250, h: 70, title: 'What-if wargame', sub: ['simulated', 'late-night walks'], lane: 'trends'},
  {id: 'map', x: 800, y: 470, w: 230, h: 110, title: 'Public map', sub: ['Get Somewhere', 'Plan dashboard'], kind: 'out', lane: 'trends'},
];

interface DEdge {id: string; d: string; lane: Lane | 'fb'}

const EDGES: DEdge[] = [
  {id: 'e1', d: 'M160,152 L200,152', lane: 'live'},
  {id: 'e2', d: 'M370,152 L410,152', lane: 'live'},
  {id: 'e3', d: 'M610,152 L650,152', lane: 'live'},
  {id: 'e4', d: 'M800,152 L840,152', lane: 'live'},
  {id: 'e5', d: 'M980,152 L1020,152', lane: 'live'},
  {id: 'e6', d: 'M1170,152 C1200,152 1200,75 1230,75', lane: 'live'},
  {id: 'e7', d: 'M1170,152 C1200,152 1200,160 1230,160', lane: 'live'},
  {id: 'e8', d: 'M1170,152 C1200,152 1200,245 1230,245', lane: 'live'},
  {id: 'fb', d: 'M1320,280 L1320,347 Q1320,355 1312,355 L113,355 Q105,355 105,363 L105,485', lane: 'fb'},
  {id: 't1', d: 'M180,525 L230,525', lane: 'trends'},
  {id: 't2', d: 'M400,525 C435,525 435,440 470,440', lane: 'trends'},
  {id: 't3', d: 'M400,525 L470,525', lane: 'trends'},
  {id: 't4', d: 'M400,525 C435,525 435,610 470,610', lane: 'trends'},
  {id: 't5', d: 'M720,440 C760,440 760,525 800,525', lane: 'trends'},
  {id: 't6', d: 'M720,525 L800,525', lane: 'trends'},
  {id: 't7', d: 'M720,610 C760,610 760,525 800,525', lane: 'trends'},
];

const nodeById = (id: string) => NODES.find(n => n.id === id)!;

/* ---------------- simulation script ---------------- */

interface Step {
  node: string;
  edge?: string;
  lane: Lane;
  title: string;
  detail: string;
  example?: string;
}

const STEPS: Step[] = [
  {node: 'cctv', lane: 'live', title: 'Camera feed', detail: 'The camera covering Lyon Recreation Center streams sampled frames to the detector. Audio is never captured.'},
  {node: 'detector', edge: 'e1', lane: 'live', title: 'Detector flags a possible altercation', detail: 'Two people in close contact with rapid movement for 3.5 s. Pose estimation runs only on these two flagged tracks.', example: 'Example: 3.5 s, 2 tracks'},
  {node: 'debate', edge: 'e2', lane: 'live', title: 'Debate: Threat agent vs Devil’s Advocate', detail: 'Both agents get typed, structured questions in parallel and write an Agent Consensus Brief. The advocate argues it is horseplay and loses.', example: 'Example: threat 0.82 vs advocate 0.21'},
  {node: 'verifier', edge: 'e3', lane: 'live', title: 'Verifier asks one narrow question', detail: 'Qwen3-VL looks at the flagged clip and answers a single yes/no question: is there physical aggression between these two people?', example: 'Example: answer "yes"'},
  {node: 'admin', edge: 'e4', lane: 'live', title: 'Admin approves', detail: 'Nothing is dispatched until a human reviews the brief and the clip. The admin approves in the dashboard.'},
  {node: 'dispatch', edge: 'e5', lane: 'live', title: 'Dispatch agent picks units', detail: 'Claude weighs unit position and ETA, urgency and wording. It assigns two nearby units and drafts their messages. Priority P1.', example: 'Example: Trojan 22 + Trojan 31, P1'},
  {node: 'alert', edge: 'e6', lane: 'live', title: 'Alert agent notifies people nearby', detail: 'DPS units and student-app users inside the 200 m geofence get the alert.', example: 'Example: 8 DPS units and 41 app users'},
  {node: 'intercept', edge: 'e7', lane: 'live', title: 'Interception agent projects the escape', detail: 'It projects the escape heading, places choke points and plans a safe-haven route for the victim.', example: 'Example: west exit in 45 s, 2 choke points'},
  {node: 'report', edge: 'e8', lane: 'live', title: 'Report agent files the incident', detail: 'A structured incident report is created from the brief, the verifier answer and the approval.'},
  {node: 'history', edge: 'fb', lane: 'trends', title: 'Confirmed incident joins the history', detail: 'The confirmed incident feeds back into Past incidents, so tomorrow’s trends include tonight.', example: 'Example: +1 incident near Lyon Rec'},
  {node: 'analyst', edge: 't1', lane: 'trends', title: 'Trend analyst re-reads the history', detail: 'Hotspots and time-of-day patterns are recomputed with the new point.', example: 'Example: evening hotspot weight rises'},
  {node: 'ambassador', edge: 't2', lane: 'trends', title: 'Ambassador planner updates', detail: 'The planner moves the suggested ambassador posts a little toward the new hotspot.', example: 'Example: one post shifts about 40 m'},
  {node: 'map', edge: 't5', lane: 'trends', title: 'Public map refreshes', detail: 'Get Somewhere routes and the Plan dashboard pick up the new risk weights. The loop is closed.'},
];

const INTRO = 'A scripted example: an altercation near Lyon Recreation Center. Press Play to follow the alert through the live lane, then into the trends lane. All numbers in the walkthrough are examples, not real data.';

/* ---------------- agent cards ---------------- */

type Tone = 'ok' | 'warn' | 'sim';
interface AgentCard {name: string; does: string; tech: string; status: string; tone: Tone; lane: Lane}

const AGENTS: AgentCard[] = [
  {name: 'Detector agent', does: 'Watches camera feeds and flags suspicious interactions. Pose estimation runs only on flagged tracks.', tech: 'YOLO11n, ByteTrack, rules', status: 'Implemented (CPU-tested)', tone: 'ok', lane: 'live'},
  {name: 'Threat vs Devil’s Advocate debate', does: 'Two agents answer typed, structured questions in parallel and produce an Agent Consensus Brief.', tech: 'Rules gate, Claude gate, vendor Jev adapter', status: 'Built in PR 2 (rules gate tested; Claude gate untested; vendor Jev adapter is a placeholder)', tone: 'warn', lane: 'live'},
  {name: 'Verifier agent', does: 'Asks one narrow yes/no question about the flagged clip before a human sees it.', tech: 'Qwen3-VL', status: 'Implemented (needs GPU, untested here)', tone: 'warn', lane: 'live'},
  {name: 'Admin approval', does: 'A human reviews the brief and approves or dismisses. Nothing is dispatched without it.', tech: 'Dashboard queue and detail panel', status: 'Implemented', tone: 'ok', lane: 'live'},
  {name: 'Dispatch agent', does: 'Chooses units by position and ETA, sets urgency and writes the messages.', tech: 'Claude, with a rules fallback', status: 'Implemented (rules fallback tested; Claude path untested, no API key)', tone: 'warn', lane: 'live'},
  {name: 'Alert agent', does: 'Notifies DPS units and student-app users within 200 m of the incident.', tech: 'Geofence, BroadcastChannel across tabs', status: 'Simulated delivery (200 m geofence works across student-app tabs)', tone: 'sim', lane: 'live'},
  {name: 'Interception agent', does: 'Projects escape headings, proposes choke points and a safe-haven route for the victim.', tech: 'Heading projection, heuristics', status: 'Built in PR 2 (heuristic, simulated heading)', tone: 'sim', lane: 'live'},
  {name: 'Report agent', does: 'Files a structured incident report once the incident is approved.', tech: 'Templated report', status: 'Placeholder values for alert testing', tone: 'sim', lane: 'live'},
  {name: 'Trend analyst', does: 'Finds hotspots and time-of-day patterns in past incidents.', tech: 'Geocoded incident aggregation', status: 'Algorithmic, on simulated history until a DPS log is added', tone: 'sim', lane: 'trends'},
  {name: 'Ambassador planner', does: 'Suggests where ambassadors or patrols should stand.', tech: 'Weighted k-means', status: 'Algorithmic, on simulated history until a DPS log is added', tone: 'sim', lane: 'trends'},
  {name: 'Safe-route agent', does: 'Finds walking routes that trade a little distance for less risk.', tech: 'Risk-weighted Dijkstra on OSM walkways', status: 'Algorithmic, on simulated history until a DPS log is added', tone: 'sim', lane: 'trends'},
  {name: 'What-if wargame', does: 'Tests how staffing or lighting changes affect late-night walks.', tech: 'Monte Carlo simulation', status: 'Built in PR 2 (scenario simulator, stated assumptions)', tone: 'warn', lane: 'trends'},
  {name: 'Public map', does: 'Shows risk-aware routes and the Trends dashboard to the public.', tech: 'React, interactive map layers', status: 'Implemented', tone: 'ok', lane: 'trends'},
];

const GUARDRAILS: [string, string][] = [
  ['No face recognition', 'The system tracks anonymous boxes, never identities. There is no face matching and no watchlist.'],
  ['Human approves dispatch', 'An admin approves every dispatch. Agents recommend; people decide.'],
  ['Audio off', 'Microphones are not used. Detection works from video only.'],
  ['Retention limits', 'Footage and clips are kept only as long as review needs them.'],
  ['Audit log', 'Each agent step, approval and dismissal is recorded so decisions can be reviewed.'],
  ['Honest labels', 'Every number is tagged Real, Demo, Simulated, Estimate or Live, so nothing simulated passes as real.'],
];

/* ---------------- component ---------------- */

const SPEEDS = [0.5, 1, 2];
const VB_W = 1440;

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  return reduced;
}

function NodeShape({n, active, done}: {n: DNode; active: boolean; done: boolean}) {
  const cls = `nd ${n.kind ?? 'std'} ${n.lane}${active ? ' active' : ''}${done ? ' done' : ''}`;
  return (
    <g className={cls} data-node={n.id}>
      <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={12} />
      <text className="t" x={n.x + n.w / 2} y={n.y + 22} textAnchor="middle">{n.title}</text>
      {n.id === 'debate' ? (
        <>
          <rect className="mini threat" x={n.x + 10} y={n.y + 34} width={86} height={34} rx={7} />
          <text className="s" x={n.x + 53} y={n.y + 55} textAnchor="middle">Threat agent</text>
          <rect className="mini adv" x={n.x + 104} y={n.y + 34} width={86} height={34} rx={7} />
          <text className="s" x={n.x + 147} y={n.y + 55} textAnchor="middle">Devil&#8217;s Advocate</text>
          <text className="s" x={n.x + 100} y={n.y + 90} textAnchor="middle">typed questions, in parallel</text>
          <text className="s hi" x={n.x + 100} y={n.y + 108} textAnchor="middle">Agent Consensus Brief</text>
        </>
      ) : n.sub.map((line, i) => (
        <text key={i} className={`s${n.kind === 'human' && i === 0 ? ' hi' : ''}`} x={n.x + n.w / 2} y={n.y + 42 + i * 14} textAnchor="middle">{line}</text>
      ))}
    </g>
  );
}

export default function About() {
  const [idx, setIdx] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const reduced = useReducedMotion();
  const link = useMemo(() => studentLink(), []);

  const edgeRefs = useRef<Record<string, SVGPathElement | null>>({});
  const packetRef = useRef<SVGGElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const last = STEPS.length - 1;
  const step = idx >= 0 ? STEPS[idx] : null;
  const activeNode = step?.node ?? '';
  const activeEdge = step?.edge ?? '';
  const visitedNodes = useMemo(() => new Set(STEPS.slice(0, Math.max(idx, 0)).map(s => s.node)), [idx]);

  // auto-advance
  useEffect(() => {
    if (!playing) return;
    if (idx >= last) { setPlaying(false); return; }
    const t = window.setTimeout(() => setIdx(i => Math.min(i + 1, last)), 2000 / speed);
    return () => window.clearTimeout(t);
  }, [playing, idx, speed, last]);

  // travelling packet
  useEffect(() => {
    const g = packetRef.current;
    if (!g) return;
    const path = step?.edge ? edgeRefs.current[step.edge] : null;
    if (!path || reduced) { g.style.opacity = '0'; return; }
    const len = path.getTotalLength();
    const dur = Math.max(450, 1100 / speed);
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / dur);
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const p = path.getPointAtLength(len * e);
      g.setAttribute('transform', `translate(${p.x} ${p.y})`);
      g.style.opacity = k < 1 ? '1' : '0';
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    g.style.opacity = '1';
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [idx, step, speed, reduced]);

  // keep the active node in view inside the diagram container
  useEffect(() => {
    const box = scrollRef.current;
    if (!box || !step) return;
    const n = nodeById(step.node);
    const svgW = box.scrollWidth;
    const cx = ((n.x + n.w / 2) / VB_W) * svgW;
    box.scrollTo({left: Math.max(0, cx - box.clientWidth / 2), behavior: reduced ? 'auto' : 'smooth'});
  }, [step, reduced]);

  const next = () => { setPlaying(false); setIdx(i => Math.min(i + 1, last)); };
  const restart = () => { setPlaying(false); setIdx(-1); };
  const togglePlay = () => {
    if (playing) { setPlaying(false); return; }
    if (idx >= last) setIdx(0);
    else if (idx < 0) setIdx(0);
    setPlaying(true);
  };

  const finished = idx >= last;

  return (
    <>
      <header className="strip">
        <Nav active="about" />
        <div className="chips">
          <span className="chip"><span className="chip-label">Two flows</span><strong>Respond + Plan</strong></span>
          <span className="chip"><span className="chip-label">Dispatch</span><strong>Human approved</strong></span>
          <span className="chip"><span className="chip-label">Faces</span><strong>Never identified</strong></span>
        </div>
      </header>

      <main className="about" id="about-root">
        <div className="about-inner">
          <section className="hero">
            <div className="hero-logo"><Logo variant="shield" size={64} /></div>
            <p className="eyebrow">Architecture</p>
            <h1>How Palladium works</h1>
            <p className="lede">
              Palladium runs two flows. The <b>live flow</b> turns camera footage into a reviewed, approved dispatch in seconds. The <b>trends flow</b> learns from past incidents to plan patrols and safer walking routes. A person approves every dispatch, and the system never uses face recognition or identifies anyone. Anything simulated is labelled as such.
            </p>
            <div className="hero-tags"><Tag kind="real" /><Tag kind="demo" /><Tag kind="simulated" /><Tag kind="estimate" /></div>
          </section>

          <section className="card sim" aria-labelledby="sim-h">
            <div className="sim-head">
              <div>
                <h2 id="sim-h">Architecture and simulation</h2>
                <p className="muted">Two lanes: respond now (live) and prevent next time (trends).</p>
              </div>
              <div className="controls" role="group" aria-label="Simulation controls">
                <button type="button" className="primary" onClick={togglePlay} aria-label={playing ? 'Pause simulation' : finished ? 'Replay simulation' : 'Run simulation'}>
                  {playing ? 'Pause' : finished ? 'Replay' : idx < 0 ? 'Run simulation' : 'Play'}
                </button>
                <button type="button" onClick={next} disabled={finished}>Step</button>
                <button type="button" onClick={restart} disabled={idx < 0 && !playing}>Restart</button>
                <div className="speed" role="radiogroup" aria-label="Speed">
                  {SPEEDS.map(s => (
                    <button key={s} type="button" role="radio" aria-checked={s === speed} className={s === speed ? 'on' : ''} onClick={() => setSpeed(s)}>{s}x</button>
                  ))}
                </div>
              </div>
            </div>

            <div className="diagram-scroll" ref={scrollRef} tabIndex={0} role="region" aria-label="Multi-agent architecture diagram, scrollable">
              <svg className="diagram" viewBox={`0 0 ${VB_W} 720`} role="img" aria-label="Architecture diagram. Live lane: CCTV feeds, Detector agent, Debate, Verifier agent, Admin approval, Dispatch agent, then Alert, Interception and Report agents. Trends lane: Past incidents, Trend analyst, then Ambassador planner, Safe-route agent and What-if wargame, then the Public map.">
                <defs>
                  <marker id="arr-live" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" style={{fill: 'var(--red)'}} /></marker>
                  <marker id="arr-trends" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" style={{fill: 'var(--green)'}} /></marker>
                  <marker id="arr-fb" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" style={{fill: 'var(--yellow)'}} /></marker>
                  <radialGradient id="pk" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" style={{stopColor: 'color-mix(in srgb, var(--yellow) 30%, var(--surface-strong))'}} /><stop offset="45%" style={{stopColor: 'var(--yellow)'}} /><stop offset="100%" style={{stopColor: 'var(--yellow)', stopOpacity: 0}} />
                  </radialGradient>
                </defs>

                <rect className="lane live" x={10} y={10} width={1420} height={320} rx={18} />
                <text className="lane-l live" x={30} y={36}>LIVE LANE · respond now</text>
                <rect className="lane trends" x={10} y={380} width={1420} height={320} rx={18} />
                <text className="lane-l trends" x={200} y={405}>TRENDS LANE · prevent next time</text>
                <text className="fb-l" x={700} y={347} textAnchor="middle">confirmed incidents feed back into history</text>

                {EDGES.map(e => (
                  <g key={e.id}>
                    <path className={`edge ${e.lane}`} d={e.d} ref={el => { edgeRefs.current[e.id] = el; }} />
                    <path className={`flow ${e.lane}${activeEdge === e.id ? ' on' : ''}`} d={e.d} markerEnd={`url(#arr-${e.lane})`} />
                  </g>
                ))}

                {NODES.map(n => <NodeShape key={n.id} n={n} active={n.id === activeNode} done={visitedNodes.has(n.id)} />)}

                <g className="legend" transform="translate(1080 430)">
                  <text className="s hi" x={0} y={0}>LEGEND</text>
                  <rect x={0} y={14} width={22} height={14} rx={4} className="lg-std" /><text className="s" x={32} y={26}>agent (software)</text>
                  <rect x={0} y={40} width={22} height={14} rx={4} className="lg-human" /><text className="s" x={32} y={52}>human decision</text>
                  <rect x={0} y={66} width={22} height={14} rx={4} className="lg-out" /><text className="s" x={32} y={78}>output / action</text>
                  <path d="M0,100 L22,100" className="lg-fb" /><text className="s" x={32} y={104}>feedback loop</text>
                  <path d="M0,124 L22,124" className="lg-live" /><text className="s" x={32} y={128}>live data flow</text>
                  <path d="M0,148 L22,148" className="lg-trends" /><text className="s" x={32} y={152}>trends data flow</text>
                </g>

                <g ref={packetRef} className="packet" style={{opacity: 0}}>
                  <circle r={16} fill="url(#pk)" />
                  <circle r={5.5} style={{fill: 'color-mix(in srgb, var(--yellow) 30%, var(--surface-strong))'}} />
                </g>
              </svg>
            </div>

            <div className="caption" aria-live="polite" aria-atomic="true">
              <div className="cap-meta">
                <span className={`lane-pill ${step?.lane ?? 'live'}`}>{step ? (step.lane === 'live' ? 'Live lane' : 'Trends lane') : 'Ready'}</span>
                <span className="muted">{step ? `Step ${idx + 1} of ${STEPS.length}` : `${STEPS.length} steps`}</span>
                {step?.example && <span className="ex-tag">{step.example}</span>}
              </div>
              <h3>{step ? step.title : 'Altercation near Lyon Recreation Center'}</h3>
              <p>{step ? step.detail : INTRO}</p>
              <ol className="dots" aria-label="Jump to step">
                {STEPS.map((s, i) => (
                  <li key={i}>
                    <button type="button" className={i === idx ? 'on' : i < idx ? 'past' : ''} aria-label={`Step ${i + 1}: ${s.title}`} aria-current={i === idx ? 'step' : undefined} onClick={() => { setPlaying(false); setIdx(i); }} />
                  </li>
                ))}
              </ol>
            </div>
          </section>

          <section aria-labelledby="agents-h">
            <div className="sec-head">
              <h2 id="agents-h">The agents</h2>
              <p className="muted">Status is stated plainly: what is built, what is tested and what is still a placeholder.</p>
            </div>
            <div className="agent-grid">
              {AGENTS.map(a => (
                <article key={a.name} className={`card agent ${a.lane}`}>
                  <header>
                    <h3>{a.name}</h3>
                    <span className={`lane-pill ${a.lane}`}>{a.lane === 'live' ? 'Live' : 'Trends'}</span>
                  </header>
                  <p>{a.does}</p>
                  <p className="tech"><span>Tech</span> {a.tech}</p>
                  <p className={`status ${a.tone}`}><i aria-hidden="true" />{a.status}</p>
                </article>
              ))}
            </div>
          </section>

          <section aria-labelledby="guard-h">
            <div className="sec-head"><h2 id="guard-h">Guardrails</h2></div>
            <ul className="guard">
              {GUARDRAILS.map(([h, p]) => (
                <li key={h} className="card"><strong>{h}</strong><span>{p}</span></li>
              ))}
            </ul>
          </section>

          <section className="card student" aria-labelledby="stu-h">
            <div className="qr">
              <QRCodeSVG value={link} size={136} bgColor="#ffffff" fgColor="#121417" marginSize={2} title="QR code for the Palladium student app" />
            </div>
            <div className="stu-body">
              <h2 id="stu-h">Try the student app</h2>
              <a className="stu-link" href={link} target="_blank" rel="noreferrer">{link}</a>
              <ol>
                <li>Scan the code, or open the link, on a phone or in another tab.</li>
                <li>Allow location so the 200 m alert geofence knows where you are.</li>
                <li>Approve an incident on the dashboard and watch the alert arrive in the app.</li>
              </ol>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
