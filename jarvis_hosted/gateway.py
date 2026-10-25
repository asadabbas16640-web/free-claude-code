"""Owner-authenticated, subscription-only JARVIS adapter around FCC.

This exposes no FCC admin UI, file browser, code session, shell or provider settings.
Render free storage is ephemeral: reconnect after restart; no token export route exists.
"""
import asyncio
import json
import os
import re
import secrets
import uuid

MODEL = re.compile(r"openai/[A-Za-z0-9_.-]{1,100}\Z")
AUTH_PATH = "/admin/api/providers/openai/auth"


class Gateway:
    def __init__(self, upstream, token, proxy_token):
        if not isinstance(token, str) or len(token) < 40:
            raise RuntimeError("A strong JARVIS_BRIDGE_TOKEN is required")
        self.upstream, self.token, self.proxy_token = upstream, token, proxy_token
        self.instance = uuid.uuid4().hex
        self.lock = asyncio.Lock()

    async def internal(self, path, method="GET", body=None):
        payload = json.dumps(body).encode() if body is not None else b""
        sent, chunks, status = False, [], 500
        async def receive():
            nonlocal sent
            if sent:
                await asyncio.Event().wait()
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if sum(map(len, chunks)) > 256000:
                    raise ValueError("Upstream response too large")
        # This loopback scope is used ONLY after transport authorization and fixed-route validation.
        scope = {"type":"http","asgi":{"version":"3.0"},"http_version":"1.1",
                 "method":method,"scheme":"http","path":path,"raw_path":path.encode(),
                 "query_string":b"","root_path":"","server":("127.0.0.1",8082),
                 "client":("127.0.0.1",1234),"headers":[(b"host",b"127.0.0.1:8082"),
                 (b"content-type",b"application/json"),(b"authorization",("Bearer "+self.proxy_token).encode())]}
        await asyncio.wait_for(self.upstream(scope, receive, send), timeout=75)
        try:
            data = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise ValueError("Upstream returned invalid JSON") from None
        if status >= 400:
            return status, {"error":"FCC could not complete this operation. Check provider connection, quota or server logs."}
        return status, data

    async def status(self):
        status, data = await self.internal(AUTH_PATH)
        if status >= 400:
            return status, data
        allowed = ("state","connected","revision","verification_url","user_code","expires_at","email")
        safe = {key:data[key] for key in allowed if key in data}
        safe.update(provider="openai", session_id=self.instance+"-"+str(data.get("revision",0)),
                    storage="ephemeral", paid_api_fallback=False)
        return 200, safe

    async def models(self):
        code, state = await self.status()
        if code != 200 or not state.get("connected"):
            return 409, {"error":"Connect ChatGPT before loading models"}
        code, data = await self.internal("/muse-code/models")
        if code >= 400:
            return code, data
        models = []
        for item in data.get("data", []):
            ref = item.get("provider_model_ref", item.get("id", ""))
            if isinstance(ref,str) and MODEL.fullmatch(ref):
                models.append({"id":ref,"name":str(item.get("display_name",ref))[:160]})
        return 200, {"models":models[:100]}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.upstream(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type":"websocket.close","code":1008})
            return
        async def reply(code, data):
            payload=json.dumps(data).encode()
            await send({"type":"http.response.start","status":code,"headers":[(b"content-type",b"application/json"),(b"cache-control",b"no-store")]})
            await send({"type":"http.response.body","body":payload})
        path, method = scope["path"], scope["method"]
        if path == "/health" and method == "GET":
            await reply(200,{"status":"ready","model_connected":"check authenticated status"})
            return
        headers = dict(scope.get("headers",[]))
        authorization = headers.get(b"authorization",b"")
        if not secrets.compare_digest(authorization, ("Bearer "+self.token).encode()):
            await reply(401,{"error":"Owner gateway authorization required"})
            return
        allowed = {("/bridge/status","GET"),("/bridge/models","GET"),
                   ("/bridge/login","POST"),("/bridge/cancel","POST"),
                   ("/bridge/disconnect","POST"),("/bridge/respond","POST")}
        if (path,method) not in allowed:
            await reply(404,{"error":"Route unavailable"})
            return
        try:
            body=b""
            if method == "POST":
                if headers.get(b"content-type",b"").split(b";")[0] != b"application/json":
                    await reply(415,{"error":"JSON required"});return
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body += event.get("body",b"")
                    if len(body)>65536:
                        await reply(413,{"error":"Request too large"});return
                    if not event.get("more_body"):
                        break
                body=json.loads(body or b"{}")
                if not isinstance(body,dict):
                    raise ValueError("Object required")
                if path!="/bridge/respond" and body:
                    raise ValueError("This operation takes no arguments")
            async with self.lock:
                if path=="/bridge/status":
                    code,result=await self.status()
                elif path=="/bridge/models":
                    code,result=await self.models()
                elif path in {"/bridge/login","/bridge/cancel","/bridge/disconnect"}:
                    suffix={"/bridge/login":"/login","/bridge/cancel":"/cancel","/bridge/disconnect":""}[path]
                    code,result=await self.internal(AUTH_PATH+suffix,"DELETE" if path.endswith("disconnect") else "POST",{"mode":"device"} if path.endswith("login") else None)
                    if code<400:
                        code,result=await self.status()
                else:
                    if set(body)!={"model","messages","max_tokens"}:
                        raise ValueError("Unsupported response fields")
                    model,messages,limit=body["model"],body["messages"],body["max_tokens"]
                    if not isinstance(model,str) or not MODEL.fullmatch(model):
                        raise ValueError("Only a connected ChatGPT model is allowed")
                    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=4096:
                        raise ValueError("Invalid output limit")
                    if not isinstance(messages,list) or not 1<=len(messages)<=12:
                        raise ValueError("Invalid messages")
                    for message in messages:
                        if not isinstance(message,dict) or set(message)!={"role","content"} or message["role"] not in ("system","user","assistant") or not isinstance(message["content"],str):
                            raise ValueError("Text messages only")
                    if sum(len(m["content"]) for m in messages)>32000:
                        raise ValueError("Conversation too large")
                    code,catalog=await self.models()
                    if code>=400:
                        await reply(code,catalog);return
                    if model not in {item["id"] for item in catalog["models"]}:
                        raise ValueError("Select an advertised connected model")
                    system="\n".join(m["content"] for m in messages if m["role"]=="system")
                    conversation=[m for m in messages if m["role"]!="system"]
                    if not conversation:
                        raise ValueError("User conversation required")
                    code,result=await self.internal("/v1/messages","POST",{"model":model,"max_tokens":limit,"messages":conversation,"system":system,"stream":False})
                    if code<400:
                        text="\n".join(p.get("text","") for p in result.get("content",[]) if p.get("type")=="text")
                        if not text.strip():
                            raise ValueError("Provider returned no answer")
                        result={"text":text[:20000],"model":model}
            await reply(code,result)
        except (ValueError,TypeError,KeyError):
            await reply(400,{"error":"Gateway request or provider result was invalid"})
        except Exception:
            await reply(503,{"error":"FCC is temporarily unavailable. No alternate paid provider was used."})


def create_gateway():
    # Imported lazily so the boundary tests require no provider packages or credentials.
    from free_claude_code.config.loader import get_settings
    from free_claude_code.runtime.bootstrap import build_asgi_app
    settings=get_settings()
    if settings.model_fallbacks:
        raise RuntimeError("Model fallback must be disabled for the JARVIS gateway")
    upstream=build_asgi_app(settings)
    return Gateway(upstream,os.environ.get("JARVIS_BRIDGE_TOKEN",""),settings.proxy_auth_token)
