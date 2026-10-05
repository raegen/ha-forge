/* No external libraries, raw HTML rendering, CDN scripts or stored credentials. */
"use strict";
const $=id=>document.getElementById(id);
const state={cid:null,conversation:null,task:null,events:[],cursor:0,moreEvents:false,convs:[],archived:false,
  status:null,csrf:"",generation:0,poll:null,polling:false,failures:0,sending:false,online:false,theme:"system",
  nav:false,follow:true,messageSeq:0,seenUpdates:0,pendingCheck:null,pendingReview:null};
const active=t=>t&&["queued","running","cancelling"].includes(t.status);
const recoverable=t=>t&&["interrupted","failed","cancelled"].includes(t.status);
const updateQueued=t=>t&&t.status==="queued"&&Boolean(t.interrupts_task_id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=String(text);if(cls)n.className=cls;return n;};
const storage={
  get(k,f=null){try{return JSON.parse(localStorage.getItem("forge.v2."+k))??f;}catch{return f;}},
  set(k,v){try{localStorage.setItem("forge.v2."+k,JSON.stringify(v));}catch{}},
  remove(k){try{localStorage.removeItem("forge.v2."+k);}catch{}}
};
function key(){return crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join("");}
function toast(text){$("toast").textContent=text;$("toast").hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$("toast").hidden=true,5000);}
function notice(text){$("banner-text").textContent=text;$("banner").hidden=!text;}
function connected(ok){state.online=ok;$("connection").classList.toggle("offline",!ok);$("connection").lastElementChild.textContent=ok?"Connected to appliance":"Connection interrupted";updateComposer();}
async function api(path,body){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  try{
    const options={signal:controller.signal,credentials:"same-origin",cache:"no-store",headers:{"Accept":"application/json"}};
    if(body!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.headers["X-Forge-CSRF"]=state.csrf;options.body=JSON.stringify(body);}
    const response=await fetch(new URL("./"+path,document.baseURI),options);
    let envelope;try{envelope=await response.json();}catch{throw new Error("Unexpected server response. The app may be restarting.");}
    if(!response.ok||!envelope.success){const error=new Error(envelope.error||"Request failed");error.status=response.status;throw error;}
    return envelope.result;
  }catch(error){
    if(error.name==="AbortError")throw new Error("The request timed out. Your draft and request ID are retained.");
    throw error;
  }finally{clearTimeout(timer);}
}
async function refreshStatus(){
  const s=await api("api/status");state.status=s;state.csrf=s.csrf_token;
  $("version").textContent="Forge "+s.version;
  models.status(s);
  $("capabilities").replaceChildren(...[
    s.storage==="sqlite-wal"?"Durable checkpoints":"Checkpoint storage",
    s.web_search?"Web search":"Web off",s.code_interpreter?"Code interpreter":"Code interpreter off",
    s.shell?"Shell enabled":"Shell disabled","Changes: manual · 60 min"
  ].map(x=>el("span",x)));
  connected(true);
  if(!s.api_key_configured)notice("Set an OpenAI API key in the app options to start work. Existing chats remain available.");
  else if(!s.worker_alive)notice("The task worker is unavailable. Check Forge logs before starting new work.");
  else notice("");
  return s;
}
function setTheme(value){
  state.theme=value;storage.set("theme",value);
  const resolved=value==="system"?(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):value;
  document.documentElement.dataset.theme=resolved;$("theme").textContent="Theme: "+value;
}
function nav(open){
  state.nav=open;document.body.classList.toggle("nav-open",open);$("scrim").hidden=!open;
  $("open-nav").setAttribute("aria-expanded",String(open));
  const mobile=matchMedia("(max-width: 800px)").matches;
  $("sidebar").inert=mobile&&!open;document.querySelector("main").inert=mobile&&open;
  if(open&&mobile)$("close-nav").focus();
}
matchMedia("(max-width: 800px)").addEventListener("change",()=>nav(false));
function activity(open){$("activity").hidden=!open;$("activity-toggle").setAttribute("aria-expanded",String(open));storage.set("activity",open);}
function formatDate(value,withTime=false){
  const date=new Date(value);if(Number.isNaN(date.getTime()))return "";
  return date.toLocaleString(undefined,withTime?{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}:{month:"short",day:"numeric"});
}
function safeURL(value){
  try{const u=new URL(value);if(["http:","https:"].includes(u.protocol)&&!u.username&&!u.password)return u.href;}catch{}
  return null;
}
function inline(parent,text,depth=0){
  if(depth>5){parent.append(document.createTextNode(text));return;}
  // A deliberately conservative Markdown subset. User text is always a text node.
  const rx=/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[[^\]\n]{1,300}\]\([^\s)]+(?:\s+"[^"]*")?\))/g;
  let pos=0,match;
  while((match=rx.exec(text))){
    parent.append(document.createTextNode(text.slice(pos,match.index)));const token=match[0];
    if(token.startsWith("`"))parent.append(el("code",token.slice(1,-1)));
    else if(token.startsWith("**")||token.startsWith("__")){const n=el("strong");inline(n,token.slice(2,-2),depth+1);parent.append(n);}
    else if(token.startsWith("*")){const n=el("em");inline(n,token.slice(1,-1),depth+1);parent.append(n);}
    else{
      const parts=token.match(/^\[([^\]]+)\]\((\S+?)(?:\s+"[^"]*")?\)$/),href=parts&&safeURL(parts[2]);
      if(href){const a=el("a",parts[1]);a.href=href;a.target="_blank";a.rel="noopener noreferrer";parent.append(a);}
      else parent.append(document.createTextNode(token));
    }
    pos=rx.lastIndex;
  }
  parent.append(document.createTextNode(text.slice(pos)));
}
async function copy(text){
  try{await navigator.clipboard.writeText(text);toast("Copied");}
  catch{toast("Clipboard access is unavailable. Select the text to copy it.");}
}
function markdown(text){
  const root=el("div");
  const lines=String(text).replace(/\r\n/g,"\n").split("\n");
  let i=0;
  const special=l=>/^(?:#{1,6}\s|```|~~~|>\s?|[-*+]\s|\d+\.\s|---+\s*$)/.test(l);
  while(i<lines.length){
    const line=lines[i];if(!line.trim()){i++;continue;}
    const fence=line.match(/^\s*(```+|~~~+)([\w+-]*)\s*$/);
    if(fence){
      const code=[];i++;while(i<lines.length&&!lines[i].trim().startsWith(fence[1]))code.push(lines[i++]);if(i<lines.length)i++;
      const wrap=el("div",null,"code-block"),head=el("div",null,"code-header"),button=el("button","Copy","text-button");
      button.type="button";button.addEventListener("click",()=>copy(code.join("\n")));
      head.append(el("span",fence[2]||"code"),button);const pre=el("pre");pre.append(el("code",code.join("\n")));wrap.append(head,pre);root.append(wrap);continue;
    }
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading){const h=el("h"+Math.min(4,heading[1].length+1));inline(h,heading[2]);root.append(h);i++;continue;}
    if(/^---+\s*$/.test(line)){root.append(el("hr"));i++;continue;}
    if(/^\s*>/.test(line)){const b=el("blockquote");inline(b,line.replace(/^\s*>\s?/,""));root.append(b);i++;continue;}
    if(i+1<lines.length&&line.includes("|")&&/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(lines[i+1])){
      const cells=s=>s.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(x=>x.trim());
      const wrap=el("div",null,"table-wrap"),table=el("table"),thead=el("thead"),tr=el("tr"),tbody=el("tbody");
      for(const c of cells(line)){const th=el("th");inline(th,c);tr.append(th);}thead.append(tr);i+=2;
      while(i<lines.length&&lines[i].includes("|")&&lines[i].trim()){const row=el("tr");for(const c of cells(lines[i++])){const td=el("td");inline(td,c);row.append(td);}tbody.append(row);}
      table.append(thead,tbody);wrap.append(table);root.append(wrap);continue;
    }
    const list=line.match(/^\s*([-*+]|\d+\.)\s+(.+)$/);
    if(list){
      const ordered=/\d/.test(list[1]),ul=el(ordered?"ol":"ul");
      while(i<lines.length){const m=lines[i].match(/^\s*([-*+]|\d+\.)\s+(.+)$/);if(!m||/\d/.test(m[1])!==ordered)break;const li=el("li");inline(li,m[2]);ul.append(li);i++;}
      root.append(ul);continue;
    }
    const parts=[line];i++;
    while(i<lines.length&&lines[i].trim()&&!special(lines[i])){if(i+1<lines.length&&lines[i+1].match(/^\s*\|?[-: ]+\|/))break;parts.push(lines[i++]);}
    const p=el("p");parts.forEach((s,j)=>{if(j)p.append(el("br"));inline(p,s);});root.append(p);
  }
  return root;
}
function messageNode(m){
  const article=el("article",null,"message "+(m.role==="user"?"user":"assistant"));article.dataset.seq=m.seq||"";
  const head=el("div",null,"message-header");
  head.append(el("strong",m.role==="user"?"YOU":"FORGE"),el("time",formatDate(m.at,true)));
  if(m.partial)head.append(el("span","Progress update","partial"));
  if(m.waiting_input)head.append(el("span","Question — reply to continue","partial"));
  const button=el("button","Copy","copy-message");button.type="button";button.setAttribute("aria-label","Copy message");button.addEventListener("click",()=>copy(m.content));head.append(button);
  const content=el("div",null,"message-content");
  if(m.role==="user")content.textContent=m.content;else content.append(markdown(m.content));
  const sources=el("div",null,"sources"),seen=new Set();
  for(const [index,a] of (m.annotations||[]).entries()){
    if(a.type==="container_file_citation"&&m.seq){
      const b=el("button","Download "+(a.filename||"artifact"),"subtle");
      b.type="button";b.addEventListener("click",()=>downloadArtifact(m.seq,index,a.filename||"artifact",b));
      sources.append(b);seen.add("artifact-"+index);continue;
    }
    if(a.type!=="url_citation")continue;const href=safeURL(a.url);
    if(href&&!seen.has(href)){const link=el("a",a.title||new URL(href).hostname);link.href=href;link.target="_blank";link.rel="noopener noreferrer";sources.append(link);seen.add(href);}
  }
  article.append(head,content);if(m.attachments?.length)article.append(incoming.cards(m.attachments,state.cid));if(seen.size)article.append(sources);
  return article;
}
async function downloadArtifact(seq,index,filename,button){
  button.disabled=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),35000);
  try{
    const r=await fetch(new URL("./api/messages/"+seq+"/artifacts/"+index,document.baseURI),{credentials:"same-origin",signal:controller.signal});
    if(!r.ok){const e=await r.json();throw new Error(e.error||"Artifact unavailable");}
    const url=URL.createObjectURL(await r.blob()),a=el("a");a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){showError(e);}finally{clearTimeout(timer);button.disabled=false;}
}
function nearBottom(){const t=$("timeline");return t.scrollHeight-t.scrollTop-t.clientHeight<100;}
function scrollBottom(){const t=$("timeline");t.scrollTop=t.scrollHeight;$("jump").hidden=true;}
function renderMessages(messages,prepend=false){
  const follow=state.follow||nearBottom(),timeline=$("timeline"),oldHeight=timeline.scrollHeight;
  if(prepend)$("messages").prepend(...messages.map(messageNode));
  else {
    const existing=new Set([...$("messages").children].map(n=>n.dataset.seq));
    const unseen=messages.filter(m=>!existing.has(String(m.seq)));
    if(!existing.size)$("messages").replaceChildren(...messages.map(messageNode));
    else $("messages").append(...unseen.map(messageNode));
  }
  $("welcome").hidden=$("messages").children.length>0;
  state.messageSeq=messages.length?messages[messages.length-1].seq||0:0;
  if(prepend)timeline.scrollTop+=timeline.scrollHeight-oldHeight;
  else if(follow)scrollBottom();else $("jump").hidden=false;
}
function renderChats(){
  const query=$("search").value.trim().toLowerCase(),items=state.convs.filter(c=>c.title.toLowerCase().includes(query));
  const nodes=items.map(c=>{
    const b=el("button",null,c.id===state.cid?"active":"");b.type="button";b.title=c.title;
    b.setAttribute("aria-current",c.id===state.cid?"page":"false");b.append(el("strong",c.title),el("small",formatDate(c.updated_at)));
    b.addEventListener("click",()=>select(c.id).catch(showError));return b;
  });
  $("chat-list").replaceChildren(...(nodes.length?nodes:[el("p",query?"No matching conversations.":state.archived?"No archived conversations.":"Your conversations will appear here.","muted")]));
  $("chat-label").textContent=state.archived?"ARCHIVE":"CONVERSATIONS";$("archive-filter").setAttribute("aria-pressed",String(state.archived));
}
async function loadChats(){
  const archived=state.archived,convs=await api("api/conversations?archived="+(archived?"1":"0"));
  if(archived!==state.archived)return;
  state.convs=convs;renderChats();
}
function saveDraft(){if(state.cid)storage.set("draft."+state.cid,$("composer").value);}
function pendingRequest(cid=state.cid){return cid&&storage.get("pending."+cid);}
function pendingChecked(pending,status){
  const c=state.pendingCheck;
  return Boolean(pending&&c&&c.cid===state.cid&&c.generation===state.generation&&c.request_id===pending.request_id&&c.status===status);
}
function settlePending(cid,pending,accepted,generation){
  const current=pendingRequest(cid);
  // A late response must not remove a replacement request created in another tab.
  if(current&&current.request_id!==pending.request_id)return false;
  if(current)storage.remove("pending."+cid);
  if(accepted){
    if(storage.get("draft."+cid,"").trim()===pending.text)storage.remove("draft."+cid);
    if(cid===state.cid&&generation===state.generation&&$("composer").value.trim()===pending.text)$("composer").value="";
  }
  return true;
}
function renderPending(){
  const p=pendingRequest();$("pending-submission").hidden=!p;if(!p)return;
  const checking=pendingChecked(p,"checking"),missing=pendingChecked(p,"missing");
  $("pending-state").textContent=checking?"Checking the saved request ID; nothing is being sent.":missing?
    "No saved task was found for this request ID yet. Review the original below if you want to retry it. Your edited draft is kept.":
    pendingChecked(p,"error")?"The status check failed. Keep this draft and check again when connected; the original request ID is retained.":
    "Check whether the previous submission was saved before sending this draft. Status checks never resend a message.";
  $("check-pending").disabled=state.sending||checking;
  $("retry-pending").disabled=state.sending||!missing||!state.online||!permissions.ready()||!state.conversation||Boolean(state.conversation.archived)||updateQueued(state.task)||!state.status?.api_key_configured||!state.status?.worker_alive;
}
function updateComposer(){
  const pending=pendingRequest();
  $("send").disabled=pending?(state.sending||pendingChecked(pending,"checking")):state.sending||updateQueued(state.task)||!state.cid||Boolean(state.conversation?.archived)||!state.online||!state.status?.api_key_configured||!state.status?.worker_alive||!permissions.ready()||!models.ready()||incoming.loading||incoming.busy||!incoming.ready||(!$("composer").value.trim()&&!incoming.items.length);
  $("send").textContent=pending?"Check submission":active(state.task)?"Stop & send":state.task?.status==="waiting_input"?"Reply":"Send";
  $("composer").placeholder=state.task?.status==="waiting_input"?"Your answer…":active(state.task)?"Answer or change direction while Forge works…":"What needs doing?";
  $("composer-hint").textContent=pending?"Your draft is saved. Resolve the previous submission first; nothing is retried automatically.":state.conversation?.archived?"Restore this chat to continue.":updateQueued(state.task)?"Update saved · waiting for the previous task to stop":active(state.task)?"Stop & send saves your update and stops future actions. An in-flight action may finish.":state.task?.status==="waiting_input"?"Paused for your answer · nothing continues until you send":"Enter to send · Shift+Enter for a new line";
  permissions.render();models.render();
  $("composer").readOnly=state.sending;incoming.render();renderPending();
}
function renderTask(t){
  state.task=t;$("task-strip").hidden=!t;updateComposer();if(!t)return;
  $("task-state").textContent=t.status==="waiting_input"?"Waiting for you":t.status;$("task-state").className="badge "+t.status;
  $("task-phase").textContent=t.phase||t.status;$("task-error").textContent=t.error||"";$("task-error").hidden=!t.error;
  $("task-model").textContent=t.model_settings?"Task: "+models.label(t.model_settings):"Task: legacy configured settings";
  $("cancel").hidden=!active(t);$("cancel").disabled=t.cancel_requested||t.status==="cancelling";
  $("cancel").textContent=t.cancel_requested?"Stopping…":"Stop task";
  $("resume").hidden=!recoverable(t);
  updateElapsed();
}
function updateElapsed(){
  if(!state.task)return;
  const end=active(state.task)?Date.now():new Date(state.task.updated_at).getTime();
  const sec=Math.max(0,Math.floor((end-new Date(state.task.created_at).getTime())/1000));
  $("elapsed").textContent=(Math.floor(sec/60))+"m "+(sec%60)+"s";
}
function appendEvents(events,reset=false){
  if(reset){state.events=[];$("event-list").replaceChildren();}
  if(!events.length&&reset)$("event-list").append(el("p","No events recorded yet.","muted"));
  if(events.length&&!state.events.length)$("event-list").replaceChildren();
  const follow=$("event-list").scrollHeight-$("event-list").scrollTop-$("event-list").clientHeight<80;
  for(const e of events){
    if(state.events.some(x=>x.seq===e.seq))continue;
    state.events.push(e);const node=el("div",null,"event "+String(e.kind).replace(/[^a-z_]/g,""));
    const top=el("div",null,"event-top");top.append(el("span",String(e.kind).replaceAll("_"," ")),el("time",formatDate(e.at,true)));
    node.append(top,el("div",e.message,"event-text"));
    if(e.data!==undefined){const details=el("details");details.append(el("summary","View details"),el("pre",typeof e.data==="string"?e.data:JSON.stringify(e.data,null,2)));node.append(details);}
    $("event-list").append(node);
  }
  // Bound DOM, not durable history. Older events remain on the server.
  while(state.events.length>400){state.events.shift();$("event-list").firstElementChild?.remove();}
  if(follow)$("event-list").scrollTop=$("event-list").scrollHeight;
}
async function select(cid){
  saveDraft();clearTimeout(state.poll);state.generation++;const generation=state.generation;
  state.cid=cid;state.pendingCheck=null;incoming.reset(cid);storage.set("selected",cid);state.conversation=null;state.task=null;state.cursor=0;state.seenUpdates=0;state.messageSeq=0;state.follow=true;
  permissions.reset(cid);models.reset(cid);$("composer").value=storage.get("draft."+cid,"");$("messages").replaceChildren();$("welcome").hidden=true;$("older").hidden=true;
  appendEvents([],true);renderTask(null);renderChats();nav(false);updateComposer();
  try{
    const c=await api("api/conversations/"+cid);if(generation!==state.generation)return;
    state.conversation=c;permissions.accept(c);models.accept(c);$("chat-title").textContent=c.title;$("archive").textContent=c.archived?"Restore chat":"Archive chat";
    renderMessages(c.messages);$("older").hidden=!c.has_more_messages;
    renderTask(c.tasks[0]||null);await incoming.load(cid);if(generation!==state.generation)return;
    const resolved=await reconcilePending(false);if(generation!==state.generation)return;
    if(resolved){await select(cid);return;}
    if(state.task)await pollTask(generation,true);else schedulePoll(generation,4000);
  }catch(e){if(generation===state.generation){showError(e);schedulePoll(generation,3000);}}
}
function schedulePoll(generation,delay=1800){
  clearTimeout(state.poll);if(generation===state.generation)state.poll=setTimeout(()=>pollTask(generation).catch(showError),delay);
}
async function pollTask(generation,reset=false){
  if(generation!==state.generation)return;
  if(state.polling===generation){schedulePoll(generation,500);return;}
  state.polling=generation;
  // One completion-driven polling chain per selected conversation. Stale responses are ignored.
  try{
    if(!state.conversation){
      const c=await api("api/conversations/"+state.cid);if(generation!==state.generation)return;
      state.conversation=c;permissions.accept(c);models.accept(c);$("chat-title").textContent=c.title;renderMessages(c.messages);$("older").hidden=!c.has_more_messages;renderTask(c.tasks[0]||null);
    }
    if(!state.task){
      await refreshStatus();const c=await api("api/conversations/"+state.cid);
      if(generation!==state.generation)return;
      permissions.accept(c);models.accept(c);
      if(c.tasks[0]){await select(state.cid);return;}
      schedulePoll(generation,8000);return;
    }
    const tid=state.task.id,task=await api("api/tasks/"+tid+"?after="+(reset?0:state.cursor));
    if(generation!==state.generation||tid!==state.task?.id)return;
    const previousStatus=state.task.status;
    state.cursor=task.cursor;state.moreEvents=task.has_more;appendEvents(task.events,reset);
    $("more-events").hidden=!task.has_more;renderTask(task);
    if(task.events.some(e=>e.kind==="assistant_update"||e.kind==="done")||previousStatus!==task.status){
      const c=await api("api/conversations/"+state.cid);if(generation!==state.generation)return;
      state.conversation=c;permissions.accept(c);models.accept(c);renderMessages(c.messages);$("older").hidden=!c.has_more_messages;
      await loadChats();
    }
    state.failures=0;connected(true);
    if($("banner-text").textContent.includes("Connection")||$("banner-text").textContent.includes("timed out"))notice("");
    // Re-bootstrap periodically and at task completion, so a restart rotates CSRF cleanly.
    if(!active(task)||reset){
      await refreshStatus();
      if(generation!==state.generation)return;
      const c=await api("api/conversations/"+state.cid);
      if(generation!==state.generation)return;
      permissions.accept(c);models.accept(c);
      if(c.tasks[0]?.id&&c.tasks[0].id!==tid){await select(state.cid);return;}
    }
    schedulePoll(generation,task.has_more?100:active(task)?1800:6000);
  }catch(e){
    if(generation!==state.generation)return;
    state.failures++;connected(false);notice("Connection interrupted. Work may still be running; your draft is saved. "+e.message);
    if(state.failures%3===0){try{await refreshStatus();}catch{}}
    schedulePoll(generation,Math.min(20000,1500*2**Math.min(state.failures,4)));
  }finally{if(state.polling===generation)state.polling=false;}
}
function showError(error){notice(error.message||String(error));if(!error.status)connected(false);toast(error.message||"Something went wrong");}
async function newChat(){
  if(state.sending)return;state.sending=true;$("new-chat").disabled=true;
  const request=storage.get("new-chat-pending")||key();storage.set("new-chat-pending",request);
  try{const c=await api("api/conversations",{request_id:request});storage.remove("new-chat-pending");state.archived=false;await loadChats();await select(c.id);$("composer").focus();}
  catch(e){showError(e);}
  finally{state.sending=false;$("new-chat").disabled=false;updateComposer();}
}
async function send(event){
  event?.preventDefault();
  if(state.sending||!state.cid)return;
  // An edited or attachment-only draft can always request a read-only status check.
  if(pendingRequest()){await checkPending();return;}
  if(updateQueued(state.task)||!state.online||state.conversation?.archived||!permissions.ready()||!models.ready()||incoming.loading||incoming.busy||!incoming.ready)return;
  const text=$("composer").value.trim(),attachment_ids=incoming.ids();if(!text&&!attachment_ids.length)return;
  const cid=state.cid,generation=state.generation,allow=$("allow-changes").checked;
  const pending={text,attachment_ids,model_settings:models.snapshot(),allow_changes:allow,allow_changes_revision:permissions.revision,request_id:key(),interrupt_task_id:active(state.task)?state.task.id:null};
  storage.set("pending."+cid,pending);await submitMessage(cid,generation,pending);
}
async function submitMessage(cid,generation,pending){
  state.pendingCheck=null;state.sending=true;updateComposer();saveDraft();
  try{
    await api("api/conversations/"+cid+"/messages",pending);
    settlePending(cid,pending,true,generation);
    if(generation!==state.generation||cid!==state.cid)return;
    state.follow=true;await select(cid);
    // select() owns the newest task; a historical dedup response must not replace it.
    schedulePoll(state.generation,100);
  }catch(e){
    if(e.status&&e.status!==403&&e.status<500)settlePending(cid,pending,false,generation);
    if(generation!==state.generation||cid!==state.cid)return;
    showError(e);
    if(e.status===403){try{await refreshStatus();toast("Session refreshed. Check the previous submission before retrying it.");}catch{}}
    if(e.status===409){try{permissions.accept(await api("api/conversations/"+cid));}catch{}}
  }finally{state.sending=false;updateComposer();}
}
async function reconcilePending(refresh=true){
  const cid=state.cid,generation=state.generation,pending=pendingRequest(cid);
  if(!cid||!pending||state.sending||pendingChecked(pending,"checking"))return false;
  const lookup={cid,generation,request_id:pending.request_id,status:"checking"};state.pendingCheck=lookup;updateComposer();
  try{
    // Read-only: not found is not proof that a delayed POST can never commit.
    const t=await api("api/conversations/"+cid+"/requests/"+encodeURIComponent(pending.request_id));
    const current=pendingRequest(cid);
    if(current&&current.request_id!==pending.request_id)return false;
    if(t){
      const settled=settlePending(cid,pending,true,generation);
      if(cid===state.cid&&generation===state.generation){
        state.pendingCheck=null;
        if(refresh)await select(cid);
        toast("Previous submission found. Any edited draft has been kept; review it before sending.");
      }
      return settled;
    }
    if(current&&state.pendingCheck===lookup&&cid===state.cid&&generation===state.generation)lookup.status="missing";
    return false;
  }catch(e){if(state.pendingCheck===lookup)lookup.status="error";throw e;}
  finally{if(state.pendingCheck===lookup&&lookup.status==="checking")state.pendingCheck=null;updateComposer();}
}
async function checkPending(){
  const cid=state.cid,generation=state.generation;
  if(!cid||state.sending||pendingChecked(pendingRequest(),"checking"))return;
  saveDraft();
  try{await refreshStatus();if(cid!==state.cid||generation!==state.generation)return;await reconcilePending();}
  catch(e){if(cid===state.cid&&generation===state.generation)showError(e);}
}
function reviewPending(){
  const pending=pendingRequest();
  if(!pending||$("retry-pending").disabled||!pendingChecked(pending,"missing"))return;
  state.pendingReview={cid:state.cid,generation:state.generation,pending};
  $("pending-review-text").textContent=pending.text||"[Attachment-only message]";
  $("pending-review-info").textContent=(pending.attachment_ids||[]).length+" saved attachment(s) · "+
    (pending.allow_changes?"Allow changes: ON (up to 60 minutes if this starts new work).":"Read-only.")+
    (pending.model_settings?" Model: "+models.label(pending.model_settings)+".":" Legacy submission: original app settings apply.")+
    (pending.interrupt_task_id?" The original Stop & send target is retained; a newer task cannot be stopped by this retry.":"");
  $("pending-dialog").returnValue="";$("pending-dialog").showModal();
}
async function retryReviewedPending(){
  const review=state.pendingReview;state.pendingReview=null;
  if($("pending-dialog").returnValue!=="retry"||!review)return;
  const pending=pendingRequest(review.cid);
  if(review.cid!==state.cid||review.generation!==state.generation||JSON.stringify(pending)!==JSON.stringify(review.pending)||!pendingChecked(pending,"missing")){
    toast("The conversation or saved submission changed. Check its status again; nothing was sent.");return;
  }
  if(state.sending||!state.online||!permissions.ready()||state.conversation?.archived||updateQueued(state.task)||incoming.busy)return;
  // Only this explicit human confirmation retries the frozen original payload/ID.
  await submitMessage(review.cid,review.generation,review.pending);
}
$("allow-changes").addEventListener("change",()=>permissions.save());
$("check-pending").addEventListener("click",checkPending);
$("retry-pending").addEventListener("click",reviewPending);
$("pending-dialog").addEventListener("close",retryReviewedPending);
window.addEventListener("storage",event=>{
  if(event.key==="forge.v2.pending."+state.cid){state.pendingCheck=null;updateComposer();}
});
$("compose-form").addEventListener("submit",send);
$("composer").addEventListener("input",()=>{saveDraft();updateComposer();});
$("composer").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!$("send").disabled)send();}});
$("new-chat").addEventListener("click",newChat);$("search").addEventListener("input",renderChats);
$("archive-filter").addEventListener("click",()=>{state.archived=!state.archived;loadChats().catch(showError);});
$("open-nav").addEventListener("click",()=>nav(true));$("close-nav").addEventListener("click",()=>nav(false));$("scrim").addEventListener("click",()=>nav(false));
document.addEventListener("keydown",e=>{if(e.key==="Escape"){nav(false);activity(false);$("chat-menu").open=false;}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();nav(true);$("search").focus();}});
$("activity-toggle").addEventListener("click",()=>activity($("activity").hidden));$("close-activity").addEventListener("click",()=>activity(false));$("details-button").addEventListener("click",()=>activity(true));
$("theme").addEventListener("click",()=>setTheme(["system","dark","light"][(["system","dark","light"].indexOf(state.theme)+1)%3]));
matchMedia("(prefers-color-scheme: light)").addEventListener("change",()=>{if(state.theme==="system")setTheme("system");});
$("timeline").addEventListener("scroll",()=>{state.follow=nearBottom();if(state.follow)$("jump").hidden=true;});$("jump").addEventListener("click",()=>{state.follow=true;scrollBottom();});
$("more-events").addEventListener("click",()=>{clearTimeout(state.poll);pollTask(state.generation).catch(showError);});
$("older").addEventListener("click",async()=>{
  const seq=Number($("messages").firstElementChild?.dataset.seq),generation=state.generation;if(!seq)return;
  $("older").disabled=true;
  try{const c=await api("api/conversations/"+state.cid+"?before="+seq);if(generation!==state.generation)return;renderMessages(c.messages,true);$("older").hidden=!c.has_more_messages;}
  catch(e){showError(e);}finally{$("older").disabled=false;}
});
$("cancel").addEventListener("click",async()=>{
  if(!state.task)return;const tid=state.task.id,generation=state.generation;$("cancel").disabled=true;
  try{const t=await api("api/tasks/"+tid+"/cancel",{});if(generation!==state.generation)return;renderTask(t);schedulePoll(generation,100);}
  catch(e){showError(e);$("cancel").disabled=false;}
});
$("resume").addEventListener("click",()=>{$("resume-changes").checked=false;$("resume-dialog").returnValue="";$("resume-dialog").showModal();});
$("resume-dialog").addEventListener("close",async()=>{
  if($("resume-dialog").returnValue!=="resume"||!state.task)return;
  const tid=state.task.id,generation=state.generation,allow=$("resume-changes").checked;
  const pending=storage.get("resume."+tid)||{request_id:key(),allow_changes:allow};storage.set("resume."+tid,pending);
  $("resume").disabled=true;
  try{const t=await api("api/tasks/"+tid+"/resume",pending);storage.remove("resume."+tid);if(generation!==state.generation)return;await select(state.cid);renderTask(t);schedulePoll(state.generation,100);}
  catch(e){showError(e);}finally{$("resume").disabled=false;}
});
$("rename").addEventListener("click",()=>{if(!state.cid)return;$("chat-menu").open=false;$("new-title").value=state.conversation?.title||"";$("rename-dialog").returnValue="";$("rename-dialog").showModal();$("new-title").select();});
$("rename-dialog").addEventListener("close",async()=>{
  if($("rename-dialog").returnValue!=="save")return;const cid=state.cid,title=$("new-title").value.trim();if(!title)return;
  try{const c=await api("api/conversations/"+cid,{title});if(cid===state.cid){$("chat-title").textContent=c.title;state.conversation.title=c.title;}await loadChats();}catch(e){showError(e);}
});
$("archive").addEventListener("click",async()=>{
  if(!state.conversation)return;const c=state.conversation;$("chat-menu").open=false;
  try{await api("api/conversations/"+c.id,{archived:!c.archived});await loadChats();await select(c.id);toast(c.archived?"Conversation restored":"Conversation archived. No history deleted.");}catch(e){showError(e);}
});
$("export").addEventListener("click",async()=>{
  if(!state.cid)return;$("chat-menu").open=false;
  try{
    const data=await api("api/conversations/"+state.cid+"/export"),blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=el("a");
    a.href=url;a.download="forge-chat-"+state.cid+".json";document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast("Chat exported. Treat the file as private.");
  }catch(e){showError(e);}
});
$("reconnect").addEventListener("click",async()=>{
  try{await refreshStatus();await loadChats();if(state.cid)await select(state.cid);else if(!state.convs.length)await newChat();}
  catch(e){showError(e);}
});
document.querySelectorAll("[data-prompt]").forEach(b=>b.addEventListener("click",()=>{$("composer").value=b.dataset.prompt;saveDraft();updateComposer();$("composer").focus();}));
window.addEventListener("online",()=>$("reconnect").click());window.addEventListener("offline",()=>{connected(false);notice("Connection interrupted. Your draft is saved; active work continues on the appliance.");});
window.addEventListener("beforeunload",saveDraft);
const permissions=new ConversationPermission({state,checkbox:$("allow-changes"),api,
  pending:cid=>cid&&storage.get("pending."+cid),update:updateComposer,error:showError,refreshStatus});
const incoming=new AttachmentUI({state,$,el,storage,api,key,toast,update:updateComposer});
const models=new ModelControls({state,$,el,storage,api,toast,pending:pendingRequest,update:updateComposer});
const connections=new GitConnections({$,el,api,key,storage,toast,closeNav:()=>nav(false)});
setInterval(updateElapsed,1000);
async function boot(){
  setTheme(storage.get("theme","system"));activity(storage.get("activity",false));nav(false);updateComposer();
  try{
    await refreshStatus();models.load();await loadChats();const selected=storage.get("selected");
    if(state.convs.some(c=>c.id===selected))await select(selected);
    else if(state.convs.length)await select(state.convs[0].id);
    else await newChat();
  }catch(e){showError(e);setTimeout(()=>{if(!state.online)boot();},5000);}
}
boot();
