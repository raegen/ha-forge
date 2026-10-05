/* A remembered human choice, never a live task grant. No automatic POST/retry. */
"use strict";
class ConversationPermission {
  constructor(ui) {
    this.ui=ui;this.cid=null;this.value=false;this.revision=null;this.saves=new Map();
  }
  reset(cid) {
    if(cid!==this.cid)this.value=false;
    this.cid=cid;this.revision=null;this.render();
  }
  accept(c) {
    if(!c||c.id!==this.cid||c.id!==this.ui.state.cid)return;
    const revision=c.allow_changes_revision;
    if(typeof c.allow_changes_default!=="boolean"||!Number.isSafeInteger(revision)||revision<0)return;
    // A late poll/POST response must not overwrite a more recent uncheck.
    if(this.revision!==null&&revision<this.revision)return;
    this.value=c.allow_changes_default;this.revision=revision;this.ui.update();
  }
  ready() {
    return Boolean(this.cid&&this.cid===this.ui.state.cid&&this.revision!==null&&!this.saves.has(this.cid));
  }
  render() {
    const {state,checkbox,pending}=this.ui,request=pending(this.cid),saving=this.saves.get(this.cid);
    // An uncertain submission keeps its original permission snapshot/request ID.
    checkbox.checked=request?request.allow_changes===true:saving?saving.value:this.value;
    // This choice applies to the next explicit Send, never the running task's grant.
    checkbox.disabled=!this.ready()||!state.online||!state.conversation||Boolean(state.conversation.archived)||state.sending||Boolean(request);
  }
  async save() {
    const {state,checkbox,api}=this.ui;
    if(checkbox.disabled||!this.ready()){this.render();return;}
    const cid=this.cid,value=checkbox.checked,revision=this.revision;
    this.saves.set(cid,{value});this.ui.update();
    try {
      const c=await api("api/conversations/"+cid+"/change-preference",{
        allow_changes_default:value,allow_changes_revision:revision
      });
      this.accept(c);
    } catch(e) {
      if(this.cid===cid&&state.cid===cid){
        this.revision=null;this.value=false;this.saves.set(cid,{value:false});this.ui.error(e);this.ui.update();
        if(e.status===403){try{await this.ui.refreshStatus();}catch{}}
        // A lost reply may have committed. Read back; never silently repeat the POST.
        try{this.accept(await api("api/conversations/"+cid));}catch{}
      }
    } finally {
      this.saves.delete(cid);this.ui.update();
    }
  }
}
