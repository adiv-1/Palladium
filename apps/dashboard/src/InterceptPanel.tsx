import type {InterceptPlan} from './intercept';
import './intercept.css';

type Props = {plan: InterceptPlan; onApprove: () => void; onDismiss: () => void; approved: boolean};

// Human-in-the-loop card for the intercept plan. Nothing moves until the operator approves.
export function InterceptPanel({plan, onApprove, onDismiss, approved}: Props) {
  const v = plan.victimRoute;
  return (
    <section className={`icpt${approved ? ' icpt-approved' : ''}`} aria-label="Intercept vector">
      <header className="icpt-head">
        <div>
          <h3>Intercept vector</h3>
          <p className="icpt-sub">Heuristic prediction · simulated heading</p>
        </div>
        <span className="icpt-tag">{plan.horizonS} s horizon</span>
      </header>

      <div className="icpt-block">
        <h4>Predicted exit</h4>
        <p className="icpt-exit">{plan.exit ? plan.exit.label : 'No exit predicted'}</p>
        <p className="icpt-note">Branches: {plan.predicted.map((b) => `${Math.round(b.prob * 100)}%`).join(' / ') || 'none'} of simulated walks. Not a calibrated probability.</p>
      </div>

      <div className="icpt-block">
        <h4>Timeline</h4>
        <ol className="icpt-timeline">
          {plan.timeline.map((e, i) => (
            <li key={i}><span className="icpt-t">T+{Math.round(e.tS)}s</span><span>{e.text}</span></li>
          ))}
        </ol>
      </div>

      <div className="icpt-block">
        <h4>Choke points</h4>
        {plan.chokePoints.length ? (
          <ul className="icpt-list">
            {plan.chokePoints.map((c) => (
              <li key={c.node}>
                <b>{c.unit.callsign}</b> <span className="icpt-muted">({c.unit.mode})</span>
                <span className="icpt-margin">arrives {Math.round(c.marginS)} s early</span>
              </li>
            ))}
          </ul>
        ) : <p className="icpt-warn">No available unit can beat the suspect to the predicted path. Escalate to dispatch.</p>}
      </div>

      {v && (
        <div className="icpt-block icpt-haven">
          <h4>Victim safe haven</h4>
          <p>Move to <b>{v.to.name}</b>, about {Math.max(1, Math.round(v.minutes))} min on foot. {v.avoidsSuspect ? 'Route avoids the predicted suspect path.' : 'Route could not fully avoid the predicted suspect path.'}</p>
        </div>
      )}

      {plan.cameraHandoff.length > 0 && (
        <div className="icpt-block">
          <h4>Camera hand-off</h4>
          <div className="icpt-chips">
            {plan.cameraHandoff.map((c) => <span key={c.cameraId} className="icpt-chip" title={c.near}>{c.cameraId} <i>T+{c.etaS}s</i></span>)}
          </div>
        </div>
      )}

      {approved ? (
        <div className="icpt-confirm" role="status" aria-live="polite">Intercept approved. Units are moving to their choke points.</div>
      ) : (
        <div className="icpt-actions">
          <button type="button" className="icpt-btn icpt-primary" onClick={onApprove}>Approve intercept</button>
          <button type="button" className="icpt-btn icpt-ghost" onClick={onDismiss}>Dismiss</button>
        </div>
      )}
      <p className="icpt-foot">Escape heading is a simulated input; in deployment it comes from the camera track. Human approval required.</p>
    </section>
  );
}
