// Runs the What-If Monte Carlo off the main thread. The map data is sent once ('init'), then each
// 'run' only carries the slider values. Progress and the final result are posted back by run id.
import {buildGraph, type Past, type Shift} from './analysis';
import {Router, simulate, type SimInput, type SimParams, type SimResult} from './whatif';
import type {Building, LngLat} from './types';

export type WorkerIn =
  | {type: 'init'; graph: {nodes: LngLat[]; edges: [number, number, number][]}; buildings: Building[]; past: Past[]; boundary: LngLat[]}
  | {type: 'run'; id: number; params: SimParams; shift: Shift};
export type WorkerOut =
  | {type: 'progress'; id: number; fraction: number; label: string}
  | {type: 'result'; id: number; result: SimResult}
  | {type: 'error'; id: number; message: string};

let base: Omit<SimInput, 'shift'> | null = null;
let router: Router | null = null; // shortest-path trees survive across runs: only the graph matters

self.onmessage = (e: MessageEvent<WorkerIn>) => {
  const m = e.data;
  if (m.type === 'init') {
    const graph = buildGraph(m.graph);
    base = {graph, buildings: m.buildings, past: m.past, boundary: m.boundary};
    router = new Router(graph);
    return;
  }
  const post = (msg: WorkerOut) => (self as unknown as Worker).postMessage(msg);
  if (!base) {post({type: 'error', id: m.id, message: 'Simulator not initialised'}); return;}
  try {
    const result = simulate({...base, shift: m.shift}, m.params, (fraction, label) => post({type: 'progress', id: m.id, fraction, label}), router ?? undefined);
    post({type: 'result', id: m.id, result});
  } catch (err) {
    post({type: 'error', id: m.id, message: err instanceof Error ? err.message : String(err)});
  }
};
