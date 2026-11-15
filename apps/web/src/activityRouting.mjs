// Pure local routing over the preloaded OSM snapshot. Costs are report-activity
// indices, never probabilities of harm. No external geocoder or routing API.
export function distance(a,b){
  const x=(a[0]-b[0])*92300,y=(a[1]-b[1])*111320;return Math.hypot(x,y);
}
export function slotFor(date,hour,minutes=0){
  const day=new Date(date+'T12:00:00Z').getUTCDay();
  const elapsed=hour*60+minutes;
  return (((day+6)%7+Math.floor(elapsed/1440))%7)*8+Math.floor((elapsed%1440)/180);
}
export function makeNetwork(graph){
  const adjacency=graph.nodes.map(()=>[]),allowed=new Set(graph.largest_component);
  graph.edges.forEach((edge,id)=>{const [u,v]=edge;adjacency[u].push([v,id]);adjacency[v].push([u,id])});
  return {graph,adjacency,allowed};
}
function snap(network,point){
  let best=-1,meters=Infinity;
  for(const i of network.allowed){const d=distance(network.graph.nodes[i],point);if(d<meters){best=i;meters=d}}
  if(best<0||meters>100)throw Error('That point is more than 100 m from the connected walking network. Choose a nearby mapped path.');
  return {node:best,meters};
}
class Heap{
  a=[];
  push(item){let i=this.a.push(item)-1;while(i){const p=(i-1)>>1;if(this.a[p][0]<=item[0])break;this.a[i]=this.a[p];i=p}this.a[i]=item}
  pop(){const root=this.a[0],last=this.a.pop();if(this.a.length){let i=0;while(i*2+1<this.a.length){let c=i*2+1;if(c+1<this.a.length&&this.a[c+1][0]<this.a[c][0])c++;if(this.a[c][0]>=last[0])break;this.a[i]=this.a[c];i=c}this.a[i]=last}return root}
}
function search(network,start,end,bundle,date,hour,family,beta){
  const {graph,adjacency}=network,n=graph.nodes.length;
  const cost=new Float64Array(n).fill(Infinity),minutes=new Float64Array(n),prev=new Int32Array(n).fill(-1),via=new Int32Array(n).fill(-1),heap=new Heap();
  cost[start]=0;heap.push([0,start]);
  while(heap.a.length){
    const [score,u]=heap.pop();if(score!==cost[u])continue;if(u===end)break;
    for(const [v,id] of adjacency[u]){
      // Generate candidates with fixed departure-time costs (valid nonnegative
      // Dijkstra weights), then score each at actual estimated traversal times.
      const edge=graph.edges[id],tau=edge[2]/80,slot=slotFor(date,hour),row=bundle.weekly[family][slot][edge[3]];
      const q=row[4]/Math.max(.001,bundle.density_thresholds[family].high),support=bundle.model.recent_support[family==='person'?0:1][edge[3]];
      const candidate=score+tau*(1+beta*(q+(support<5?.35:0)));
      if(candidate<cost[v]){cost[v]=candidate;minutes[v]=minutes[u]+tau;prev[v]=u;via[v]=id;heap.push([candidate,v])}
    }
  }
  if(!Number.isFinite(cost[end]))throw Error('No connected walking path between these points. Try another nearby location.');
  const nodes=[],edgeIds=[];let u=end;
  while(u!==start){nodes.push(u);edgeIds.push(via[u]);u=prev[u]}
  nodes.push(start);nodes.reverse();edgeIds.reverse();
  let elapsed=0,activity=0,sparse=0;const streets=[];
  for(const id of edgeIds){const e=graph.edges[id],tau=e[2]/80,slot=slotFor(date,hour,elapsed+tau/2),q=bundle.weekly[family][slot][e[3]][4]/Math.max(.001,bundle.density_thresholds[family].high);
    activity+=tau*q;if(bundle.model.recent_support[family==='person'?0:1][e[3]]<5)sparse+=tau;
    const name=graph.names[e[4]];if(streets.at(-1)!==name)streets.push(name);elapsed+=tau;
  }
  return {coordinates:nodes.map(i=>graph.nodes[i]),minutes:elapsed,meters:elapsed*80,activity,sparseFraction:elapsed?sparse/elapsed:0,streets};
}
export function routes(network,bundle,startPoint,endPoint,date,hour,family,detour=.3){
  const ring=bundle.boundary.geometry.coordinates[0];
  function inside(point){let yes=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
    const a=ring[i],b=ring[j];if((a[1]>point[1])!==(b[1]>point[1])&&point[0]<(b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0])yes=!yes;
  }return yes}
  if(!inside(startPoint)||!inside(endPoint))throw Error('Choose both endpoints inside the published UPC Lyft-zone boundary.');
  const start=snap(network,startPoint),end=snap(network,endPoint);
  if(start.node===end.node)throw Error('Choose start and destination farther apart.');
  const direct=search(network,start.node,end.node,bundle,date,hour,family,0);
  let alternative=direct;
  for(const beta of [.25,.5,1,2,4]){
    const candidate=search(network,start.node,end.node,bundle,date,hour,family,beta);
    if(candidate.minutes<=direct.minutes*(1+detour)&&candidate.activity<alternative.activity)alternative=candidate;
  }
  return {direct,alternative,changed:alternative!==direct,startSnapMeters:start.meters,endSnapMeters:end.meters,
    // Coordinate offsets are shown separately; no unvalidated connector is drawn.
    snappedStart:network.graph.nodes[start.node],snappedEnd:network.graph.nodes[end.node]};
}
