"use client";
import { useState } from "react";
export function Accounts() {
  return <article className="panel management"><h2>Cloud accounts</h2><p>No AWS accounts are connected. Shared Engineering is the sample workspace, not a verified AWS account.</p><h3>Live onboarding prerequisites</h3><ol><li>Configure the on-premises collector and IAM Roles Anywhere certificate.</li><li>Configure the account ID, role ARN, profile ARN, and trust anchor ARN on the collector.</li><li>Validate identity, inventory, pricing, and usage permissions.</li><li>Approve an SNS topic before enabling notifications.</li></ol><p>Account registration and connection testing are not implemented yet. Do not paste private keys or access keys into this demo.</p></article>;
}
export function AlertHistory() {
  return <article className="panel management"><h2>Alert history</h2><p>No real alerts have been sent. Live SNS delivery is disabled.</p><p>The warning cards in the demo are illustrative scenarios, not delivery receipts. There are no SNS message IDs or Lambda invocation results to show.</p></article>;
}
export function Settings() {
  const [density,setDensity]=useState("comfortable");
  const [message,setMessage]=useState("");
  return <article className="panel management"><h2>Display settings</h2><p>Device-local preferences only. This does not configure collectors, team limits, or alarm delivery.</p><form onSubmit={e=>{e.preventDefault();try {localStorage.setItem("cloudscope-density",density);document.documentElement.dataset.density=density;setMessage("Display preference saved on this device.");}catch{setMessage("Your browser blocked saving this preference.");}}}><label>Table spacing<select value={density} onChange={e=>setDensity(e.target.value)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label><button type="submit">Save display preference</button><button type="button" onClick={()=>{try{const value=localStorage.getItem("cloudscope-density")==="compact"?"compact":"comfortable";setDensity(value);document.documentElement.dataset.density=value;setMessage("Saved preference loaded and applied.");}catch{setMessage("Saved preferences unavailable.");}}}>Load saved preference</button><p role="status">{message}</p></form><h3>Production configuration</h3><p>Collection scheduling, RBAC, pricing refresh, and SNS configuration remain unavailable in this demo.</p></article>;
}
