// Snapshot of deployed Sites source 36a7fe6; identities and storage here are
// synthetic. The hosting identity boundary is checked separately over HTTPS.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('./deployed-worker-36a7fe6.mjs', import.meta.url), 'utf8');
const worker = (await import('./deployed-worker-36a7fe6.mjs')).default;
const released = (await import('data:text/javascript;base64,' + Buffer.from(source.replace(
  'const CLEARED_RELEASE = null;',
  'const CLEARED_RELEASE = {key:"release.tar", sha256:"test-checksum"};'
)).toString('base64'))).default;
const origin = 'https://test.invalid';
const applicant = {id:'synthetic-applicant', email:'applicant@example.test'};
const approver = {id:'synthetic-approver', email:'olatejuakinlabi@gmail.com'};
class Bucket {
  records = new Map();
  async get(key) {
    const text = this.records.get(key);
    if (text === undefined) return null;
    if (key === 'release.tar') return {body:text, customMetadata:{sha256:'test-checksum'}};
    return {json:async () => JSON.parse(text)};
  }
  async put(key, text) { this.records.set(key, text); }
  async list({prefix}) {
    return {objects:[...this.records.keys()].filter(key => key.startsWith(prefix)).map(key => ({key})), truncated:false};
  }
}
function request(path, user, data, from = origin) {
  const headers = {};
  if (user) {
    headers['oai-authenticated-user-id'] = user.id;
    headers['oai-authenticated-user-email'] = user.email;
  }
  if (data) headers.Origin = from;
  return new Request(origin + path, {method:data ? 'POST' : 'GET', headers,
    ...(data ? {body:new URLSearchParams(data)} : {})});
}
async function setup() {
  const BUCKET = new Bucket();
  const env = {BUCKET};
  const form = await (await worker.fetch(request('/', applicant), env)).text();
  const termsHash = form.match(/name="termsHash" value="([a-f0-9]+)"/)[1];
  const data = {name:'Synthetic tester', github:'test-user', accepted:'yes', termsHash};
  return {BUCKET, env, data};
}
async function accepted() {
  const state = await setup();
  assert.equal((await worker.fetch(request('/', applicant, state.data), state.env)).status,303);
  state.record = JSON.parse([...state.BUCKET.records.values()][0]);
  state.decision = {applicant:state.record.key, requestId:state.record.requestId, decision:'approved'};
  return state;
}
test('anonymous visits cannot request, approve or download', async () => {
  const env = {BUCKET:new Bucket()};
  for (const path of ['/', '/admin', '/download'])
    assert.match(await (await worker.fetch(request(path), env)).text(), /Sign in to request access/);
  assert.equal((await worker.fetch(request('/', null, {accepted:'yes'}),env)).status,401);
  assert.equal(env.BUCKET.records.size,0);
});
test('terms checkbox, current hash and same-origin submission are mandatory', async () => {
  const {env,data,BUCKET} = await setup();
  for (const bad of [{...data,accepted:''}, {...data,termsHash:'obsolete'}])
    assert.equal((await worker.fetch(request('/',applicant,bad),env)).status,400);
  assert.equal((await worker.fetch(request('/',applicant,data,'https://attacker.invalid'),env)).status,403);
  assert.equal(BUCKET.records.size,0);
});
test('acceptance retains exact text, authenticated identity, hash and server time', async () => {
  const {record,data} = await accepted();
  assert.equal(record.email,applicant.email);
  assert.equal(record.userId,applicant.id);
  assert.equal(record.termsHash,data.termsHash);
  const hash = await crypto.subtle.digest('SHA-256',new TextEncoder().encode(record.termsText));
  assert.equal(Buffer.from(hash).toString('hex'),record.termsHash);
  assert.match(record.acceptedAt,/^\d{4}-\d{2}-\d{2}T/);
  assert.match(record.termsText,/production use/);
  assert.match(record.termsText,/Redistribution and sublicensing/);
});
test('only designated approver can approve; stale request IDs are refused', async () => {
  const {env,decision,BUCKET} = await accepted();
  assert.equal((await worker.fetch(request('/admin',applicant,decision),env)).status,403);
  assert.equal((await worker.fetch(request('/admin',approver,{...decision,requestId:'stale'}),env)).status,409);
  assert.equal([...BUCKET.records.keys()].filter(key => key.startsWith('approvals/')).length,0);
});
test('downloads require acceptance and matching approval, and revocation takes effect', async () => {
  const {env,decision,record,BUCKET,data} = await accepted();
  BUCKET.records.set('release.tar','synthetic archive');
  assert.equal((await released.fetch(request('/download',applicant),env)).status,403);
  assert.equal((await worker.fetch(request('/admin',approver,decision),env)).status,303);
  const approval = JSON.parse(BUCKET.records.get('approvals/'+record.key+'.json'));
  assert.equal(approval.approver,approver.email);
  assert.equal(approval.requestId,record.requestId);
  assert.equal(approval.termsHash,data.termsHash);
  assert.match(approval.decidedAt,/^\d{4}-\d{2}-\d{2}T/);
  const response = await released.fetch(request('/download',applicant),env);
  assert.equal(response.status,200);
  assert.equal(await response.text(),'synthetic archive');
  // Actual deployed release gate must remain closed even after approval.
  assert.equal((await worker.fetch(request('/download',applicant),env)).status,403);
  await worker.fetch(request('/',applicant,data),env);
  assert.equal(JSON.parse(BUCKET.records.get('requests/'+record.key+'.json')).requestId,record.requestId);
  await worker.fetch(request('/admin',approver,{...decision,decision:'denied'}),env);
  assert.equal((await released.fetch(request('/download',applicant),env)).status,403);
});
test('changed acceptance or wrong approval hash cannot reuse an old approval', async () => {
  const {env,decision,record,BUCKET} = await accepted();
  await worker.fetch(request('/admin',approver,decision),env);
  BUCKET.records.set('release.tar','synthetic archive');
  BUCKET.records.set('requests/'+record.key+'.json',JSON.stringify({...record,requestId:'replacement'}));
  assert.equal((await released.fetch(request('/download',applicant),env)).status,403);
  BUCKET.records.set('requests/'+record.key+'.json',JSON.stringify(record));
  const key = 'approvals/'+record.key+'.json';
  BUCKET.records.set(key,JSON.stringify({...JSON.parse(BUCKET.records.get(key)),termsHash:'obsolete'}));
  assert.equal((await released.fetch(request('/download',applicant),env)).status,403);
});
