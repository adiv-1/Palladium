import {useActivityBundle} from './campusData';
import ActivityForecast from './ActivityForecast';
export default function ActivityMap({mode,onModeChange,planningOnly=false}: {planningOnly?:boolean;mode?:'posts'|'routes';onModeChange?:(mode:'explore'|'routes'|'posts'|'history')=>void}) {
  const {bundle,error,retry}=useActivityBundle();
  if(error)return <section className="panel unavailable"><h1>Campus analysis unavailable</h1><p>{error}</p><button onClick={retry}>Retry analysis</button></section>;
  if(!bundle)return <section className="panel unavailable" role="status"><h1>Loading campus analysis</h1><p>Preparing the saved public-report snapshot and walking network.</p></section>;
  return <ActivityForecast bundle={bundle} focusMode={mode} onModeChange={onModeChange} planningOnly={planningOnly}/>;
}
