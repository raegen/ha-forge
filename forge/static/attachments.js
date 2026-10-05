/* Incoming files: server-owned draft IDs, no file bytes or credentials in localStorage. */
"use strict";
class AttachmentUI {
  constructor(ui) {
    this.ui=ui;this.cid=null;this.items=[];this.loading=false;this.ready=false;this.busy=false;this.error="";this.progress="";this.loadSerial=0;this.rendered="";
    const {$}=ui;
    $("attach-files").addEventListener("click",()=>$("file-picker").click());
    $("file-picker").addEventListener("change",()=>{const files=[...$("file-picker").files];$("file-picker").value="";this.add(files);});
    $("refresh-files").addEventListener("click",()=>this.load(this.cid).catch(e=>this.fail(e)));
    const form=$("compose-form");
    form.addEventListener("dragover",e=>{if([...e.dataTransfer.types].includes("Files")){e.preventDefault();form.classList.add("file-drag");e.dataTransfer.dropEffect=this.canEdit()?"copy":"none";}});
    form.addEventListener("dragleave",e=>{if(!form.contains(e.relatedTarget))form.classList.remove("file-drag");});
    // Never navigate this privileged panel to a dropped file, even outside the composer.
    document.addEventListener("dragover",e=>{if([...e.dataTransfer.types].includes("Files"))e.preventDefault();});
    document.addEventListener("drop",e=>{
      if(![...e.dataTransfer.types].includes("Files"))return;
      e.preventDefault();form.classList.remove("file-drag");this.add([...e.dataTransfer.files]);
    });
    $("composer").addEventListener("paste",e=>{
      const files=[...(e.clipboardData?.items||[])].filter(x=>x.kind==="file").map(x=>x.getAsFile()).filter(Boolean);
      if(files.length){e.preventDefault();this.add(files);}
    });
    $("close-file-preview").addEventListener("click",()=>$("file-preview-dialog").close());
    $("file-preview-dialog").addEventListener("close",()=>$("file-preview-image").removeAttribute("src"));
  }
  size(n){return n>=1048576?(n/1048576).toFixed(1)+" MiB":Math.max(1,Math.ceil(n/1024))+" KiB";}
  ids(){return this.items.map(a=>a.id);}
  pending(){return this.cid&&this.ui.storage.get("pending."+this.cid);}
  canEdit(){const s=this.ui.state;return Boolean(this.cid&&s.conversation&&!s.conversation.archived&&s.online&&s.status?.attachments?.enabled&&!s.sending&&this.ready&&!this.loading&&!this.busy&&!this.pending());}
  notify(){this.ui.update();}
  fail(e){this.error=e.message||String(e);this.ui.toast(this.error);this.notify();}
  reset(cid){this.cid=cid;this.items=[];this.loading=true;this.ready=false;this.error="";this.rendered="";this.loadSerial++;}
  async load(cid=this.cid){
    if(!cid||cid!==this.cid)return;
    const serial=++this.loadSerial;
    if(cid===this.cid){this.loading=true;this.notify();}
    try{
      const result=await this.ui.api("api/conversations/"+cid+"/attachments");
      if(cid!==this.cid||serial!==this.loadSerial)return;
      this.items=result.attachments;this.ready=true;this.ui.state.status.attachments=result.limits;
      this.error="";
      // Reconcile completed uploads by ID after a lost browser response. Never POST on reload.
      const pending=this.ui.storage.get("uploads."+cid,{});
      for(const [signature,uploadId] of Object.entries(pending))if(this.items.some(a=>a.upload_id===uploadId))delete pending[signature];
      this.ui.storage.set("uploads."+cid,pending);
    }catch(e){if(cid===this.cid&&serial===this.loadSerial){this.error=e.message;this.ready=false;}throw e;}
    finally{if(cid===this.cid&&serial===this.loadSerial){this.loading=false;this.notify();}}
  }
  url(cid,id,suffix){return new URL("./api/conversations/"+encodeURIComponent(cid)+"/attachments/"+encodeURIComponent(id)+"/"+suffix,document.baseURI).href;}
  preview(a,cid){
    const {$}=this.ui;
    $("file-preview-title").textContent=a.filename;
    $("file-preview-image").alt=a.filename;
    $("file-preview-image").src=this.url(cid,a.id,"content?view=1");
    $("file-preview-download").href=this.url(cid,a.id,"content");
    $("file-preview-download").download=a.filename;
    $("file-preview-note").textContent="Optimized image · "+a.width+" × "+a.height+" · EXIF/location metadata removed";
    $("file-preview-dialog").showModal();
  }
  cards(items,cid,editable=false){
    const {el}=this.ui,list=el("div",null,"file-cards");
    for(const a of items){
      const card=el("div",null,"file-card"),info=el("div",null,"file-info");card.dataset.attachmentId=a.id;
      if(a.kind==="image"){
        const b=el("button",null,"file-thumbnail"),img=el("img");b.type="button";b.setAttribute("aria-label","Preview "+a.filename);
        img.src=this.url(cid,a.id,"thumbnail");img.alt="";img.loading="lazy";img.width=72;img.height=56;
        b.append(img);b.addEventListener("click",()=>this.preview(a,cid));card.append(b);
      }else card.append(el("span",a.kind==="pdf"?"PDF":"TXT","file-type"));
      const name=el("span",a.filename,"file-name");name.title=a.filename;
      info.append(name,el("small",this.size(a.size)+(a.normalized?" · optimized":"")));
      const download=el("a","Download","file-download");download.href=this.url(cid,a.id,"content");download.download=a.filename;info.append(download);card.append(info);
      if(editable){
        const remove=el("button","×","icon file-remove");remove.type="button";remove.disabled=!this.canEdit();remove.setAttribute("aria-label","Remove "+a.filename);
        remove.addEventListener("click",()=>this.remove(cid,a.id));card.append(remove);
      }
      list.append(card);
    }
    return list;
  }
  render(){
    const {$,state}=this.ui,limits=state.status?.attachments;
    $("attach-files").disabled=!this.canEdit();$("file-picker").disabled=!this.canEdit();
    $("refresh-files").disabled=!this.cid||this.loading||Boolean(this.busy)||state.sending;
    $("file-progress").hidden=!this.progress;$("file-progress").textContent=this.progress;
    $("file-error").hidden=!this.error;$("file-error").textContent=this.error;
    $("file-summary").textContent=this.loading?"Loading saved attachments…":this.items.length?this.items.length+" / 4 files · "+this.size(this.items.reduce((n,a)=>n+a.size,0)):"Photos, PDF, text & logs";
    $("file-quota").textContent=limits?this.size(limits.storage_used_bytes)+" / "+this.size(limits.storage_limit_bytes)+" stored on appliance":"";
    const signature=JSON.stringify([this.cid,this.ids(),this.canEdit()]);
    if(signature!==this.rendered){$("draft-files").replaceChildren(this.cards(this.items,this.cid,true));this.rendered=signature;}
    $("draft-files").hidden=!this.items.length;
  }
  async remove(cid,id){
    if(!this.canEdit())return;
    this.busy=cid;this.notify();
    try{await this.ui.api("api/conversations/"+cid+"/attachments/"+id+"/remove",{});await this.load(cid);}
    catch(e){if(cid===this.cid)this.fail(e);}
    finally{this.busy=false;this.notify();}
  }
  upload(cid,file,uploadId,index,total){
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open("POST",new URL("./api/conversations/"+cid+"/attachments",document.baseURI));
      xhr.timeout=120000;xhr.withCredentials=true;
      xhr.setRequestHeader("Content-Type","application/octet-stream");xhr.setRequestHeader("X-Forge-CSRF",this.ui.state.csrf);
      xhr.setRequestHeader("X-File-Name",encodeURIComponent(file.name));xhr.setRequestHeader("X-Upload-ID",uploadId);
      const progress=percent=>{this.progress="Uploading "+index+"/"+total+": "+file.name+" · "+percent;this.notify();};
      progress("starting");xhr.upload.onprogress=e=>{if(e.lengthComputable)progress(Math.round(100*e.loaded/e.total)+"%");};
      xhr.onload=()=>{
        let data;try{data=JSON.parse(xhr.responseText);}catch{reject(new Error("Upload response was interrupted. Refresh attachments before retrying."));return;}
        if(xhr.status<200||xhr.status>=300||!data.success){const e=new Error(data.error||"Upload failed");e.status=xhr.status;reject(e);return;}
        resolve(data.result);
      };
      xhr.onerror=xhr.ontimeout=xhr.onabort=()=>reject(new Error("Upload connection interrupted. Checking saved files; no message has been sent. Reselect the same file to retry safely if it is missing."));
      xhr.send(file);
    });
  }
  async add(files){
    if(!files.length)return;
    if(!this.canEdit()){this.ui.toast(this.pending()?"Reconnect to resolve the pending message before changing its attachments.":"Wait for the draft to load, or reconnect/restore this chat before attaching files.");return;}
    const cid=this.cid,limits=this.ui.state.status.attachments;
    if(files.length+this.items.length>limits.max_count){this.fail(new Error("Choose at most four files per message."));return;}
    if(files.some(f=>!f.size||f.size>limits.max_file_bytes)){this.fail(new Error("Each attachment must be non-empty and no larger than 10 MiB."));return;}
    if(files.reduce((n,f)=>n+f.size,0)+this.items.reduce((n,a)=>n+a.original_size,0)>limits.max_message_bytes){this.fail(new Error("Attachments are limited to 20 MiB per message."));return;}
    this.busy=cid;this.error="";this.notify();
    let failure=null;
    try{
      for(const [index,file] of files.entries()){
        const signature=JSON.stringify([file.name,file.size,file.lastModified]),pending=this.ui.storage.get("uploads."+cid,{});
        const uploadId=pending[signature]||this.ui.key();pending[signature]=uploadId;this.ui.storage.set("uploads."+cid,pending);
        try{
          await this.upload(cid,file,uploadId,index+1,files.length);
          const latest=this.ui.storage.get("uploads."+cid,{});delete latest[signature];this.ui.storage.set("uploads."+cid,latest);
        }catch(e){
          if(e.status&&e.status<500&&e.status!==403){const latest=this.ui.storage.get("uploads."+cid,{});delete latest[signature];this.ui.storage.set("uploads."+cid,latest);}
          throw e;
        }
        // Switching chats doesn't retarget a queued upload batch.
      }
    }catch(e){failure=e;}
    finally{
      this.busy=false;this.progress="";
      if(cid===this.cid){
        try{await this.load(cid);}catch(e){failure=failure||e;}
        if(failure)this.fail(failure);
      }else this.ui.toast(failure?"Upload in the previous chat needs checking: "+failure.message:"Files attached to the previous conversation's draft.");
      this.notify();
    }
  }
}
