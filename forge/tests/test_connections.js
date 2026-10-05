/* Offline component checks with a deliberately minimal DOM/API fixture.
 * These assertions do not establish browser layout, Ingress or real SSH behavior. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
const source=fs.readFileSync(path.join(root,'static/connections.js'),'utf8');
const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
let checks=0;
function check(value,message){assert.ok(value,message);checks++;}
class Element{
  constructor(){this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.children=[];this.events={};this.attributes={};this.classes=new Set();this.classList={toggle:(k,v)=>v?this.classes.add(k):this.classes.delete(k)};}
  addEventListener(k,fn){this.events[k]=fn;}
  setAttribute(k,v){this.attributes[k]=v;}
  append(...nodes){this.children.push(...nodes);if(this.value===''&&nodes[0]?.value)this.value=nodes[0].value;}
  replaceChildren(...nodes){this.children=nodes;this.value='';}
  focus(){this.focused=true;}
  select(){this.selected=true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  reportValidity(){return true;}
  reset(){}
}
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture({pending=null,items=[],available=true,createMode=null,scope="repository",access="read"}={}){
  const elements=new Map([...ids].map(id=>[id,new Element()]));
  const $=id=>{assert.ok(elements.has(id),'actual HTML contains '+id);return elements.get(id);};
  const saved=new Map(pending?[['ssh.pending',clone(pending)]]:[]),calls=[],confirmations=[];
  const data={items:clone(items),available,createMode,nextStatus:null};
  const storage={get:k=>saved.get(k)||null,set:(k,v)=>saved.set(k,clone(v)),remove:k=>saved.delete(k)};
  const uuid='12345678-1234-4234-8234-123456789abc',pub='ssh-ed25519 PUBLIC_FIXTURE_NOT_A_KEY',fp='SHA256:INDEPENDENT_FIXTURE';
  const api=async(url,body)=>{
    calls.push({url,body:body===undefined?undefined:clone(body)});
    if(body===undefined)return {connections:clone(data.items),available:data.available};
    if(data.nextStatus){const e=new Error('fixture rejected');e.status=data.nextStatus;data.nextStatus=null;throw e;}
    if(url==='api/git/connections'){
      if(data.createMode==='lost_before')throw new Error('response uncertain');
      let item=data.items.find(x=>x.id===body.request_id.replaceAll('-',''));
      if(!item){item={...body,id:body.request_id.replaceAll('-',''),revision:1,state:'ready',enabled:true,public_key:pub,fingerprint:'SHA256:KEY_FIXTURE',host_fingerprint:null};data.items.push(item);}
      if(data.createMode==='lost_after')throw new Error('response uncertain');
      return clone(item);
    }
    const match=url.match(/\/([a-f0-9]{32})\/(scan|pin|test|enabled|access)$/);assert.ok(match,'known route');
    const item=data.items.find(x=>x.id===match[1]);assert.ok(item,'approved connection');
    if(match[2]==='scan')return {candidates:[{public_key:pub,fingerprint:fp}],revision:item.revision,trusted:false};
    if(match[2]==='test')return {refs:[{ref:'refs/heads/main',commit:'a'.repeat(40)}],truncated:false};
    assert.equal(body.revision,item.revision);item.revision++;
    if(match[2]==='pin'){assert.equal(body.expected_fingerprint,fp);item.host_fingerprint=fp;}
    else if(match[2]==='access'){item.access=body.access;item.scope=item.scope||'repository';}
    else item.enabled=body.enabled;
    return clone(item);
  };
  $("ssh-scope").value=scope;$("ssh-create-access").value=access;
  const context={confirm:text=>{confirmations.push(text);return true;},navigator:{clipboard:{writeText:async text=>{data.copied=text;}}}};
  vm.createContext(context);vm.runInContext(source+'\nglobalThis.Component=GitConnections;',context);
  const ui=new context.Component({$,el:(tag,text,cls)=>{const e=new Element();e.tag=tag;e.textContent=text||'';e.className=cls||'';return e;},api,key:()=>uuid,storage,toast:text=>{data.toast=text;},closeNav:()=>{data.navClosed=true;}});
  for(const [id,value] of Object.entries({name:'Workshop',host:'github.com',port:'22',username:'git',repository:'OWNER/REPO.git'}))$('ssh-'+id).value=value;
  return {ui,$,saved,calls,data,confirmations,uuid,fp};
}
(async()=>{
  const f=fixture();
  check(f.calls.length===0,'constructing settings does not call API');
  await f.ui.open();
  check(f.$('connections-dialog').open&&f.data.navClosed,'dialog opens and mobile navigation closes');
  check(f.calls.length===1&&f.calls[0].body===undefined,'opening performs only read reconciliation');
  check(f.$('ssh-detail').hidden,'empty state hides detail');
  check(f.$('ssh-test').disabled,'unconfigured test disabled');
  await f.ui.create(false);
  check(f.data.items.length===1,'one explicit create');
  check(f.calls.filter(x=>x.body!==undefined).length===1,'no additional mutation on successful create');
  check(!f.saved.has('ssh.pending')&&!f.ui.pending,'completed request marker cleared');
  check(f.$('ssh-public-key').value==='ssh-ed25519 PUBLIC_FIXTURE_NOT_A_KEY','public key displayed');
  check(f.$('ssh-test').disabled&&!f.$('ssh-scan').disabled,'read test requires pin but scanning allowed');
  await f.ui.copyKey();check(f.data.copied===f.$('ssh-public-key').value,'only public key copied');
  await f.ui.scan();
  check(f.ui.candidates.length===1&&!f.$('ssh-host-review').hidden,'untrusted candidate exposed for review');
  check(!f.data.items[0].host_fingerprint&&f.$('ssh-pin').disabled,'scan never automatically pins');
  f.$('ssh-expected-fingerprint').value='SHA256:wrong';f.ui.pinState();
  check(f.$('ssh-pin').disabled,'mismatched fingerprint blocks pin');
  const before=f.calls.length;await f.ui.pin();check(f.calls.length===before,'mismatched fingerprint cannot dispatch pin');
  f.$('ssh-expected-fingerprint').value=f.fp;f.ui.pinState();check(!f.$('ssh-pin').disabled,'matching independently entered fingerprint permits explicit pin');
  await f.ui.pin();check(f.data.items[0].host_fingerprint===f.fp,'explicit pin saved');
  check(!f.$('ssh-test').disabled,'pinned ready connection can be tested');
  await f.ui.test();check(f.$('ssh-message').textContent.includes('Read access only'),'read-only result scope explicit');
  await f.ui.enable();check(!f.data.items[0].enabled,'explicit disable');
  check(f.$('ssh-test').disabled&&f.$('ssh-scan').disabled,'disabled connection blocks network buttons');
  check(f.data.items[0].public_key==='ssh-ed25519 PUBLIC_FIXTURE_NOT_A_KEY','disable retains key');
  await f.ui.enable();check(f.data.items[0].enabled&&f.confirmations.length===2,'reenable requires confirmation');
  check(f.calls.every(x=>!x.url.includes('clone')&&!x.url.includes('push')),'settings never clones or pushes');
  f.ui.lock(true);check(f.$('ssh-create').disabled&&f.$('refresh-connections').disabled,'busy guards');f.ui.lock(false);
  f.data.items[0].name='<img src=x onerror=alert(1)>';await f.ui.refresh();
  check(f.$('ssh-detail-name').textContent==='<img src=x onerror=alert(1)>','hostile label kept literal');
  check(f.$('ssh-detail-name').children.length===0,'no HTML rendering of labels');
  const accepted=fixture({createMode:'lost_after'});await accepted.ui.refresh();await accepted.ui.create(false);
  check(accepted.data.items.length===1&&!accepted.ui.pending,'lost accepted create reconciles by GET');
  check(accepted.calls.filter(x=>x.body!==undefined).length===1,'lost response not retried automatically');
  const uncertain=fixture({createMode:'lost_before'});await uncertain.ui.refresh();await uncertain.ui.create(false);
  check(!!uncertain.ui.pending&&uncertain.saved.has('ssh.pending'),'uncertain absent create keeps original payload');
  check(!uncertain.$('ssh-pending').hidden&&uncertain.$('ssh-create-fields').disabled,'uncertain recovery controls protect frozen inputs');
  const original=JSON.stringify(uncertain.ui.pending);await uncertain.ui.refresh();
  check(uncertain.calls.filter(x=>x.body!==undefined).length===1,'refresh never retries absent creation');
  uncertain.data.createMode=null;await uncertain.ui.create(true);
  const posts=uncertain.calls.filter(x=>x.body!==undefined);
  check(posts.length===2&&JSON.stringify(posts[1].body)===original,'explicit retry uses exact ID and original fields');
  check(uncertain.confirmations.length===1&&uncertain.data.items.length===1,'retry is confirmed and creates only one record');
  const bad=fixture({pending:{unexpected:'cached'}});await bad.ui.refresh();
  check(bad.$('ssh-message').textContent.includes('No remote hosts'),'malformed cached pending object does not break read');
  const unavailable=fixture({available:false});await unavailable.ui.refresh();await unavailable.ui.create(false);
  check(unavailable.$('ssh-create').disabled&&unavailable.calls.every(x=>x.body===undefined),'missing clients fail closed without create');
  const validation=fixture();await validation.ui.refresh();validation.data.nextStatus=400;await validation.ui.create(false);
  check(!validation.ui.pending&&!validation.saved.has('ssh.pending'),'certain preallocation validation clears marker');
  const item={id:'f'.repeat(32),name:'Interrupted',host:'example.org',port:22,username:'git',repository:'x.git',state:'creating',enabled:false,revision:0,public_key:null,fingerprint:null,host_fingerprint:null};
  const incomplete=fixture({items:[item]});await incomplete.ui.refresh();
  check(!incomplete.$('ssh-incomplete').hidden&&incomplete.$('ssh-enable').disabled,'incomplete key retained but unusable');
  check(incomplete.calls.every(x=>x.body===undefined),'incomplete key never automatically repaired');
  const account=fixture({scope:'account',access:'write'});await account.ui.refresh();
  check(account.$('ssh-repository-field').hidden&&!account.$('ssh-repository').required,'account key does not require a fixed repository');
  await account.ui.create(false);
  const accountCreate=account.calls.find(x=>x.url==='api/git/connections'&&x.body);
  check(accountCreate.body.scope==='account'&&accountCreate.body.access==='write'&&accountCreate.body.repository==='','account creation sends explicit scope/access without stale repository');
  check(account.confirmations.length===1&&account.confirmations[0].includes('account SSH key'),'write-capable account creation requires explicit confirmation');
  check(account.$('ssh-register-help').textContent.includes('SSH and GPG keys')&&!account.$('ssh-test-repository-field').hidden,'account key registration and per-test repository exposed');
  account.data.items[0].host_fingerprint=account.fp;await account.ui.refresh();
  const noRepo=account.calls.length;await account.ui.test();
  check(account.calls.length===noRepo&&account.$('ssh-test-repository').focused,'account read test requires an explicit repository');
  account.$('ssh-test-repository').value='owner/custom-apps.git';await account.ui.test();
  check(account.calls.find(x=>x.url.endsWith('/test')).body.repository==='owner/custom-apps.git','test uses explicit repository');
  check(account.$('ssh-message').textContent.includes('Read access only'),'read test never claims push was verified');
  account.$('ssh-access').value='read';account.ui.renderButtons();check(!account.$('ssh-save-access').disabled,'changed access enables explicit save');
  await account.ui.saveAccess();
  check(account.data.items[0].access==='read'&&account.confirmations.length===2,'access restriction requires explicit confirmed save');
  check(account.calls.find(x=>x.url.endsWith('/access')).body.revision===1,'access change uses inspected revision');
  const legacy=fixture({items:[{...item,state:'ready',enabled:true,public_key:'PUBLIC_ONLY',host_fingerprint:'PIN'}]});await legacy.ui.refresh();
  check(legacy.$('ssh-access').value==='read'&&legacy.$('ssh-test-repository-field').hidden,'legacy connection stays repository-scoped read-only');
  legacy.$('ssh-access').value='write';await legacy.ui.saveAccess();
  check(legacy.data.items[0].scope==='repository'&&legacy.data.items[0].access==='write','explicit legacy write enablement does not widen repository scope');
  check(legacy.confirmations[0].includes('publication task'),'connection permission is not task authorization');
  const legacyPending={request_id:'b'.repeat(32),name:'Old request',host:'github.com',port:22,username:'git',repository:'owner/old.git'};
  const pendingOld=fixture({pending:legacyPending,scope:'account',access:'write'});
  check(pendingOld.$('ssh-scope').value==='repository'&&pendingOld.$('ssh-create-access').value==='read','old pending creation is not silently upgraded');
  account.$('ssh-scope').value='repository';account.ui.scopeState();check(!account.$('ssh-repository-field').hidden&&account.$('ssh-repository').required,'scope change restores required repository field');
  account.$('ssh-scope').value='account';account.$('new-connection').events.click();
  check(account.$('ssh-repository-field').hidden,'opening reset form synchronizes scope visibility');
  check(account.calls.every(x=>!x.url.includes('clone')&&!x.url.includes('push')),'account settings do not clone or push');
  check(!source.includes('.innerHTML'),'component has no HTML injection sink');
  check(ids.has('connections-dialog')&&html.includes('src="./connections.js"')&&html.includes('href="./connections.css"'),'actual page includes dialog and assets');
  console.log(JSON.stringify({status:'passed',checks,scope:'Actual connections component with mock DOM/API; not browser layout, Ingress, provider authentication or container persistence'},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
