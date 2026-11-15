import {useEffect,useRef,useState} from 'react';
import {AlertTriangle,Bell,Check,X} from 'lucide-react';

export type PhoneAlert={id:string;status:string;message:string;simulated?:boolean;test?:boolean};

export default function PhoneAlerts({alert,active}:{alert:PhoneAlert|null;active:boolean}){
  const [current,setCurrent]=useState<PhoneAlert|null>(null);
  const [permission,setPermission]=useState('Notification' in window?Notification.permission:'unavailable');
  const [notice,setNotice]=useState('');
  const last=useRef(''),desktop=useRef<Notification|null>(null);
  function show(next:PhoneAlert){
    setCurrent(next);
    if(last.current===next.id)return;
    last.current=next.id;
    try{navigator.vibrate?.([180,100,180])}catch{}
    if('Notification' in window&&Notification.permission==='granted'){
      try{
        desktop.current?.close();
        desktop.current=new Notification(next.test?'Palladium · Test alert':next.simulated?'Palladium · Simulated review alert':'Palladium · Review requested',{
          body:next.test?'Phone demo notifications are working.':next.message,tag:'palladium-phone-'+next.id,
        });
        desktop.current.onclick=()=>{window.focus();desktop.current?.close()};
      }catch{setNotice('This browser supports in-page alerts only. Keep this page open.')}
    }
  }
  useEffect(()=>{if(alert)show(alert)},[alert]);
  useEffect(()=>()=>desktop.current?.close(),[]);
  async function enable(){
    try{setPermission(await Notification.requestPermission())}catch{setNotice('System notifications are unavailable here. In-page alerts remain on.')}
  }
  return <section className="phone-alerts" aria-label="Phone notifications">
    <div className="phone-alert-heading"><Bell size={20}/><div><strong>Phone notifications</strong><p>{active?'Listening for flags from this camera.':'Start the camera to receive its incident flags.'}</p></div><span className="pill">In-page alerts on</span></div>
    <p className="fine">A flagged event appears here immediately while surrounding video is saved. Human review is required. No officers are contacted.</p>
    <div className="phone-alert-actions">{permission==='default'&&<button onClick={enable}>Enable system notifications</button>}<button onClick={()=>show({id:'test-'+Date.now(),status:'test',message:'This is a test notification. No incident was detected.',test:true})}><Bell size={15}/>Test phone alert</button></div>
    <p className="fine" role="status">{notice|| (permission==='granted'?'System notifications allowed. Delivery depends on your browser.':permission==='denied'?'System notifications are blocked. In-page alerts still work.':'Keep this page open and your phone awake. Vibration is used where supported.')}</p>
    {current&&<article className="phone-alert-card" role="alert" aria-live="assertive"><div>{current.test?<Bell size={23}/>:<AlertTriangle size={23}/>}<strong>{current.test?'Test notification':current.simulated?'Simulated incident flag':'Possible incident flagged'}</strong><button className="text-button" onClick={()=>setCurrent(null)} aria-label="Dismiss phone alert"><X size={18}/></button></div><p>{current.message}</p><span>{current.test?'Demo test only · no incident created':current.status==='saved'?<><Check size={15}/>Video saved · reviewer assessment required</>:current.status==='failed'?'Video could not be saved · check the review workspace':'Saving surrounding video · reviewer assessment required'}</span></article>}
  </section>
}
