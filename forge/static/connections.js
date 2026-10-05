/* Public-key-only connection settings. No automatic key creation, trust or POST retries. */
"use strict";
class GitConnections {
  constructor({$,el,api,key,storage,toast,closeNav}) {
    Object.assign(this,{$,el,api,key,storage,toast,closeNav});
    this.items=[];this.selected=null;this.busy=false;this.available=false;this.candidates=[];
    this.pending=storage.get("ssh.pending");
    $("open-connections").addEventListener("click",()=>this.open());
    $("close-connections").addEventListener("click",()=>$("connections-dialog").close());
    $("refresh-connections").addEventListener("click",()=>this.refresh());
    $("new-connection").addEventListener("click",()=>{
      $("ssh-create-section").hidden=false;this.fillPending();this.scopeState();$("ssh-name").focus();
    });
    $("cancel-connection").addEventListener("click",()=>{$("ssh-create-section").hidden=true;});
    $("ssh-create-form").addEventListener("submit",e=>{e.preventDefault();this.create(false);});
    $("ssh-check-pending").addEventListener("click",()=>this.refresh());
    $("ssh-retry-pending").addEventListener("click",()=>this.create(true));
    $("ssh-copy-key").addEventListener("click",()=>this.copyKey());
    $("ssh-scan").addEventListener("click",()=>this.scan());
    $("ssh-test").addEventListener("click",()=>this.test());
    $("ssh-enable").addEventListener("click",()=>this.enable());
    $("ssh-pin").addEventListener("click",()=>this.pin());
    $("ssh-scope").addEventListener("change",()=>this.scopeState());
    $("ssh-save-access").addEventListener("click",()=>this.saveAccess());
    $("ssh-access").addEventListener("change",()=>this.renderButtons());
    $("ssh-expected-fingerprint").addEventListener("input",()=>this.pinState());
    $("ssh-host-candidate").addEventListener("change",()=>this.pinState());
    this.pendingState();this.scopeState();
  }
  message(text,error=false){const n=this.$("ssh-message");n.textContent=text;n.classList.toggle("is-error",error);}
  current(){return this.items.find(x=>x.id===this.selected);}
  lock(value){
    this.busy=value;this.$("connections-dialog").setAttribute("aria-busy",String(value));
    for(const id of ["refresh-connections","new-connection","ssh-scan","ssh-test","ssh-enable","ssh-create","ssh-retry-pending","ssh-check-pending","ssh-save-access"])this.$(id).disabled=value;
    this.pendingState();this.renderButtons();
  }
  async open(){
    this.closeNav();this.$("connections-dialog").showModal();
    await this.refresh();
  }
  async refresh(){
    if(this.busy)return;
    this.lock(true);
    try{await this.read();this.message(this.available?"Connections stay on this appliance. No remote hosts were contacted.":"Update Forge to install its Git/SSH clients before creating a connection.",!this.available);}
    catch(e){this.message(e.message,true);}
    finally{this.lock(false);}
  }
  async read(){
    const result=await this.api("api/git/connections");
    this.items=result.connections;this.available=result.available;
    if(this.pending && typeof this.pending.request_id==="string"){
      const found=this.items.find(x=>x.id===this.pending.request_id.replaceAll("-",""));
      if(found){this.selected=found.id;this.pending=null;this.storage.remove("ssh.pending");this.$("ssh-create-section").hidden=true;this.$("ssh-create-form").reset();}
    }
    if(!this.current())this.selected=this.items[0]?.id||null;
    this.candidates=[];this.render();this.pendingState();this.scopeState();
  }
  pendingState(){
    this.$("ssh-pending").hidden=!this.pending;
    this.$("ssh-create-fields").disabled=this.busy||!!this.pending;
    this.$("ssh-create").disabled=this.busy||!!this.pending||!this.available;
    this.$("ssh-retry-pending").disabled=this.busy||!this.pending;
    if(this.pending)this.fillPending();
  }
  fillPending(){
    if(!this.pending)return;
    const map={name:"name",host:"host",port:"port",username:"username",repository:"repository"};
    for(const [field,id] of Object.entries(map))this.$("ssh-"+id).value=this.pending[field]||"";
    this.$("ssh-scope").value=this.pending.scope||"repository";
    this.$("ssh-create-access").value=this.pending.access||"read";
    this.scopeState();
  }
  scopeState(){
    const account=this.$("ssh-scope").value==="account";
    this.$("ssh-repository-field").hidden=account;
    this.$("ssh-repository").required=!account;
    this.$("ssh-scope-help").textContent=account?"One key for repositories your Git account can access. Register it in your account’s SSH key settings.":"A key restricted by Forge to one repository. Register it as that repository’s deploy key; enable provider write access if you intend to push.";
  }
  render(){
    const {$,el}=this;$("ssh-list").replaceChildren();
    if(!this.items.length)$("ssh-list").append(el("p","No SSH keys yet. Add your Git account to start developing and publishing.","muted"));
    for(const item of this.items){
      const b=el("button",null,"ssh-connection-item"+(item.id===this.selected?" selected":""));b.type="button";
      b.setAttribute("aria-pressed",String(item.id===this.selected));
      b.append(el("strong",item.name),el("span",item.host+" · "+(item.scope==="account"?"Account key":item.repository)+" · "+(item.access==="write"?"Read & write":"Read only")),el("small",item.state!=="ready"?"Incomplete — retained":!item.enabled?"Disabled":item.host_fingerprint?"Host pinned":"Needs host verification"));
      b.addEventListener("click",()=>{if(this.busy)return;this.selected=item.id;this.candidates=[];this.render();});$("ssh-list").append(b);
    }
    const item=this.current();$("ssh-detail").hidden=!item;
    if(!item){this.renderButtons();return;}
    $("ssh-detail-name").textContent=item.name;
    $("ssh-detail-remote").textContent=item.username+"@"+item.host+":"+item.port+" · "+(item.scope==="account"?"Account-wide repository access":item.repository);
    $("ssh-access").value=item.access||"read";
    $("ssh-test-repository-field").hidden=item.scope!=="account";
    $("ssh-register-help").textContent=item.scope==="account"?"For GitHub: your account Settings → SSH and GPG keys → New SSH key (Authentication key). Add this public key once; it can access repositories your account can access. Never paste a private key or account credentials into chat.":"For GitHub: repository Settings → Deploy keys → Add deploy key. "+(item.access==="write"?"Enable Allow write access for publication.":"Leave Allow write access off for read-only use.")+" No account credentials go into chat.";
    $("ssh-public-key").value=item.public_key||"Key creation did not finish. No replacement was generated automatically.";
    $("ssh-key-fingerprint").textContent=item.fingerprint||"No completed key";
    $("ssh-trust-state").textContent=item.host_fingerprint?"Pinned host · "+item.host_fingerprint:"No host key trusted yet. Scan, then verify independently.";
    $("ssh-incomplete").hidden=item.state==="ready";
    $("ssh-host-review").hidden=!this.candidates.length;
    $("ssh-expected-fingerprint").value="";
    const select=$("ssh-host-candidate");select.replaceChildren();
    for(const c of this.candidates){const option=el("option",c.fingerprint);option.value=c.fingerprint;select.append(option);}
    this.renderButtons();
  }
  renderButtons(){
    const item=this.current(),ready=this.available&&item?.enabled&&item.state==="ready"&&!this.busy;
    this.$("ssh-scan").disabled=!ready;
    this.$("ssh-test").disabled=!ready||!item?.host_fingerprint;
    this.$("ssh-enable").disabled=this.busy||!item||item.state!=="ready";
    this.$("ssh-enable").textContent=item?.enabled?"Disable connection":"Enable connection";
    this.$("ssh-copy-key").disabled=!item?.public_key;
    this.$("ssh-access").disabled=this.busy||!item||item.state!=="ready";
    this.$("ssh-save-access").disabled=this.busy||!item||item.state!=="ready"||this.$("ssh-access").value===(item.access||"read");
    this.pinState();
  }
  pinState(){
    const candidate=this.candidates.find(c=>c.fingerprint===this.$("ssh-host-candidate").value);
    this.$("ssh-pin").disabled=this.busy||!candidate||this.$("ssh-expected-fingerprint").value.trim()!==candidate.fingerprint;
  }
  async create(retry){
    if(this.busy||!this.available)return;
    if(retry){
      if(!this.pending||!confirm("Retry only the original connection creation with its saved request ID? An existing connection will be returned, never replaced."))return;
    }else{
      if(this.pending||!this.$("ssh-create-form").reportValidity())return;
      const scope=this.$("ssh-scope").value,access=this.$("ssh-create-access").value;
      if(access==="write"&&!confirm("Create a persistent "+(scope==="account"?"account SSH key that can access repositories allowed by your Git account":"repository SSH key")+" for development and publication? Forge may push only when you authorize a task and destination. No provider is contacted by this step."))return;
      this.pending={request_id:this.key(),name:this.$("ssh-name").value.trim(),host:this.$("ssh-host").value.trim(),port:Number(this.$("ssh-port").value),username:this.$("ssh-username").value.trim(),scope,access,repository:scope==="account"?"":this.$("ssh-repository").value.trim()};
      this.storage.set("ssh.pending",this.pending);
    }
    this.lock(true);this.message("Creating one persistent key. Nothing is being sent to a Git provider…");
    try{
      const result=await this.api("api/git/connections",this.pending);
      this.selected=result.id;this.pending=null;this.storage.remove("ssh.pending");
      this.$("ssh-create-section").hidden=true;this.$("ssh-create-form").reset();
      await this.read();this.message(result.state==="ready"?"Key ready. Register the public key using the account or repository instructions below, then verify the server.":"Creation was interrupted. No key was replaced; create a new connection explicitly and retain this record.",result.state!=="ready");
    }catch(e){
      // Validation errors before allocation are certain; ambiguous outcomes stay reconciliable.
      if(e.status===400||e.status===403){this.pending=null;this.storage.remove("ssh.pending");}
      try{await this.read();}catch{}
      this.message(e.message+" Check the saved connections before deciding whether to retry.",true);
    }finally{this.lock(false);}
  }
  async copyKey(){
    const item=this.current();if(!item?.public_key)return;
    try{await navigator.clipboard.writeText(item.public_key);this.toast("Public key copied. Private key stays on the appliance.");}
    catch{this.$("ssh-public-key").focus();this.$("ssh-public-key").select();this.message("Select and copy the public key above.");}
  }
  async action(suffix,body,success){
    const item=this.current();if(this.busy||!item)return null;
    this.lock(true);this.message("Working…");
    try{const result=await this.api("api/git/connections/"+item.id+"/"+suffix,body);await this.read();this.message(success);return result;}
    catch(e){try{await this.read();}catch{}this.message(e.message+" No automatic retry was made.",true);return null;}
    finally{this.lock(false);}
  }
  async scan(){
    const result=await this.action("scan",{},"Scan is untrusted. Compare the selected SHA256 fingerprint with your host administrator or official provider documentation.");
    if(result){const item=this.current();if(!item||item.revision!==result.revision){this.message("Connection changed during the scan. Scan again before verifying.",true);return;}this.candidates=result.candidates;this.render();this.$("ssh-expected-fingerprint").focus();}
  }
  async pin(){
    const item=this.current(),candidate=this.candidates.find(c=>c.fingerprint===this.$("ssh-host-candidate").value),expected=this.$("ssh-expected-fingerprint").value.trim();
    if(!item||!candidate||expected!==candidate.fingerprint||this.busy)return;
    if(item.host_fingerprint&&item.host_fingerprint!==expected&&!confirm("Replace the pinned host key? Proceed only after independently confirming the host’s key change."))return;
    await this.action("pin",{revision:item.revision,public_key:candidate.public_key,expected_fingerprint:expected},"Host key pinned. Test repository access after registering the public key with your Git provider.");
  }
  async test(){
    const item=this.current();if(!item)return;
    const repository=this.$("ssh-test-repository").value.trim();
    if(item.scope==="account"&&!repository){this.message("Enter a repository path such as owner/project.git to test this account key.",true);this.$("ssh-test-repository").focus();return;}
    const result=await this.action("test",item.scope==="account"?{repository}:{},"Repository read access verified. No files were cloned and no write/push permission was tested.");
    if(result)this.message("Authenticated successfully · "+result.refs.length+(result.truncated?"+":"")+" branches/tags listed. Read access only; nothing cloned or pushed.");
  }
  async saveAccess(){
    const item=this.current(),access=this.$("ssh-access").value;
    if(this.busy||!item||access===(item.access||"read"))return;
    if(!confirm(access==="write"?"Allow this key to push through Forge’s Git tools? "+(item.scope==="account"?"This is an account-wide key. ":"")+"Provider permissions and an explicit publication task are still required. Existing grants are not extended.":"Restrict future Git-tool use to read access? This does not revoke provider-side access or undo an in-flight push."))return;
    await this.action("access",{revision:item.revision,access},"Allowed Git operations saved. Task authorization and provider permissions are unchanged.");
  }
  async enable(){
    const item=this.current();if(!item)return;
    if(!confirm(item.enabled?"Disable future use of this connection? Its key is retained; in-flight work may finish. To revoke it remotely, remove its public key at your Git provider.":"Enable this saved connection for its displayed Git permissions, including explicitly authorized pushes if write access is enabled? This does not grant a task permission to make changes."))return;
    await this.action("enabled",{revision:item.revision,enabled:!item.enabled},item.enabled?"Connection disabled. Key retained; remote revocation is separate.":"Connection enabled. Task permissions are unchanged.");
  }
}
