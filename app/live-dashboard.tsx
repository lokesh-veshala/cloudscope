"use client";

import {useCallback,useEffect,useMemo,useState} from "react";
import {
  Activity, AlertTriangle, BarChart3, Boxes, CheckCircle2,
  CircleDollarSign, Cloud, Database, HardDrive, LayoutDashboard, Menu,
  RefreshCw, Server, ShieldCheck, Tags, X,
} from "lucide-react";
import {Accounts,request} from "./management";
import {LiveInventory} from "./live-inventory";

type Account={id:string;display_name:string;provider_account_id:string;connection_status:string;last_collected_at?:string|null};
type Service={service:string;resources:number;complete_rates:number;partial_rates:number;estimated_rates:number;unresolved_rates:number};
type ServiceCost={service:string;intervals:number;priced_intervals:number;unresolved_intervals:number;amount_usd:string|null};
type Trend={day:string;daily_usd:string;cumulative_usd:string;intervals:number};
type CostResource={id:string;name:string|null;provider_resource_id:string;service:string;region:string;state:string;pricing_status:string;amount_usd:string};
type Limit={id:string;name:string;amount_usd:string;tag_key:string;tag_value:string;observed_subtotal_usd:string|null;evaluation_status:string};
type Dashboard={account_name:string;period_start:string;period_end:string;last_collected_at:string|null;last_error:string|null;
  services:Service[];service_costs:ServiceCost[];cost_trend:Trend[];top_resources:CostResource[];limits:Limit[];
  inventory:{resources:number;untagged_resources:number};cost_summary:{intervals:number;priced_intervals:number;unresolved_intervals:number;observed_subtotal_usd:string|null};
  complete_account_cost_usd:null;alerts_evaluated:false;limitations:string[]};

const money=new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:6});
const colors=["#316cf4","#33a57a","#24a2ae","#7956d8","#df7a3e","#d4aa32","#7d879c"];

function utcDefaults(){const now=new Date();const end=now.toISOString().slice(0,10);return {from:`${end.slice(0,8)}01`,to:end};}
function price(value:string|null){return value===null?"Unavailable":money.format(Number(value));}

function LiveTrend({rows}:{rows:Trend[]}){
  const [selected,setSelected]=useState<number|null>(null);
  if(rows.length===0)return <div className="live-empty-chart"><BarChart3/><b>No priced intervals in this range</b><span>Unavailable periods are not drawn as zero.</span></div>;
  const max=Math.max(...rows.map(row=>Number(row.cumulative_usd)),0.000001);
  const points=rows.map((row,index)=>({x:rows.length===1?360:48+index*(642/(rows.length-1)),y:190-Number(row.cumulative_usd)/max*150,...row}));
  const line=points.map(point=>`${point.x},${point.y}`).join(" ");
  const area=`${points[0].x},190 ${line} ${points.at(-1)?.x},190`;
  return <svg className="cost-chart" viewBox="0 0 720 230" role="img" aria-label="Cumulative observed cost trend">
    <defs><linearGradient id="liveCostFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#316cf4" stopOpacity=".28"/><stop offset="100%" stopColor="#316cf4" stopOpacity=".02"/></linearGradient></defs>
    {[0,.25,.5,.75,1].map(part=><g key={part}><line x1="48" y1={190-part*150} x2="690" y2={190-part*150} stroke="#e7ebf3" strokeDasharray="3 5"/><text x="42" y={194-part*150} textAnchor="end">{money.format(max*part)}</text></g>)}
    <polygon points={area} fill="url(#liveCostFill)"/><polyline points={line} fill="none" stroke="#316cf4" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round"/>
    {points.map((point,index)=><g key={point.day}><circle cx={point.x} cy={point.y} r="4" fill="white" stroke="#316cf4" strokeWidth="2"/><text x={point.x} y="216" textAnchor="middle">{point.day.slice(5)}</text><rect x={point.x-28} y="0" width="56" height="200" fill="transparent" tabIndex={0} role="button" aria-label={`${point.day}: ${money.format(Number(point.cumulative_usd))}`} onPointerEnter={()=>setSelected(index)} onPointerLeave={()=>setSelected(null)} onFocus={()=>setSelected(index)} onBlur={()=>setSelected(null)} onClick={()=>setSelected(index)} onKeyDown={event=>{if(event.key==="Escape")setSelected(null);if(event.key==="Enter"||event.key===" ")setSelected(index);}}/></g>)}
    {selected!==null&&<g pointerEvents="none" role="tooltip"><rect x={Math.min(points[selected].x,535)} y="0" width="170" height="52" rx="7" fill="#15223a"/><text x={Math.min(points[selected].x,535)+10} y="20" style={{fill:"white",fontSize:12}}>{points[selected].day}</text><text x={Math.min(points[selected].x,535)+10} y="40" style={{fill:"white",fontSize:12}}>{money.format(Number(points[selected].cumulative_usd))} observed</text></g>}
  </svg>;
}

export function LiveDashboard({onSample}:{onSample:()=>void}){
  const [accounts,setAccounts]=useState<Account[]>([]);const [account,setAccount]=useState("");
  const [data,setData]=useState<Dashboard|null>(null);const [error,setError]=useState("");
  const [view,setView]=useState<"overview"|"inventory"|"accounts">("overview");
  const [from,setFrom]=useState("");const [to,setTo]=useState("");const [queryRange,setQueryRange]=useState("");const [applied,setApplied]=useState(0);
  const [loading,setLoading]=useState(true);const [mobile,setMobile]=useState(false);
  useEffect(()=>{let active=true;request<Account[]>("/api/v1/accounts").then(rows=>{if(!active)return;const defaults=utcDefaults();setFrom(defaults.from);setTo(defaults.to);setQueryRange(`${defaults.from}/${defaults.to}`);setAccounts(rows);const preferred=rows.find(item=>item.connection_status==="CONNECTED")??rows[0];if(preferred)setAccount(preferred.id);else setLoading(false);}).catch(reason=>{if(active){setError(reason.message);setLoading(false);}});return()=>{active=false;};},[]);
  const reload=useCallback(()=>{setLoading(true);setError("");setApplied(value=>value+1);},[]);
  useEffect(()=>{if(!account||!queryRange)return;let active=true;const [start,end]=queryRange.split("/");request<Dashboard>(`/api/v1/accounts/${account}/overview?start_date=${start}&end_date=${end}`).then(result=>{if(active){setData(result);setError("");}}).catch(reason=>{if(active){setError(reason.message);setData(null);}}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[account,queryRange,applied]);
  const selected=accounts.find(item=>item.id===account);
  const intervalCoverage=data?.cost_summary.intervals?data.cost_summary.priced_intervals/data.cost_summary.intervals*100:0;
  const maxService=useMemo(()=>Math.max(...(data?.service_costs.map(item=>Number(item.amount_usd||0))??[0]),.000001),[data]);
  const nav=[{id:"overview" as const,label:"Live overview",icon:LayoutDashboard},{id:"inventory" as const,label:"Live inventory",icon:Boxes},{id:"accounts" as const,label:"Cloud accounts",icon:Database}];
  return <div className="shell live-shell">
    <aside className={`sidebar ${mobile?"open":""}`}><div className="brand"><span className="logo"><Cloud size={19}/></span><span>Cloud<span>Scope</span></span><button className="close" onClick={()=>setMobile(false)} aria-label="Close navigation"><X/></button></div>
      <nav>{nav.map(item=><button key={item.id} className={view===item.id?"active":""} onClick={()=>{setView(item.id);setMobile(false);}}><item.icon size={18}/>{item.label}</button>)}</nav>
      <div className="side-caption">REFERENCE</div><nav><button onClick={onSample}><BarChart3 size={18}/>Sample dashboard</button></nav>
      <div className="sidebar-foot"><div className="health"><span><i/>Live collector</span><small>{selected?.connection_status??"No account selected"}</small></div></div>
    </aside>
    {mobile&&<button className="backdrop" onClick={()=>setMobile(false)} aria-label="Close navigation"/>}
    <main className="main"><header className="topbar"><button className="menu" onClick={()=>setMobile(true)} aria-label="Open navigation"><Menu/></button><div className="crumb"><Activity size={15}/><strong>Live read-only pilot</strong></div><div className="top-actions"><select aria-label="AWS account" value={account} onChange={event=>{setLoading(true);setError("");setAccount(event.target.value);setData(null);}}><option value="">Select account</option>{accounts.map(item=><option value={item.id} key={item.id}>{item.display_name}</option>)}</select><button className="refresh" disabled={!account||loading} onClick={reload}><RefreshCw size={16}/>{loading?"Loading…":"Refresh"}</button></div></header>
      <section className="content">
        {view==="overview"&&<><div className="title-row"><div><div className="eyebrow"><CheckCircle2 size={14}/>LIVE OBSERVATIONS</div><h1>{data?.account_name??selected?.display_name??"Cloud cost overview"}</h1><p>Observed local subtotals only. Complete AWS account cost and notifications remain disabled.</p></div><form className="live-date" onSubmit={event=>{event.preventDefault();setQueryRange(`${from}/${to}`);reload();}}><label>FROM<input type="date" required value={from} onChange={event=>setFrom(event.target.value)}/></label><label>TO<input type="date" required value={to} onChange={event=>setTo(event.target.value)}/></label><button type="submit">Apply</button></form></div>
          {error&&<div className="guardrail error"><AlertTriangle/><div><b>Live data unavailable</b><span>{error}</span></div></div>}
          {!error&&accounts.length===0&&!loading&&<article className="panel management"><h2>No account registered</h2><p>Open Cloud accounts to register and test an AWS account.</p><button onClick={()=>setView("accounts")}>Open Cloud accounts</button></article>}
          {data&&<><div className="guardrail amber"><ShieldCheck/><div><b>Partial observed-cost view</b><span>Unavailable services are excluded, never treated as zero. Alarm evaluation and SNS publishing are withheld.</span></div><span>NO ALERTS</span></div>
            <div className="kpis"><article><div className="kpi-icon blue"><CircleDollarSign/></div><span>OBSERVED SUBTOTAL</span><strong>{price(data.cost_summary.observed_subtotal_usd)}</strong><small>Since collection began within selected range</small></article><article><div className="kpi-icon violet"><Server/></div><span>STORED RESOURCES</span><strong>{data.inventory.resources}</strong><small>Latest normalized inventory</small></article><article><div className="kpi-icon green"><ShieldCheck/></div><span>PRICED INTERVALS</span><strong>{data.cost_summary.priced_intervals}</strong><small>{intervalCoverage.toFixed(1)}% interval coverage</small></article><article><div className="kpi-icon amber"><Tags/></div><span>UNTAGGED RESOURCES</span><strong>{data.inventory.untagged_resources}</strong><small>Latest observation has no tags</small></article></div>
            <div className="overview-grid"><article className="panel chart-panel"><div className="panel-head"><div><h2>Observed cost trend</h2><span>Cumulative • USD</span></div></div><div className="chart"><LiveTrend rows={data.cost_trend}/></div><div className="chart-foot"><span><i/>Priced intervals only</span><span>{data.period_start.slice(0,10)} → {data.period_end.slice(0,10)}</span></div></article><article className="panel services"><div className="panel-head"><div><h2>Observed cost by service</h2><span>Not complete spend</span></div></div>{data.service_costs.map((item,index)=><div className="service-row live-service" key={item.service}><span className="service-rank">{index+1}</span><span className="service-dot" style={{background:colors[index%colors.length]}}/><b>{item.service.toUpperCase()}</b><div className="service-bar"><i style={{width:`${Number(item.amount_usd||0)/maxService*100}%`,background:colors[index%colors.length]}}/></div><strong>{price(item.amount_usd)}</strong><small>{item.unresolved_intervals} unresolved</small></div>)}</article></div>
            <div className="lower-grid"><article className="panel limit-card"><div className="panel-head"><div><h2>Monthly team limits</h2><span>Evaluation withheld</span></div></div>{data.limits.length===0?<p>No local team limit is configured.</p>:data.limits.map(limit=><div className="live-limit" key={limit.id}><div><b>{limit.name}</b><span>{limit.tag_key}={limit.tag_value}</span></div><strong>{price(limit.observed_subtotal_usd)} <small>/ {price(limit.amount_usd)}</small></strong><span className="pill warning">WITHHELD</span></div>)}<p className="muted-copy">Partial observed costs may be displayed for visibility, but they cannot make a threshold decision.</p></article><article className="panel quality-card"><div className="panel-head"><div><h2>Rate coverage</h2><span>Latest resource observations</span></div></div><div className="quality-list">{data.services.map(item=><span key={item.service}><HardDrive/>{item.service.toUpperCase()} — {item.resources} resources <b>{item.complete_rates} complete · {item.partial_rates} partial · {item.estimated_rates} assumed · {item.unresolved_rates} unresolved</b></span>)}</div></article></div>
            <article className="panel table-panel"><div className="panel-head"><div><h2>Highest observed resource subtotals</h2><span>Priced intervals in selected range</span></div></div><div className="table-wrap"><table><thead><tr><th>Resource</th><th>Service</th><th>Region</th><th>State</th><th>Rate status</th><th className="right">Observed subtotal</th></tr></thead><tbody>{data.top_resources.map(item=><tr key={item.id}><td><b>{item.name||item.provider_resource_id}</b><br/><small>{item.provider_resource_id}</small></td><td>{item.service.toUpperCase()}</td><td>{item.region}</td><td>{item.state}</td><td>{item.pricing_status}</td><td className="right cost">{price(item.amount_usd)}</td></tr>)}</tbody></table></div>{data.top_resources.length===0&&<p>No priced resource intervals exist for this range.</p>}</article>
            <details className="panel live-limitations"><summary>Coverage limitations</summary><ul>{data.limitations.map(item=><li key={item}>{item}</li>)}</ul></details><p className="live-freshness">Last successful collection: {data.last_collected_at??"not collected"}</p></>}
        </>}
        {view==="inventory"&&<><div className="title-row"><div><div className="eyebrow"><Activity size={14}/>LIVE INVENTORY</div><h1>Resources and matched rates</h1><p>Stored locally from the read-only collector.</p></div></div><LiveInventory accounts={accounts}/></>}
        {view==="accounts"&&<><div className="title-row"><div><div className="eyebrow"><Activity size={14}/>ACCOUNT MANAGEMENT</div><h1>Cloud accounts</h1><p>Connection, collection schedule and onboarding controls.</p></div></div><Accounts/></>}
      </section>
    </main>
  </div>;
}
