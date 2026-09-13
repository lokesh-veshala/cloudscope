"use client";

import { useCallback, useEffect, useState } from "react";
import { buildAwsOnboardingTemplate, validateAwsOnboarding } from "./aws-onboarding";

type Account = { id:string; provider_account_id:string; display_name:string; region:string; credential_profile:string; connection_status:"UNTESTED"|"CONNECTED"|"FAILED"; last_error?:string };
function apiUrl(path:string) {
  const configured=process.env.NEXT_PUBLIC_CLOUDSCOPE_API_URL;
  if(configured) return configured.replace(/\/$/,"")+path;
  if(typeof window!=="undefined") return `${window.location.protocol}//${window.location.hostname}:8000${path}`;
  return `http://127.0.0.1:8000${path}`;
}

async function request<T>(path:string,options?:RequestInit):Promise<T>{
  const response=await fetch(apiUrl(path),{...options,headers:{"Content-Type":"application/json",...options?.headers}});
  const body=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(body.detail||`Request failed (${response.status})`);
  return body;
}

export function Accounts() {
  const [accountName,setAccountName]=useState(""); const [accountId,setAccountId]=useState(""); const [region,setRegion]=useState("us-east-1");
  const [cn,setCn]=useState("cloudscope-test-collector"); const [certificate,setCertificate]=useState("");
  const [profileName,setProfileName]=useState("cloudscope-test"); const [roleArn,setRoleArn]=useState("");
  const [profileArn,setProfileArn]=useState(""); const [anchorArn,setAnchorArn]=useState(""); const [snsArn,setSnsArn]=useState("");
  const [accounts,setAccounts]=useState<Account[]>([]); const [details,setDetails]=useState<Record<string,unknown>>({});
  const [message,setMessage]=useState(""); const [busy,setBusy]=useState("");

  const loadAccounts=useCallback(async()=>{try{setAccounts(await request<Account[]>("/api/v1/accounts"));}catch(error){setMessage(`Backend unavailable: ${(error as Error).message}`);}},[]);
  useEffect(()=>{void loadAccounts();},[loadAccounts]);

  const generate=()=>{const input={accountName,accountId,region,certificateCommonName:cn,caCertificatePem:certificate};const error=validateAwsOnboarding(input);if(error){setMessage(error);return;}const blob=new Blob([buildAwsOnboardingTemplate(input)],{type:"application/x-yaml"});const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download=`cloudscope-${accountName.trim().toLowerCase().replace(/[^a-z0-9]+/g,"-")}-onboarding.yaml`;link.click();URL.revokeObjectURL(url);setMessage("Template generated. Deploy it only in the account and region entered above.");};

  const register=async()=>{setBusy("register");setMessage("");try{await request("/api/v1/accounts",{method:"POST",body:JSON.stringify({display_name:accountName,provider_account_id:accountId,region,credential_profile:profileName,role_arn:roleArn,roles_anywhere_profile_arn:profileArn,trust_anchor_arn:anchorArn,sns_topic_arn:snsArn||null})});setMessage("Account registered locally. Run Test connection before collecting.");await loadAccounts();}catch(error){setMessage((error as Error).message);}finally{setBusy("");}};
  const action=async(account:Account,name:"test-connection"|"collect")=>{setBusy(`${account.id}:${name}`);setMessage("");try{const result=await request(`/api/v1/accounts/${account.id}/${name}`,{method:"POST"});setDetails(current=>({...current,[account.id]:result}));setMessage(name==="collect"?"Collection finished. Review its verified and unresolved results.":"Connection test finished.");await loadAccounts();}catch(error){setDetails(current=>({...current,[account.id]:(error as Error).message}));}finally{setBusy("");}};

  return <div className="management-stack">
    <article className="panel management"><h2>Connected AWS accounts</h2><p>Account metadata and inventory remain in this installation&apos;s PostgreSQL database.</p>
      {accounts.length===0&&<p>No account is registered with the live backend.</p>}
      {accounts.map(account=><section className="account-row" key={account.id}><div><b>{account.display_name}</b><span>{account.provider_account_id} · {account.region}</span><small>Profile: {account.credential_profile}</small></div><span className={`pill ${account.connection_status==="CONNECTED"?"success":account.connection_status==="FAILED"?"danger":"warning"}`}>{account.connection_status}</span><button disabled={Boolean(busy)} onClick={()=>void action(account,"test-connection")}>{busy===`${account.id}:test-connection`?"Testing…":"Test connection"}</button><button disabled={Boolean(busy)||account.connection_status!=="CONNECTED"} onClick={()=>void action(account,"collect")}>{busy===`${account.id}:collect`?"Collecting…":"Collect now"}</button>{details[account.id]!==undefined&&<pre>{typeof details[account.id]==="string"?String(details[account.id]):JSON.stringify(details[account.id],null,2)}</pre>}</section>)}
    </article>
    <article className="panel management"><h2>1. Generate the AWS stack</h2><p>The public CA certificate is embedded in CloudFormation. Private keys stay on this VM.</p><div className="onboarding-grid"><label>Account name<input value={accountName} onChange={e=>setAccountName(e.target.value)} placeholder="Engineering Sandbox"/></label><label>AWS account ID<input value={accountId} onChange={e=>setAccountId(e.target.value.replace(/\D/g,"").slice(0,12))} inputMode="numeric" placeholder="123456789012"/></label><label>AWS region<input value={region} onChange={e=>setRegion(e.target.value)} placeholder="us-east-1"/></label><label>Collector certificate CN<input value={cn} onChange={e=>setCn(e.target.value)}/></label><label className="full">Public CA certificate (PEM)<textarea value={certificate} onChange={e=>setCertificate(e.target.value)} rows={6} placeholder={'-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----'}/><small>Never paste a private key, access key, secret, or session token.</small></label></div><button type="button" onClick={generate}>Download CloudFormation template</button></article>
    <article className="panel management"><h2>2. Register deployed stack outputs</h2><p>These identifiers stay in the local database. The named AWS profile must be mounted into the API container.</p><div className="onboarding-grid"><label>AWS profile name<input value={profileName} onChange={e=>setProfileName(e.target.value)} placeholder="cloudscope-test"/></label><label>Collector role ARN<input value={roleArn} onChange={e=>setRoleArn(e.target.value)} placeholder="arn:aws:iam::123456789012:role/..."/></label><label>Roles Anywhere profile ARN<input value={profileArn} onChange={e=>setProfileArn(e.target.value)} placeholder="arn:aws:rolesanywhere:us-east-1:123456789012:profile/..."/></label><label>Trust anchor ARN<input value={anchorArn} onChange={e=>setAnchorArn(e.target.value)} placeholder="arn:aws:rolesanywhere:us-east-1:123456789012:trust-anchor/..."/></label><label className="full">SNS topic ARN (optional)<input value={snsArn} onChange={e=>setSnsArn(e.target.value)} placeholder="arn:aws:sns:us-east-1:123456789012:cloudscope-..."/></label></div><button type="button" disabled={Boolean(busy)} onClick={()=>void register()}>{busy==="register"?"Registering…":"Register account locally"}</button><p role="status">{message}</p></article>
  </div>;
}

export function AlertHistory(){return <article className="panel management"><h2>Alert history</h2><p>No real alerts have been sent. Alert evaluation remains disabled until complete usage intervals and pricing coverage exist.</p></article>;}
export function Settings(){const[density,setDensity]=useState("comfortable");const[message,setMessage]=useState("");return <article className="panel management"><h2>Display settings</h2><p>Device-local preferences only.</p><form onSubmit={e=>{e.preventDefault();try{localStorage.setItem("cloudscope-density",density);document.documentElement.dataset.density=density;setMessage("Display preference saved on this device.");}catch{setMessage("Your browser blocked saving this preference.");}}}><label>Table spacing<select value={density} onChange={e=>setDensity(e.target.value)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label><button type="submit">Save display preference</button><p role="status">{message}</p></form></article>;}
