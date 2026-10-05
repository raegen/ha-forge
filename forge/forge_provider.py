"""Responses API background transport: durable IDs, bounded I/O and cancellation."""
from __future__ import annotations
import datetime as dt
import email.utils
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

class Cancelled(Exception):
    pass
class Interrupted(Exception):
    pass
class AmbiguousRequest(Interrupted):
    pass
class ProviderError(Exception):
    pass

def retry_seconds(headers, attempt):
    values=[]
    for key in ("Retry-After","x-ratelimit-reset-requests","x-ratelimit-reset-tokens"):
        raw=(headers.get(key,"") or "").strip()
        if not raw: continue
        try:
            values.append(float(raw));continue
        except ValueError: pass
        try:
            date=email.utils.parsedate_to_datetime(raw)
            values.append((date-dt.datetime.now(dt.timezone.utc)).total_seconds());continue
        except (ValueError,TypeError,OverflowError): pass
        matches=re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)",raw)
        if matches:
            values.append(sum(float(n)*{"ms":.001,"s":1,"m":60,"h":3600}[u] for n,u in matches))
    return max(0.1,max(values) if values else min(30,2**attempt))+.25

class NoModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Model endpoint redirected unexpectedly; credentials were not forwarded")

class Provider:
    def __init__(self, tools, opener=None):
        self.f=tools
        self.opener=opener or urllib.request.urlopen
        self.model_opener=opener or urllib.request.build_opener(NoModelRedirect()).open

    def list_models(self):
        """Read-only discovery. No inference, prompt, project files, or raw errors."""
        if not self.f.OPENAI_API_KEY:
            raise ProviderError("OpenAI API key is not configured in app options")
        req=urllib.request.Request("https://api.openai.com/v1/models",method="GET",headers={
            "Authorization":"Bearer "+self.f.OPENAI_API_KEY,"Accept":"application/json",
            "User-Agent":"Forge/"+self.f.VERSION})
        try:
            with self.model_opener(req,timeout=10) as stream:
                raw=stream.read(1024*1024+1)
            if len(raw)>1024*1024:raise ProviderError("OpenAI model list exceeds the 1 MiB limit")
            value=json.loads(raw)
            if not isinstance(value,dict) or not isinstance(value.get("data"),list):
                raise ProviderError("OpenAI returned an invalid model list")
            return value["data"]
        except urllib.error.HTTPError as exc:
            exc.close()
            hints={401:"Check the configured API key.",403:"The API key needs permission to list models.",
                   429:"Rate limited; wait before refreshing."}
            raise ProviderError("OpenAI model list: HTTP "+str(exc.code)+". "+hints.get(exc.code,"Try again later.")) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("Unable to reach the OpenAI model list. Check connectivity and retry.") from None

    def request(self,method,path="",payload=None,client_id=None):
        if not self.f.OPENAI_API_KEY:
            raise ProviderError("OpenAI API key is not configured in app options")
        headers={"Authorization":"Bearer "+self.f.OPENAI_API_KEY,"Content-Type":"application/json",
                 "User-Agent":"Forge/"+self.f.VERSION}
        if client_id: headers["X-Client-Request-Id"]=client_id
        req=urllib.request.Request(self.f.OPENAI_RESPONSES_URL+path,method=method,headers=headers,
                                  data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None)
        with self.opener(req,timeout=min(30,self.f.OPENAI_TIMEOUT)) as stream:
            raw=stream.read(16*1024*1024+1)
            if len(raw)>16*1024*1024:
                error=AmbiguousRequest if method=="POST" and not path else ProviderError
                raise error("Provider response exceeds 16 MiB limit; a submitted request may still exist.")
        value=json.loads(raw)
        if not isinstance(value,dict) or not isinstance(value.get("id"),str):
            error=AmbiguousRequest if method=="POST" and not path else ProviderError
            raise error("Provider returned an invalid response envelope; a submitted request may still exist.")
        return value

    @staticmethod
    def pause(seconds,check):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            check();time.sleep(min(.25,max(0,until-time.monotonic())))

    def create(self,payload,client_id,check,event):
        attempt=0; waited=0.0
        while True:
            check()
            try:
                response=self.request("POST",payload=payload,client_id=client_id)
                if (not isinstance(response,dict) or
                    not re.fullmatch(r"resp_[A-Za-z0-9_-]+",str(response.get("id",""))) or
                    response.get("status") not in {"queued","in_progress","completed","failed","cancelled","incomplete"}):
                    raise AmbiguousRequest("The provider returned an unusable response. It may have created a request; explicit Resume is required before resubmission.")
                return response
            except urllib.error.HTTPError as exc:
                with exc: body=exc.read(8192).decode("utf-8","replace")
                if exc.code==429 and "insufficient_quota" not in body:
                    attempt+=1; delay=retry_seconds(exc.headers,attempt)
                    if waited+delay<=self.f.OPENAI_RATE_LIMIT_MAX_WAIT:
                        event("rate_limit",f"Provider rate limited; waiting {delay:.1f}s before retry {attempt}.")
                        self.pause(delay,check);waited+=delay;continue
                if exc.code>=500:
                    raise AmbiguousRequest(f"Provider HTTP {exc.code} during submission. Outcome unknown; no local tools were run from this request.") from exc
                raise ProviderError(f"Provider HTTP {exc.code}: {self.f.REDACT.text(body)[:2000]}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as exc:
                # A tracking header is NOT an idempotency guarantee.
                raise AmbiguousRequest("Connection interrupted while submitting a provider request. "
                                       "The remote outcome is unknown. Resume explicitly; another request may incur additional cost.") from exc

    def retrieve(self,rid,check,event):
        if not re.fullmatch(r"resp_[A-Za-z0-9_-]+",rid):
            raise ProviderError("Invalid saved provider response ID")
        for attempt in range(6):
            check()
            try: return self.request("GET","/"+urllib.parse.quote(rid,safe=""))
            except urllib.error.HTTPError as exc:
                with exc: body=exc.read(4096).decode("utf-8","replace")
                if exc.code not in (408,429,500,502,503,504):
                    raise ProviderError(f"Provider retrieve HTTP {exc.code}: {self.f.REDACT.text(body)[:1000]}") from exc
                delay=min(30,retry_seconds(exc.headers,attempt))
            except (urllib.error.URLError, TimeoutError,ConnectionError,OSError,json.JSONDecodeError):
                delay=min(20,2**attempt)
            if attempt==5: break
            event("connection",f"Provider connection interrupted; retrying retrieval in {delay:.1f}s. The saved request will not be resubmitted.")
            self.pause(delay,check)
        raise Interrupted("Provider retrieval retries exhausted. Resume to retrieve the same saved response.")

    def wait(self,response,check,event,save):
        deadline=time.monotonic()+self.f.OPENAI_TIMEOUT
        heartbeat=0
        while response.get("status") in {"queued","in_progress"}:
            check()
            if time.monotonic()>=deadline:
                raise Interrupted("Provider wait deadline reached. Response ID is saved; Resume retrieves it without resubmission.")
            if time.monotonic()-heartbeat>=15:
                event("model",f"Provider {response['status'].replace('_',' ')}; response ID checkpointed.")
                heartbeat=time.monotonic()
            self.pause(1.5,check)
            response=self.retrieve(response["id"],check,event)
            # Persist terminal response before returning to the execution loop.
            if response.get("status") not in {"queued","in_progress"}: save(response)
        return response

    def artifact(self,annotation):
        container=annotation.get("container_id","");file_id=annotation.get("file_id","")
        if not re.fullmatch(r"cntr_[A-Za-z0-9_-]+",container) or not re.fullmatch(r"cfile_[A-Za-z0-9_-]+",file_id):
            raise ProviderError("Invalid provider artifact identifier")
        url=self.f.OPENAI_RESPONSES_URL.rsplit("/",1)[0]+f"/containers/{container}/files/{file_id}/content"
        request=urllib.request.Request(url,headers={"Authorization":"Bearer "+self.f.OPENAI_API_KEY,
            "User-Agent":"Forge/"+self.f.VERSION})
        try:
            with self.opener(request,timeout=30) as stream:
                data=stream.read(20*1024*1024+1)
            if len(data)>20*1024*1024:raise ProviderError("Artifact exceeds the 20 MiB download limit")
            return data
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ProviderError(f"Artifact unavailable (provider HTTP {exc.code}). Its container may have expired; ask Forge to regenerate it.") from exc

    def cancel(self,rid):
        try:
            return self.request("POST","/"+urllib.parse.quote(rid,safe="")+"/cancel",{})
        except Exception as exc:
            if isinstance(exc,urllib.error.HTTPError):exc.close()
            raise ProviderError("Could not confirm provider cancellation; local future actions remain stopped.") from exc
