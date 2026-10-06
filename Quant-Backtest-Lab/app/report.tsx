'use client';
import {useState} from 'react';
import {Info,BarChart3,Layers,FileBarChart} from 'lucide-react';
import {Tabs,TabsList,TabsTrigger} from '@/components/ui/tabs';
import {Select,SelectContent,SelectItem,SelectTrigger,SelectValue} from '@/components/ui/select';
import {Tooltip,TooltipProvider,TooltipTrigger,TooltipContent} from '@/components/ui/tooltip';
import {reportGroups,diagramNames,diagramData,reportContext,headline,headlineIsPending,type ReportMetric} from './report-data';
import {fmt} from './demo';
import {useRun,type RunState} from './run-context';
import {PendingMetric} from './pending-metric';

type DiagramPoint={label:string;x:number;v:number;n:number;positive?:number;negative?:number};

function ReportState({state}:{state:RunState}){
 if(state.status==='loading')return <div className="report-workspace"><div className="panel"><div className="panel-head"><h2><FileBarChart/>Backtest Summary</h2></div><p className="report-note">Loading the seeded reference run…</p></div></div>;
 return <div className="report-workspace"><div className="panel"><div className="panel-head"><h2><FileBarChart/>Backtest Summary</h2></div><p className="report-note" role="alert"><Info size={13}/>{state.status==='error'?state.error:state.status==='malformed'?state.message:'Unknown state'}</p></div></div>;
}

/**
 * Splits a category into its analytical sub-groups, keeping the declared
 * order and never dropping a metric.
 */
function groupMetrics(items:ReportMetric[]){const order:string[]=[];const buckets=new Map<string,ReportMetric[]>();for(const m of items){const key=m.group||'Other';if(!buckets.has(key)){buckets.set(key,[]);order.push(key)}buckets.get(key)!.push(m)}return order.map(name=>({name,items:buckets.get(name)!}))}

export function FullReport(){
 const state=useRun();
 const [group,setGroup]=useState('Profit & Risk');
 if(state.status!=='ready')return <ReportState state={state}/>;
 const run=state.run;
 const strip=headline(run);
 const grouped=groupMetrics(reportGroups(run)[group]);
 return <div className="report-workspace">
 <section className="panel report-summary">
  <div className="panel-head">
   <h2><FileBarChart/>Backtest Summary</h2>
   <span className="report-context">{reportContext(run)}</span>
  </div>
  <div className="report-strip">{strip.map(([l,v])=><div key={l} className={headlineIsPending(l)?'is-pending':''}><span>{l}</span><b>{v}</b></div>)}</div>
 </section>
 <section className="panel report-groups">
  <div className="panel-head report-groups-head">
   <h2><Layers/>Run Analysis</h2>
   <Tabs value={group} onValueChange={setGroup}><TabsList className="report-tabs">{Object.keys(reportGroups(run)).map(g=><TabsTrigger key={g} value={g}>{g}</TabsTrigger>)}</TabsList></Tabs>
  </div>
  <div className="report-body">{grouped.map(({name,items})=>
   <div className="report-block" key={name}>
    <div className="report-block-head"><h3>{name}</h3><span>{items.length}</span></div>
    <TooltipProvider delayDuration={200}>
     <dl className="report-kv">{items.map(m=>m.pending?<PendingMetric key={m.label} label={m.label} reason={m.note??'Not supplied by run.'} compact status="Pending" showLabel={false} className="report-pending"/>:<div key={m.label}>
      <dt>{m.label}{m.note&&<Tooltip><TooltipTrigger asChild><button className="help" aria-label={`About ${m.label}`}><Info size={11}/></button></TooltipTrigger><TooltipContent className="max-w-80">{m.note}</TooltipContent></Tooltip>}</dt>
      <dd>{m.value}</dd>
     </div>)}</dl>
    </TooltipProvider>
   </div>)}</div>
  <p className="report-note"><Info size={12}/>Pending fields are listed for report completeness. Account currency, lot sizing, equity, margin and statistical definitions are not supplied by the engine; no account numbers are shown for the seeded demo.</p>
 </section>
 </div>;
}

export function Analytics(){
 const state=useRun();
 const [name,setName]=useState('Profit / Loss by Hour');
 const [hover,setHover]=useState<number|null>(null);
 if(state.status!=='ready')return <ReportState state={state}/>;
 const run=state.run;
 const data=diagramData(name,run) as DiagramPoint[];
 const scatter=name==='Profit vs Holding Time';
 const multi=name.startsWith('Profit / Loss');
 const max=Math.max(1,...data.map(d=>Math.abs(d.v)),...data.map(d=>Math.abs(d.positive||0)),...data.map(d=>Math.abs(d.negative||0)));
 const baseline=150,y=(v:number)=>baseline-v/max*100,w=800/Math.max(1,data.length);
const m=run.metrics;
  const pnls=run.trades.map(t=>t.pnl);
  const stopDist=Number(run.strategy.parameters.stopDistance)||5;
  const avgR=pnls.length?pnls.reduce((a,b)=>a+b,0)/pnls.length/stopDist:0;
  const spanMs=Date.parse(run.dateRange.endUtc)-Date.parse(run.dateRange.startUtc);
  const exposure=run.trades.length&&spanMs>0?(run.trades.reduce((a,t)=>a+(Date.parse(t.exitTimeUtc)-Date.parse(t.entryTimeUtc)),0)/spanMs*100):0;
  const avgSpreadIn = m.avgSpreadIn ?? 0;
  const payoff = m.payoff ?? 0;
  return <div className="analytics-workspace">
 <div className="analysis-kpis">{[
  ['Payoff Ratio',m.payoff===null?'Pending':fmt(payoff),'Average win / average loss · price terms'],
  ['Average R',`${fmt(avgR)}R`,`PnL / ${stopDist} reference stop · price terms · sizing not validated`],
  ['Time in Market',exposure.toFixed(1)+'%','Sum of holding time / 22h seeded window'],
  ['Average Entry Spread',m.avgSpreadIn===null||m.avgSpreadIn===undefined?'Pending':fmt(avgSpreadIn),'Observed seeded entry ticks · price terms'],
 ].map(([l,v,n])=><div key={l}><span>{l}</span><strong>{v}</strong><small>{n}</small></div>)}</div>
 <section className="analytics-chart panel">
 <div className="panel-head">
 <h2>
 <BarChart3 size={16}/>Report Diagrams & Analysis</h2>
 <Select value={name} onValueChange={v=>{setName(v);setHover(null)}}>
 <SelectTrigger aria-label="Analysis chart">
 <SelectValue/>
 </SelectTrigger>
 <SelectContent>{diagramNames.map(n=><SelectItem key={n} value={n}>{n}</SelectItem>)}</SelectContent>
 </Select>
 </div>{!data.length?<div className="analytics-empty"><BarChart3 size={28}/><h3>{name}</h3><p>Requires observed {name.startsWith('MFE')?'favorable':'adverse'} excursion data from the engine.</p><span>Chart prepared · no invented excursion values</span></div>:<><svg viewBox="0 0 920 300" preserveAspectRatio="none" role="img" aria-label={name} onPointerLeave={()=>setHover(null)}>{[-1,-.5,0,.5,1].map(k=><g key={k}><line x1="55" x2="855" y1={y(k*max)} y2={y(k*max)} className="gridline"/><text x="4" y={y(k*max)+4}>{(k*max).toFixed(1)}</text></g>)}{data.map((d,i)=>{const x=scatter?55+d.x/Math.max(...data.map(p=>p.x))*780:55+(i+.5)*w;return <g key={d.label} onPointerEnter={()=>setHover(i)}><rect x={scatter?x-12:55+i*w} y="35" width={scatter?24:w} height="240" fill="transparent"/>{scatter?<circle cx={x} cy={y(d.v)} r="5" fill={d.v>=0?'var(--success)':'var(--danger)'}/>:multi?<>{[d.positive||0,d.negative||0].map((v,k)=><rect key={k} x={x-w*.3+k*w*.3} y={Math.min(y(v),baseline)} width={Math.max(2,w*.27)} height={Math.max(1,Math.abs(y(v)-baseline))} fill={k?'var(--danger)':'var(--success)'}/>)}</>:<rect x={x-w*.28} y={Math.min(y(d.v),baseline)} width={Math.max(2,w*.56)} height={Math.max(1,Math.abs(y(d.v)-baseline))} fill={d.v>=0?'var(--primary)':'var(--danger)'}/>}<text x={x} y="281" textAnchor="middle">{scatter?Math.round(d.x)+'m':d.label}</text></g>})}{hover!==null&&data[hover]&&<text x="55" y="19" className="analysis-hover">{data[hover].label} · {fmt(data[hover].v)} · {data[hover].n} trade(s) · price terms</text>}</svg>
 <div className="analysis-caption">
 <span>{name}</span>
 <span>{name.startsWith('Entries')||name==='Exit Reasons'?'Trade count':name.startsWith('R-')?`R = price PnL / ${stopDist} (reference, unvalidated)`:'Price terms — not account currency'} · UTC · seeded demo</span>
 </div>
 </>}
 <p className="report-note"><Info size={12}/>All diagrams use seeded demo trades. Values are price terms, not account currency.</p>
 </section>
 </div>;
}