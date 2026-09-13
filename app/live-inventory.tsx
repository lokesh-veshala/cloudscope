"use client";

import { useEffect, useMemo, useState } from "react";
import { request } from "./management";
import { LiveOverview } from "./live-overview";

type Resource = {
  id: string; name: string; provider_resource_id: string;
  provider_resource_type: string; region: string; state: string; last_seen: string;
  metadata: { tags?: Record<string, string>; pricing?: {status?: string; usd_per_hour?: string; source?: string; fetched_at?: string} };
};

export function LiveInventory({accounts}: {accounts: {id: string; display_name: string}[]}) {
  const [account, setAccount] = useState("");
  const [rows, setRows] = useState<Resource[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!account) return;
    let active = true;
    request<Resource[]>(`/api/v1/accounts/${account}/resources`)
      .then(result => {if(active) setRows(result);})
      .catch(error => {if(active) setError(error.message);})
      .finally(() => {if(active) setLoading(false);});
    return () => {active = false;};
  }, [account, revision]);
  const filtered = useMemo(() => rows.filter(row =>
    [row.name,row.provider_resource_id,row.provider_resource_type,row.region,JSON.stringify(row.metadata.tags || {})]
      .join(" ").toLowerCase().includes(query.toLowerCase())), [rows,query]);
  return <article className="panel management">
    <h2>Live inventory</h2>
    <p>Stored observations from your collector. Rates below are not month-to-date spend. Missing prices are unavailable, not zero.</p>
    <label>Registered account<select value={account} onChange={event => {setAccount(event.target.value);setRows([]);setError("");setLoading(Boolean(event.target.value));}}>
      <option value="">Select an account</option>{accounts.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}
    </select></label>
    <label>Search resources or tags<input value={query} onChange={event => setQuery(event.target.value)}/></label>
    <button disabled={!account || loading} onClick={() => {setLoading(true);setError("");setRows([]);setRevision(value => value + 1);}}>Reload stored inventory</button>
    <p role="status">{loading ? "Loading stored inventory…" : account ? `${rows.length} stored resources; ${filtered.length} match search.` : "Select an account to view live records."}</p>
    {error && <p role="alert">{error}</p>}
    {account && <LiveOverview key={account} account={account} revision={revision}/>}
    {!loading && account && !error && <div className="table-wrap"><table><thead><tr><th>Resource</th><th>Service</th><th>Region</th><th>Observed state</th><th>Last observed (UTC)</th><th>USD/hour</th><th>Rate basis</th></tr></thead><tbody>
      {filtered.map(row => {
        const price = row.metadata.pricing;
        const supported = price?.status === "COMPLETE" || price?.status === "ESTIMATED";
        return <tr key={row.id}><td>{row.name}<br/><small>{row.provider_resource_id}</small></td><td>{row.provider_resource_type}</td><td>{row.region}</td><td>{row.state}</td><td>{row.last_seen}</td><td>{supported ? price?.usd_per_hour : "Unavailable"}</td><td>{price?.status === "ESTIMATED" ? "Assumed 42% Spot discount; not alarm eligible" : price?.status === "COMPLETE" ? "Matched EC2 list rate; not total cost" : "Not priced"}</td></tr>;
      })}
    </tbody></table></div>}
  </article>;
}
