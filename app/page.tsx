"use client";

import { useMemo, useState } from "react";
import { Accounts, AlertHistory, Settings } from "./management";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import {
  Activity, AlertTriangle, Bell, Boxes, CheckCircle2, ChevronDown,
  CircleDollarSign, Cloud, Database, Gauge, HardDrive, Info,
  LayoutDashboard, Menu, MoreHorizontal, RefreshCw, Search, Server,
  ShieldCheck, SlidersHorizontal, Sparkles, TrendingDown, Users, X,
} from "lucide-react";

type Service = "EC2" | "EBS" | "EFS" | "FSx" | "RDS" | "S3" | "NAT" | "ELB";
type Resource = { id:string; name:string; service:Service; region:string; state:string; model:string; cost:number; confidence:number; team:string };

const trend = [
  { day:"Sep 1", cost:419 }, { day:"Sep 3", cost:932 }, { day:"Sep 5", cost:1546 },
  { day:"Sep 7", cost:2117 }, { day:"Sep 9", cost:2894 }, { day:"Sep 11", cost:3610 },
  { day:"Sep 13", cost:4137 },
];

const resources: Resource[] = [
  {id:"i-0c91a2f34e67d89ab",name:"hpc-worker-01",service:"EC2",region:"us-east-1",state:"Running",model:"Spot",cost:1184.21,confidence:100,team:"HPC"},
  {id:"fs-0a132ef980c7b21d4",name:"scratch-lustre",service:"FSx",region:"us-east-1",state:"Available",model:"—",cost:741.42,confidence:100,team:"HPC"},
  {id:"i-0f62d77ab12c45e98",name:"hpc-headnode",service:"EC2",region:"us-east-1",state:"Running",model:"On-Demand",cost:693.18,confidence:100,team:"HPC"},
  {id:"vol-06b0fabe6712d890c",name:"training-data",service:"EBS",region:"us-east-1",state:"In-use",model:"gp3",cost:481.08,confidence:100,team:"ML Platform"},
  {id:"db-XJ2Q9T4B8R",name:"qa-postgres",service:"RDS",region:"us-east-2",state:"Available",model:"db.r6g.xlarge",cost:367.93,confidence:100,team:"QA"},
  {id:"nat-07e8b84c93a24c03d",name:"shared-egress",service:"NAT",region:"us-east-1",state:"Available",model:"—",cost:288.51,confidence:99,team:"DevOps"},
  {id:"fs-08a3e77ce2081b9d2",name:"team-shared",service:"EFS",region:"us-east-1",state:"Available",model:"Elastic",cost:190.38,confidence:100,team:"HPC"},
  {id:"prod-artifacts-us",name:"prod-artifacts",service:"S3",region:"us-east-1",state:"Active",model:"Standard",cost:112.64,confidence:87,team:"DevOps"},
  {id:"app/prod-api/50dc6c495c0c9188",name:"prod-api",service:"ELB",region:"us-east-1",state:"Active",model:"ALB",cost:77.65,confidence:98,team:"QA"},
];

const services = [
  {name:"EC2",value:1877.39,color:"#316cf4"},{name:"FSx",value:741.42,color:"#7956d8"},
  {name:"EBS",value:481.08,color:"#33a57a"},{name:"RDS",value:367.93,color:"#df7a3e"},
  {name:"NAT",value:288.51,color:"#d4aa32"},{name:"EFS",value:190.38,color:"#24a2ae"},
  {name:"S3 + ELB",value:190.29,color:"#7d879c"},
];

const teams = [
  {name:"HPC",cost:2811,limit:5000,threshold:80,color:"#316cf4"},
  {name:"QA",cost:2740,limit:3000,threshold:80,color:"#e0a729"},
  {name:"DevOps",cost:2790,limit:4500,threshold:80,color:"#38a47b"},
  {name:"Research",cost:5050,limit:5000,threshold:80,color:"#dc5d60"},
];

const money = new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2});
const nav = [{id:"overview",label:"Overview",icon:LayoutDashboard},{id:"resources",label:"Resources",icon:Boxes},{id:"teams",label:"Team limits",icon:Users},{id:"quality",label:"Data quality",icon:ShieldCheck}];

function StateDot({state}:{state:string}) { return <span className="state"><i className={state === "Running" || state === "Active" || state === "Available" || state === "In-use" ? "ok":""}/>{state}</span> }
function Confidence({value}:{value:number}) { const tone=value>=99?"good":value>=95?"warn":"medium"; return <span className={`confidence ${tone}`}><ShieldCheck size={13}/>{value}%</span> }

function CostTrend() {
  const [hover,setHover]=useState<number|null>(null);
  const points=trend.map((item,index)=>({x:48+index*104,y:190-(item.cost/4500)*160,...item}));
  const line=points.map(point=>`${point.x},${point.y}`).join(" ");
  const area=`48,190 ${line} ${points.at(-1)?.x},190`;
  return <svg className="cost-chart" viewBox="0 0 720 230" role="img" aria-label="Month-to-date estimated cost rising from $419 to $4,137">
    <defs><linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#316cf4" stopOpacity=".28"/><stop offset="100%" stopColor="#316cf4" stopOpacity=".02"/></linearGradient></defs>
    {[0,1000,2000,3000,4000].map(value=><g key={value}><line x1="48" y1={190-value/4500*160} x2="690" y2={190-value/4500*160} stroke="#e7ebf3" strokeDasharray="3 5"/><text x="39" y={194-value/4500*160} textAnchor="end">${value/1000}k</text></g>)}
    <polygon points={area} fill="url(#costFill)"/><polyline points={line} fill="none" stroke="#316cf4" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round"/>
    {points.map(point=><g key={point.day}><circle cx={point.x} cy={point.y} r="3.5" fill="white" stroke="#316cf4" strokeWidth="2"/><text x={point.x} y="216" textAnchor="middle">{point.day}</text></g>)}
    {points.map((point,index)=><rect key={point.day} x={point.x-25} y="0" width="50" height="200" fill="transparent" tabIndex={0} role="button" aria-label={point.day+": "+money.format(point.cost)} onPointerEnter={()=>setHover(index)} onPointerLeave={()=>setHover(null)} onFocus={()=>setHover(index)} onBlur={()=>setHover(null)} onClick={()=>setHover(index)} onKeyDown={e=>{if(e.key==="Escape")setHover(null);else if(e.key==="Enter"||e.key===" ")setHover(index)}}/>)}
    {hover!==null&&<g pointerEvents="none" role="tooltip"><rect x={Math.min(points[hover].x,535)} y="0" width="170" height="48" rx="7" fill="#15223a"/><text x={Math.min(points[hover].x,535)+10} y="20" style={{fill:"white",fontSize:14}}>{points[hover].day}</text><text x={Math.min(points[hover].x,535)+10} y="39" style={{fill:"white",fontSize:14}}>{money.format(points[hover].cost)}</text></g>}
  </svg>
}

export default function Home() {
  const [view,setView]=useState("overview");
  const [range,setRange]=useState("2026-09-01/2026-09-13");
  const [from,setFrom]=useState("2026-09-01"); const [to,setTo]=useState("2026-09-13");
  const [dateError,setDateError]=useState("");
  const hasData=range==="2026-09-01/2026-09-13"; const [query,setQuery]=useState("");
  const [service,setService]=useState("All services"); const [region,setRegion]=useState("All regions");
  const [mobile,setMobile]=useState(false); const [notice,setNotice]=useState("");
  const filtered=useMemo(()=>resources.filter(r=>(service==="All services"||r.service===service)&&(region==="All regions"||r.region===region)&&(`${r.name} ${r.id} ${r.team}`).toLowerCase().includes(query.toLowerCase())),[query,service,region]);
  const refresh=()=>{setNotice("Demo snapshot reloaded. No live AWS request was made."); setTimeout(()=>setNotice(""),3200)};

  return <div className="shell">
    <aside className={`sidebar ${mobile?"open":""}`}>
      <div className="brand"><span className="logo"><Cloud size={19}/></span><span>Cloud<span>Scope</span></span><button className="close" onClick={()=>setMobile(false)} aria-label="Close navigation"><X/></button></div>
      <nav>{nav.map(n=><button key={n.id} className={view===n.id?"active":""} onClick={()=>{setView(n.id);setMobile(false)}}><n.icon size={18}/>{n.label}{n.id==="quality"&&<span className="nav-count">2</span>}</button>)}</nav>
      <div className="side-caption">MANAGE</div>
      <nav>{[["accounts","Cloud accounts"],["alerts","Alert history"],["settings","Settings"]].map(([id,label])=><button key={id} className={view===id?"active":""} onClick={()=>{setView(id);setMobile(false)}}>{id==="accounts"?<Database size={18}/>:id==="alerts"?<Bell size={18}/>:<SlidersHorizontal size={18}/>} {label}</button>)}</nav>
      <div className="sidebar-foot"><div className="health"><span><i/>Demo • collectors not connected</span><small>No live collection</small></div><div className="profile"><span>LK</span><div><b>Lokesh Kumar</b><small>Platform admin</small></div><MoreHorizontal size={18}/></div></div>
    </aside>
    {mobile&&<button className="backdrop" onClick={()=>setMobile(false)} aria-label="Close navigation"/>}
    <main className="main">
      <header className="topbar"><button className="menu" onClick={()=>setMobile(true)} aria-label="Open navigation"><Menu/></button><div className="crumb"><Popover><PopoverTrigger asChild><button className="scope-button">Shared Engineering <ChevronDown size={15}/></button></PopoverTrigger><PopoverContent className="control-popover"><h2>Workspace</h2><p>Shared Engineering — demo workspace.</p><p>No connected AWS accounts are available to switch to.</p><button onClick={()=>setView("accounts")}>Open cloud accounts</button></PopoverContent></Popover><b>/</b><strong>HPC Production</strong><em>DEMO DATA</em></div><div className="top-actions"><Popover><PopoverTrigger asChild><button className="ghost" aria-label="Notifications"><Bell size={18}/></button></PopoverTrigger><PopoverContent className="control-popover"><h2>Notifications</h2><p>No live notifications. SNS publishing is disabled.</p><button onClick={()=>setView("alerts")}>Open alert history</button></PopoverContent></Popover><button className="refresh" onClick={refresh}><RefreshCw size={16}/>Refresh</button></div></header>
      <section className="content">
        <div className="title-row"><div><div className="eyebrow"><Activity size={14}/>DEMO SNAPSHOT</div><h1>{nav.find(n=>n.id===view)?.label ?? ({accounts:"Cloud accounts",alerts:"Alert history",settings:"Settings"} as Record<string,string>)[view]}</h1><p>{view==="overview"?"Illustrative snapshot only; not connected to AWS.":view==="resources"?"Every discovered resource with traceable price coverage.":view==="teams"?"Independent monthly limits, verified thresholds, and notification state.":"Freshness and pricing-dimension coverage behind every estimate."}</p></div><div className="date-control"><small>DATE RANGE</small><Popover><PopoverTrigger asChild><button>{range.replace("/"," → ")} <ChevronDown size={15}/></button></PopoverTrigger><PopoverContent className="control-popover"><h2>Date range (UTC)</h2><p>Only Sep 1–13, 2026 has a complete demo snapshot. Other ranges show unavailable, never a guessed estimate.</p><button onClick={()=>{setRange("2026-09-01/2026-09-13");setFrom("2026-09-01");setTo("2026-09-13");setDateError("")}}>Demo month to date</button><form onSubmit={e=>{e.preventDefault();const days=(Date.parse(to)-Date.parse(from))/86400000+1;if(!Number.isFinite(days)||days<1||days>90){setDateError("Choose an ordered date range of 1–90 days.");return;}setRange(from+"/"+to);setDateError("");}}><label>Start date<input aria-label="Start date" type="date" required value={from} onChange={e=>setFrom(e.target.value)}/></label><label>End date<input aria-label="End date" type="date" required value={to} onChange={e=>setTo(e.target.value)}/></label><button type="submit">Apply date range</button><p role="alert">{dateError}</p></form></PopoverContent></Popover></div></div>

        {hasData&&view==="overview"&&<Overview onGo={setView}/>}
        {hasData&&view==="resources"&&<Resources rows={filtered} query={query} setQuery={setQuery} service={service} setService={setService} region={region} setRegion={setRegion}/>}
        {hasData&&view==="teams"&&<Teams/>}
        {hasData&&view==="quality"&&<Quality/>}
        {!hasData&&["overview","resources","teams","quality"].includes(view)&&<article className="panel management"><h2>Data unavailable for this range</h2><p>No usage intervals have been collected for {range.replace("/"," through ")}. No costs or alarm decisions have been calculated.</p><button onClick={()=>setRange("2026-09-01/2026-09-13")}>Restore demo snapshot</button></article>}
        {view==="accounts"&&<Accounts/>}{view==="alerts"&&<AlertHistory/>}{view==="settings"&&<Settings/>}
      </section>
      {notice&&<div className="toast"><CheckCircle2 size={18}/>{notice}</div>}
    </main>
  </div>
}

function Overview({onGo}:{onGo:(v:string)=>void}) { return <>
  <div className="kpis">
    <article><div className="kpi-icon blue"><CircleDollarSign/></div><span>ESTIMATED COST</span><strong>$4,137.00</strong><small><b className="up">↑ 8.4%</b> vs. prior period</small></article>
    <article><div className="kpi-icon violet"><Server/></div><span>ACTIVE RESOURCES</span><strong>482</strong><small>Across 3 AWS accounts</small></article>
    <article><div className="kpi-icon green"><TrendingDown/></div><span>SPOT SAVINGS</span><strong>$1,284</strong><small><b>38.6%</b> vs. on-demand</small></article>
    <article><div className="kpi-icon amber"><AlertTriangle/></div><span>UNTAGGED</span><strong>17</strong><small><b className="warn-text">4 cost-bearing</b> resources</small></article>
  </div>
  <div className="overview-grid">
    <article className="panel chart-panel"><PanelHead title="Cost trend" detail="Estimated • USD"/><div className="chart"><CostTrend/></div><div className="chart-foot"><span><i/>Illustrative intervals</span><span>Demo snapshot • Sep 13, 2026</span></div></article>
    <article className="panel services"><PanelHead title="Cost by service" detail="MTD"/>{services.map((s,i)=><div className="service-row" key={s.name}><span className="service-rank">{i+1}</span><span className="service-dot" style={{background:s.color}}/><b>{s.name}</b><div className="service-bar"><i style={{width:`${s.value/services[0].value*100}%`,background:s.color}}/></div><strong>{money.format(s.value)}</strong></div>)}</article>
  </div>
  <div className="lower-grid"><article className="panel limit-card"><PanelHead title="Monthly team limit" detail="Calendar month"/><div className="limit-main"><div><span>HPC Production</span><strong>$4,137 <small>/ $5,000</small></strong></div><span className="pill warning"><AlertTriangle size={14}/>80% triggered</span></div><div className="progress"><i style={{width:"82.7%"}}/><span style={{left:"80%"}}/></div><div className="limit-labels"><span>82.7% used</span><b>$863 remaining</b></div><div className="callout"><Sparkles size={17}/><div><b>Illustrative threshold scenario</b><span>No SNS message has been sent. Live alarm publishing is not connected.</span></div></div><button className="text-button" onClick={()=>onGo("teams")}>View limit details →</button></article>
  <article className="panel quality-card"><PanelHead title="Estimate quality" detail="9 services"/><div className="quality-score"><div className="ring"><span>98.4<small>%</small></span></div><div><b>High coverage</b><p>Pricing dimensions calculated</p><small><i/>No stale price records</small></div></div><div className="quality-list"><span><CheckCircle2/>EC2, EBS, EFS, FSx, RDS <b>100%</b></span><span><CheckCircle2/>NAT Gateway, ELB <b>98.7%</b></span><span><Info/>S3 request dimensions <b>87.2%</b></span></div><button className="text-button" onClick={()=>onGo("quality")}>Inspect data quality →</button></article></div>
  <article className="panel table-panel"><PanelHead title="Highest-cost resources" detail="Top 5 this period" action={<button onClick={()=>onGo("resources")}>View all</button>}/><ResourceTable rows={resources.slice(0,5)}/></article>
  </> }

function PanelHead({title,detail,action}:{title:string,detail:string,action?:React.ReactNode}) { return <div className="panel-head"><div><h2>{title}</h2><span>{detail}</span></div>{action}</div> }

function Resources({rows,query,setQuery,service,setService,region,setRegion}:{rows:Resource[];query:string;setQuery:(s:string)=>void;service:string;setService:(s:string)=>void;region:string;setRegion:(s:string)=>void}) { return <article className="panel resource-panel"><div className="filters"><label><Search size={17}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search name, ID, or team"/></label><select value={service} onChange={e=>setService(e.target.value)}>{["All services","EC2","EBS","EFS","FSx","RDS","S3","NAT","ELB"].map(x=><option key={x}>{x}</option>)}</select><select value={region} onChange={e=>setRegion(e.target.value)}>{["All regions","us-east-1","us-east-2"].map(x=><option key={x}>{x}</option>)}</select><span className="result-count">{rows.length} results</span></div><ResourceTable rows={rows}/>{rows.length===0&&<div className="empty"><Search/><b>No resources match</b><span>Clear a filter or try a different search.</span></div>}</article> }

function ResourceTable({rows}:{rows:Resource[]}) { return <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Service</th><th>Team</th><th>Region</th><th>State</th><th>Model</th><th>Confidence</th><th className="right">Estimated cost</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td><div className="resource-name"><span className={`resource-icon ${r.service.toLowerCase()}`}>{r.service==="EC2"?<Server/>:r.service==="EBS"?<HardDrive/>:<Database/>}</span><div><b>{r.name}</b><small>{r.id}</small></div></div></td><td>{r.service}</td><td>{r.team}</td><td>{r.region}</td><td><StateDot state={r.state}/></td><td>{r.model}</td><td><Confidence value={r.confidence}/></td><td className="right cost">{money.format(r.cost)}</td></tr>)}</tbody></table></div> }

function Teams() { return <div className="team-grid">{teams.map(t=>{const pct=t.cost/t.limit*100; const status=pct>=100?"Limit exceeded":pct>=t.threshold?"Warning":"Normal";return <article className="panel team-card" key={t.name}><div className="team-head"><span style={{background:t.color}}>{t.name.slice(0,2)}</span><div><h2>{t.name}</h2><p>Monthly • USD</p></div><span className={`pill ${pct>=100?"danger":pct>=80?"warning":"success"}`}>{status}</span></div><strong>{money.format(t.cost)} <small>/ {money.format(t.limit)}</small></strong><div className="progress"><i style={{width:`${Math.min(pct,100)}%`,background:t.color}}/><span style={{left:`${t.threshold}%`}}/></div><div className="limit-labels"><b>{pct.toFixed(1)}% used</b><span>{pct<100?`${money.format(t.limit-t.cost)} remaining`:`${money.format(t.cost-t.limit)} over limit`}</span></div><div className="threshold-row"><span>Alert thresholds</span><b>80%</b><b>100%</b></div><div className="delivery"><ShieldCheck/><div><b>Illustrative evaluation</b><span>Demo values • no live evaluation</span></div></div></article>})}</div> }

function Quality() { const rows=[{name:"EC2",coverage:100,fresh:"48 sec",source:"Price List + Spot history",status:"Verified"},{name:"EBS",coverage:100,fresh:"4 min",source:"Price List",status:"Verified"},{name:"EFS / FSx / RDS",coverage:100,fresh:"4 min",source:"Price List",status:"Verified"},{name:"NAT / ELB",coverage:98.7,fresh:"3 min",source:"Price List + CloudWatch",status:"Verified"},{name:"S3",coverage:87.2,fresh:"28 min",source:"Price List + CloudWatch",status:"Partial"}];return <><div className="guardrail"><ShieldCheck/><div><b>Planned production guardrails</b><span>Alerts are withheld when price coverage is incomplete, data is stale, or two consecutive evaluations do not agree.</span></div><span>NOT CONNECTED</span></div><article className="panel quality-table"><PanelHead title="Calculation coverage" detail="Freshness is tracked independently by source"/><div className="table-wrap"><table><thead><tr><th>Service</th><th>Coverage</th><th>Latest data</th><th>Pricing source</th><th>Status</th></tr></thead><tbody>{rows.map(r=><tr key={r.name}><td><b>{r.name}</b></td><td><div className="mini-progress"><i style={{width:`${r.coverage}%`}}/></div><b>{r.coverage}%</b></td><td>{r.fresh}</td><td>{r.source}</td><td><span className={`pill ${r.status==="Verified"?"success":"warning"}`}>{r.status}</span></td></tr>)}</tbody></table></div></article><div className="rules-grid"><article className="panel"><Gauge/><h2>Freshness gates</h2><p>Inventory, usage, on-demand catalog, and Spot history each have independent maximum ages. A stale input blocks alert publication.</p></article><article className="panel"><ShieldCheck/><h2>Exact price matching</h2><p>Ambiguous or missing SKU dimensions are rejected. Estimates never use a guessed price or convert a missing value to zero.</p></article><article className="panel"><Bell/><h2>Alarm confirmation</h2><p>Threshold crossings require two matching evaluations and a unique period + limit version + threshold key.</p></article></div></> }
