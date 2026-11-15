import {Component,type ReactNode} from 'react';
import {Logo} from './Logo';

// A stale lazy chunk after an app update should offer recovery, not a blank screen.
export default class WorkspaceBoundary extends Component<{children:ReactNode},{failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){
    if(this.state.failed)return <main className="login"><div className="brand"><Logo size={40}/><strong>Palladium</strong></div><h1>This view could not load.</h1><p>The app may have updated, or a required module is unavailable. Reload to open the current version.</p><button className="primary" onClick={()=>location.reload()}>Reload workspace</button></main>;
    return this.props.children;
  }
}
