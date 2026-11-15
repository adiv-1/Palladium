import {BENIGN_IDS, LABEL, THREAT_IDS} from './debate';
import type {DebateDecision, DebateResult} from './debate';
import {Tag} from './Panels';
import './brief.css';

const DECISION_TEXT: Record<DebateDecision, string> = {escalate: 'Escalate', downgrade: 'Downgraded', suppress: 'Suppressed'};
const BANNER: Record<DebateResult['advocate'], string> = {lost: "Devil's advocate lost", draw: "Devil's advocate: draw", won: "Devil's advocate won"};

function Bars({title, ids, values, side}: {title: string; ids: readonly string[]; values: Record<string, number>; side: 'threat' | 'advocate'}) {
  return (
    <div className={`brief-col brief-${side}`}>
      <h4>{title}</h4>
      {ids.map((id) => {
        const p = values[id] ?? 0;
        return (
          <div className="brief-row" key={id} title={`${LABEL[id]}: ${Math.round(p * 100)}%`}>
            <span className="brief-q">{LABEL[id]}</span>
            <span className="brief-track"><span className="brief-fill" style={{width: `${Math.round(p * 100)}%`}} /></span>
            <span className="brief-pct">{Math.round(p * 100)}%</span>
          </div>
        );
      })}
    </div>
  );
}

export function BriefCard({result, loading}: {result: DebateResult | null; loading: boolean}) {
  if (loading || !result) {
    return (
      <section className="brief-card brief-loading" aria-busy="true" aria-live="polite">
        <h3>Agent Consensus Brief</h3>
        <p className="brief-wait">{loading ? 'Threat agent and devil’s advocate are debating…' : 'No debate yet.'}</p>
      </section>
    );
  }
  const latency = result.latency_ms < 10 ? result.latency_ms.toFixed(1) : String(Math.round(result.latency_ms));
  return (
    <section className={`brief-card brief-${result.decision}`} aria-live="polite">
      <header className="brief-head">
        <h3>Agent Consensus Brief</h3>
        <span className={`brief-badge brief-badge-${result.decision}`}>{DECISION_TEXT[result.decision]}</span>
      </header>
      <div className={`brief-banner brief-banner-${result.advocate}`}>{BANNER[result.advocate]}</div>
      <ol className="brief-bullets">
        {result.brief.bullets.map((b, i) => <li key={i}>{b}</li>)}
      </ol>
      <div className="brief-cols">
        <Bars title="Threat" ids={THREAT_IDS} values={result.threat} side="threat" />
        <Bars title="Devil's advocate" ids={BENIGN_IDS} values={result.benign} side="advocate" />
      </div>
      <footer className="brief-foot">
        <span className="brief-chip" title={result.note ?? undefined}>debate {latency} ms · {result.source}</span>
        {result.simulated && <><Tag kind="simulated" title="Motion features were staged for a demo, not measured by a detector." /><span className="brief-sim">Simulated features</span></>}
        {result.note && <span className="brief-note">{result.note}</span>}
      </footer>
    </section>
  );
}
