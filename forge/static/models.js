/* Account inventory is fetched server-side; API credentials never enter the browser.
 * Draft choices apply only to the next explicit Send. Pending retries stay frozen. */
"use strict";
class ModelControls {
  constructor(ui) {
    this.ui=ui;this.cid=null;this.defaults=null;this.choice=null;this.catalog=null;
    this.loading=false;this.error="";this.accepted=false;
    const {$}=ui;
    $("model-controls").addEventListener("click",()=>this.open());
    $("model-label").addEventListener("click",()=>this.open());
    $("refresh-models").addEventListener("click",()=>this.load(true));
    $("model-select").addEventListener("change",()=>this.changeModel());
    $("effort-select").addEventListener("change",()=>this.changeEffort());
  }
  valid(s) {
    return s&&typeof s.model==="string"&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(s.model)&&
      (s.reasoning_effort===null||["none","minimal","low","medium","high","xhigh","max"].includes(s.reasoning_effort));
  }
  status(s) {
    this.defaults=this.valid(s.model_settings)?{...s.model_settings}:{model:s.model,reasoning_effort:s.reasoning_effort};
    this.render();
  }
  reset(cid) {
    this.cid=cid;this.accepted=false;
    const saved=this.ui.storage.get("model."+cid);
    this.choice=this.valid(saved)?{...saved}:null;
    this.render();
  }
  accept(c) {
    if(!c||c.id!==this.cid||c.id!==this.ui.state.cid||this.accepted)return;
    this.accepted=true;
    if(!this.choice&&this.valid(c.model_settings))this.choice={...c.model_settings};
    this.render();
  }
  selection() { return this.choice||this.defaults; }
  snapshot() { const s=this.selection();return s?{model:s.model,reasoning_effort:s.reasoning_effort}:null; }
  label(s) { return s?s.model+" · "+(s.reasoning_effort===null?"no effort setting":s.reasoning_effort+" effort"):"Connecting…"; }
  rows() {
    if(this.catalog)return this.catalog.models||[];
    return this.defaults?[{id:this.defaults.model,efforts:this.defaults.reasoning_effort===null?[]:[this.defaults.reasoning_effort],
      configured:true,available:false,suggested_effort:this.defaults.reasoning_effort}]:[];
  }
  ready() {
    const s=this.selection();if(!this.valid(s))return false;
    if(this.defaults&&s.model===this.defaults.model&&s.reasoning_effort===this.defaults.reasoning_effort)return true;
    const row=this.rows().find(x=>x.id===s.model);
    return Boolean(row?.available&&(row.efforts.length?row.efforts.includes(s.reasoning_effort):s.reasoning_effort===null));
  }
  locked() {
    const {state,pending}=this.ui;
    return !this.cid||!state.conversation||state.sending||Boolean(state.conversation.archived)||Boolean(pending(this.cid));
  }
  open() {
    this.render();this.ui.$("model-dialog").showModal();
    if(!this.catalog&&!this.loading)this.load();
  }
  async load(refresh=false) {
    if(this.loading)return;
    this.loading=true;this.error="";this.render();
    try {
      this.catalog=await this.ui.api("api/models"+(refresh?"?refresh=1":""));
      this.error=this.catalog.error||"";
    } catch(e) { this.error=e.message||"Model discovery is unavailable. The configured default is retained."; }
    finally {this.loading=false;this.render();this.ui.update();}
  }
  remember() {
    this.ui.storage.set("model."+this.cid,this.choice);this.render();this.ui.update();
  }
  changeModel() {
    if(this.locked()){this.render();return;}
    const row=this.rows().find(r=>r.id===this.ui.$("model-select").value);if(!row)return;
    const previous=this.selection()?.reasoning_effort;
    const effort=row.efforts.length?(row.efforts.includes(previous)?previous:row.suggested_effort):null;
    this.choice={model:row.id,reasoning_effort:effort};this.remember();
    if(previous!==effort)this.ui.toast(effort===null?"This model has no reasoning-effort setting.":"Effort set to "+effort+" for this model. Applies to your next Send.");
  }
  changeEffort() {
    if(this.locked()){this.render();return;}
    const current=this.selection(),row=this.rows().find(r=>r.id===current?.model),value=this.ui.$("effort-select").value;
    if(!row?.efforts.includes(value))return;
    this.choice={model:current.model,reasoning_effort:value};this.remember();
  }
  setOptions(id, options, value) {
    // Polling must not rebuild an open native select or steal keyboard focus.
    const select=this.ui.$(id),signature=JSON.stringify(options.map(o=>[o.value,o.textContent,o.disabled]));
    if(select.dataset.options!==signature){select.replaceChildren(...options);select.dataset.options=signature;}
    if(select.value!==value)select.value=value;
  }
  render() {
    const {$,el,state,pending}=this.ui,request=pending(this.cid),selected=request?.model_settings||this.selection(),rows=this.rows();
    const row=rows.find(r=>r.id===selected?.model),lock=this.locked();
    const options=rows.map(r=>{
      const o=el("option",r.id+(r.configured?" · app default":"")+(!r.available?" · not confirmed":""));
      o.value=r.id;o.disabled=!r.available&&!r.configured;return o;
    });
    if(selected&&!row){const o=el("option",selected.model+" · refresh to check availability");o.value=selected.model;o.disabled=true;options.unshift(o);}
    if(!options.length){const o=el("option","Loading configured model…");o.value="";options.push(o);}
    this.setOptions("model-select",options,selected?.model||"");
    $("model-select").disabled=lock;
    const efforts=row?.efforts||[],effortOptions=efforts.map(v=>{const o=el("option",({none:"None",minimal:"Minimal",low:"Low",medium:"Medium",high:"High",xhigh:"Extra high",max:"Maximum"})[v]||v);o.value=v;return o;});
    if(!efforts.length){const o=el("option",row?"Not applicable to this model":"Unavailable until model is checked");o.value="";effortOptions.push(o);}
    else if(selected?.reasoning_effort&&!efforts.includes(selected.reasoning_effort)){const o=el("option",selected.reasoning_effort+" · unsupported; choose an effort");o.value=selected.reasoning_effort;o.disabled=true;effortOptions.unshift(o);}
    this.setOptions("effort-select",effortOptions,selected?.reasoning_effort||"");
    $("effort-select").disabled=lock||!efforts.length;
    $("refresh-models").disabled=this.loading;$("refresh-models").textContent=this.loading?"Refreshing…":"Refresh from OpenAI";
    $("model-label").textContent=this.label(selected);$("model-label").title="Model & effort for "+(request?"the saved submission":"your next Send");
    $("model-state").textContent=this.loading?"Fetching your account’s available model IDs…":this.error||
      (this.catalog?this.catalog.compatible_count+" compatible models · "+this.catalog.total_count+" IDs returned by your account":"Configured default retained. Refresh to discover available models.");
    $("model-state").classList.toggle("warning",Boolean(this.error));
    $("model-scope").textContent=request?"A submission is unresolved. Its original model and effort are locked until its status is checked.":
      state.conversation?.archived?"Restore this conversation to change the next task’s settings.":
      "Applies to your next Send in this conversation. Running tasks and Resume keep their saved settings. This does not grant permission to make changes.";
    $("model-availability").textContent=!row?.available?"Availability is not confirmed. Only the exact app default can be used as a fallback; the API may still reject it.":
      !this.ready()?"The saved effort is not supported. Choose a supported effort before sending.":
      "Available in the account list. Listing a model does not guarantee quota, project permissions or successful generation.";
    const toolNames=[];
    if(row?.web&&state.status?.web_search&&!(row.family?.startsWith("gpt-5")&&["gpt-5","gpt-5-mini","gpt-5-nano"].includes(row.family)&&selected?.reasoning_effort==="minimal"))toolNames.push("web search");
    if(row?.code&&state.status?.code_interpreter)toolNames.push("code interpreter");
    $("model-tools").textContent=row?.family?"Task tools: appliance tools"+(toolNames.length?", "+toolNames.join(", "):"")+". Some models do not support all hosted tools.":"Configured custom models retain their existing request contract; capability support is not verified.";
  }
}
