const APPROVER = "olatejuakinlabi@gmail.com";
const TERMS_VERSION = "starter-access-2026-11-09-v1";
const TERMS = [
  "Michael Ayoade grants approved recipients permission to download, copy, run, modify and build the designated Dotmac Starter release for testing and production use, including hosted applications for their customers, without a license fee.",
  "Redistribution and sublicensing of Starter source, packages, container images or modified Starter copies are prohibited. Customers may access your hosted application.",
  "Keep applicable copyright and license notices. Separately licensed components retain their own terms and any rights those terms grant.",
  "Permission applies only to the release identified at download. A request or approval grants no rights in code awaiting release clearance.",
  "The software is provided as is, without warranty to the extent permitted by law. Support and updates are optional. On breach, permission ends and you must stop using and delete covered copies, subject to independent third-party rights.",
].join("\n\n");
// Cleared releases must be installed through a reviewed deployment.
// There is deliberately no browser endpoint that can bypass this gate.
const CLEARED_RELEASE = null;

const escape = value => String(value).replace(/[&<>"']/g, char =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
const headers = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};
function page(body, status = 200) {
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Dotmac Starter access</title><style>
  *{box-sizing:border-box}body{margin:0;background:#f1f5fa;color:#182b43;font:16px/1.65 system-ui,sans-serif}
  main{max-width:720px;margin:36px auto;padding:28px;background:white;border-radius:20px}
  h1{line-height:1.15}label{display:block;margin-top:18px}input{font:inherit;padding:10px;width:100%;border:1px solid #91a2b7;border-radius:6px}
  input[type=checkbox]{width:auto}button,a.button{font:inherit;display:inline-block;padding:12px 18px;background:#174fb4;color:white;border:0;border-radius:8px;margin:16px 0;cursor:pointer}
  small{color:#405674}article{padding:14px 0;border-bottom:1px solid #dce3ed}a{color:#154ca9}pre{white-space:pre-wrap}
  </style><main><p>Dotmac Starter</p>${body}</main></html>`, {status, headers});
}
async function digest(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(x => x.toString(16).padStart(2,"0")).join("");
}
function identity(request) {
  const id = request.headers.get("oai-authenticated-user-id");
  const email = request.headers.get("oai-authenticated-user-email");
  return id && email ? {id, email} : null;
}
async function read(bucket, key) {
  const object = await bucket.get(key);
  return object ? object.json() : null;
}
function redirect(path) {
  return new Response(null, {status:303, headers:{Location:path, "Cache-Control":"no-store"}});
}
function termsHtml(hash) {
  return `<details open><summary>Terms · ${TERMS_VERSION}</summary>
  ${TERMS.split("\n\n").map(text => `<p>${escape(text)}</p>`).join("")}
  <small>Terms SHA-256 · ${hash}</small></details>`;
}
async function handle(request, env) {
  const url = new URL(request.url);
  if (!["GET","POST"].includes(request.method)) return page("Method unavailable.",405);
  if (request.method === "POST" && request.headers.get("Origin") !== url.origin)
    return page("Please submit this form from this site.",403);
  const user = identity(request);
  const termsHash = await digest(TERMS);
  if (!user) {
    if (request.method !== "GET") return page("Sign in before submitting.",401);
    return page(`<h1>Build with Dotmac Starter.</h1>
    <p>Free testing, modification, production use and hosted customer apps. Redistribution of Starter is prohibited.</p>
    <p>One request, terms acceptance and approval before download access.</p>
    <a class="button" target="_top" href="/signin-with-chatgpt?return_to=%2F">Sign in to request access</a>
    <p>Release clearance is in progress. You can join the waiting list now.</p>`);
  }
  if (!env.BUCKET) return page("Request storage is unavailable. Please try again later.",503);
  const key = await digest(user.id);
  const requestKey = "requests/" + key + ".json";
  const current = await read(env.BUCKET, requestKey);
  const approval = await read(env.BUCKET, "approvals/" + key + ".json");
  const admin = user.email.toLowerCase() === APPROVER;
  if (url.pathname === "/admin") {
    if (!admin) return page("Approval access is restricted.",403);
    if (request.method === "POST") {
      const form = await request.formData();
      const applicant = String(form.get("applicant") || "");
      if (!/^[a-f0-9]{64}$/.test(applicant)) return page("Invalid request.",400);
      const record = await read(env.BUCKET, "requests/" + applicant + ".json");
      if (!record || record.requestId !== form.get("requestId") || record.termsHash !== termsHash)
        return page("This request changed. Review it again.",409);
      const decision = form.get("decision");
      if (!["approved","denied"].includes(decision)) return page("Invalid decision.",400);
      await env.BUCKET.put("approvals/" + applicant + ".json", JSON.stringify({
        requestId:record.requestId, termsHash, decision,
        approver:user.email, approverId:user.id, decidedAt:new Date().toISOString(),
      }));
      return redirect("/admin");
    }
    const cursor = url.searchParams.get("cursor") || undefined;
    const list = await env.BUCKET.list({prefix:"requests/",limit:100,cursor});
    const records = await Promise.all(list.objects.map(object => read(env.BUCKET, object.key)));
    const cards = records.filter(Boolean).map(record => `<article><strong>${escape(record.name)}</strong>
      <p>${escape(record.email)} · GitHub ${escape(record.github || "not supplied")}</p>
      <p>Accepted ${escape(record.acceptedAt)} · ${escape(record.termsVersion)}</p>
      <form method="post"><input type="hidden" name="applicant" value="${record.key}">
      <input type="hidden" name="requestId" value="${record.requestId}">
      <button name="decision" value="approved">Approve</button>
      <button name="decision" value="denied">Deny / revoke</button></form></article>`).join("");
    return page(`<h1>Access requests</h1><p>Signed in as ${escape(user.email)}.</p>
      <p>Approval is recorded now. Downloads also require a cleared release and matching terms acceptance.</p>
      ${cards || "<p>No requests yet.</p>"}
      ${list.truncated ? `<a href="/admin?cursor=${encodeURIComponent(list.cursor)}">Next requests</a>` : ""}
      <p><a href="/">My request</a></p>`);
  }
  if (url.pathname === "/download") {
    if (!CLEARED_RELEASE) return page("Downloads await release clearance.",403);
    if (!current || current.termsHash !== termsHash || !approval ||
      approval.decision !== "approved" || approval.requestId !== current.requestId ||
      approval.termsHash !== termsHash) return page("Accepted terms and approval are required.",403);
    const artifact = await env.BUCKET.get(CLEARED_RELEASE.key);
    if (!artifact || artifact.customMetadata?.sha256 !== CLEARED_RELEASE.sha256)
      return page("The approved artifact is unavailable.",503);
    return new Response(artifact.body, {headers:{
      "Content-Type":"application/octet-stream",
      "Content-Disposition":'attachment; filename="dotmac-starter.tar"',
      "Cache-Control":"private, no-store",
      "X-Content-Type-Options":"nosniff",
    }});
  }
  if (url.pathname !== "/") return page("Page unavailable.",404);
  if (request.method === "POST") {
    const form = await request.formData();
    const name = String(form.get("name") || "").trim();
    const github = String(form.get("github") || "").trim();
    if (!name || name.length > 120 || (github && !/^[a-z0-9-]{1,39}$/i.test(github)) ||
      form.get("accepted") !== "yes" || form.get("termsHash") !== termsHash)
      return page("Enter your name and accept the current terms.",400);
    // Repeated submissions retain their original acceptance and approval.
    if (current?.termsHash === termsHash) return redirect("/");
    await env.BUCKET.put(requestKey, JSON.stringify({
      key, requestId:crypto.randomUUID(), userId:user.id, email:user.email, name, github,
      termsVersion:TERMS_VERSION, termsHash, termsText:TERMS, acceptedAt:new Date().toISOString(),
    }));
    return redirect("/");
  }
  const accepted = current?.termsHash === termsHash;
  const approved = accepted && approval?.decision === "approved" && approval.requestId === current.requestId;
  return page(`<h1>Request Starter access</h1><p>Signed in as ${escape(user.email)}.</p>
    ${accepted ? `<p>Your acceptance is recorded. Status · ${approved ? "approved" : approval?.decision === "denied" ? "not approved" : "awaiting review"}.</p>
    <p>Release clearance is in progress. Return here for download availability.</p>` :
    `<p>Free production use, modification and hosted customer apps. No Starter redistribution.</p>
    <form method="post"><label>Your name<input name="name" autocomplete="name" required maxlength="120"></label>
    <label>GitHub username (optional)<input name="github" maxlength="39" pattern="[A-Za-z0-9-]+"></label>
    <p>We use your sign-in email and these details to review and manage access. Acceptance and approval records are retained for license administration. Contact the approver to request deletion or end access.</p>
    ${termsHtml(termsHash)}<input type="hidden" name="termsHash" value="${termsHash}">
    <label><input type="checkbox" name="accepted" value="yes" required> I accept these terms.</label>
    <button>Accept and request access</button></form>`}
    ${approved && CLEARED_RELEASE ? '<a class="button" href="/download">Download approved release</a>' : ""}
    ${admin ? '<p><a href="/admin">Review requests</a></p>' : ""}`);
}
export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch { return page("The request could not be completed. Please try again.",503); }
  },
};
