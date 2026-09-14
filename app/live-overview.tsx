"use client";
import {useEffect,useState} from "react";
import {request} from "./management";

type Overview = {
  account_name:string; last_collected_at:string|null; last_error:string|null;
  services:{service:string;resources:number}[];
  observed_costs:{basis:string;intervals:number;amount_usd:string|null}[];
  limits:{id:string;name:string;amount_usd:string;tag_key:string;tag_value:string;observed_subtotal_usd:string|null}[];
  limitations:string[];
};

export function LiveOverview({account,revision}:{account:string;revision:number}) {
  const [data,setData]=useState<Overview|null>(null);
  const [error,setError]=useState("");
  const [name,setName]=useState(""); const [key,setKey]=useState("Team");
  const [value,setValue]=useState(""); const [amount,setAmount]=useState("");
  const [saving,setSaving]=useState(false); const [saved,setSaved]=useState(0);
  useEffect(()=>{
    let active=true;
    request<Overview>(`/api/v1/accounts/${account}/overview`)
      .then(result=>{if(active){setData(result);setError("");}})
      .catch(error=>{if(active){setError(error.message);setData(null);}});
    return ()=>{active=false;};
  },[account,revision,saved]);
  return <section>
    <h2>Observed costs and team limits</h2>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>Loading overview…</p>}
    {data && <>
      <p>Last collection: {data.last_collected_at || "Not collected"}. Complete account cost: <strong>Unavailable</strong>.</p>
      {data.last_error && <p role="alert">Last collection reported incomplete data. Review collection details.</p>}
      <div className="table-wrap"><table><thead><tr><th>Service</th><th>Stored resources</th></tr></thead><tbody>{data.services.map(row=><tr key={row.service}><td>{row.service}</td><td>{row.resources}</td></tr>)}</tbody></table></div>
      <h3>Current UTC month — observed subtotal only</h3>
      <p>Two observations no more than ten minutes apart are required. These are polling estimates, not complete account spend.</p>
      {data.observed_costs.length === 0 && <p>No intervals recorded yet. Run two collections within ten minutes to begin.</p>}
      <div className="table-wrap"><table><thead><tr><th>Basis</th><th>Intervals</th><th>USD subtotal</th></tr></thead><tbody>{data.observed_costs.map(row=><tr key={row.basis}><td>{row.basis}</td><td>{row.intervals}</td><td>{row.amount_usd === null ? "Unavailable" : `$${Number(row.amount_usd).toFixed(6)}`}</td></tr>)}</tbody></table></div>
      <details><summary>Calculation exclusions</summary><ul>{data.limitations.map(item=><li key={item}>{item}</li>)}</ul></details>
      <h3>Monthly team limits</h3>
      <p>Limits are stored locally. Alarm decisions are withheld because account cost coverage is incomplete. No SNS or Lambda actions run.</p>
      {data.limits.map(limit=><p key={limit.id}><strong>{limit.name}</strong> — {limit.tag_key}={limit.tag_value}: observed subtotal ${limit.observed_subtotal_usd ?? "unavailable"}; monthly limit ${limit.amount_usd}. Status: evaluation withheld.</p>)}
      <form onSubmit={async event=>{
        event.preventDefault();setSaving(true);setError("");
        try {await request(`/api/v1/accounts/${account}/limits`,{method:"POST",body:JSON.stringify({name,tag_key:key,tag_value:value,amount_usd:amount})});setSaved(n=>n+1);setName("");setValue("");setAmount("");}
        catch(error){setError((error as Error).message);}finally{setSaving(false);}
      }}>
        <div className="onboarding-grid">
          <label>Limit name<input required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label>
          <label>Monthly limit (USD)<input required type="number" min="0.01" step="0.01" value={amount} onChange={e=>setAmount(e.target.value)}/></label>
          <label>Ownership tag key<input required value={key} onChange={e=>setKey(e.target.value)}/></label>
          <label>Ownership tag value<input required value={value} onChange={e=>setValue(e.target.value)}/></label>
        </div><button disabled={saving} type="submit">{saving?"Saving…":"Save local limit"}</button>
      </form>
    </>}
  </section>;
}
