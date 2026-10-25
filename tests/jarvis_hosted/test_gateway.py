import asyncio,json,unittest
from jarvis_hosted.gateway import Gateway
TOKEN='a'*48
class GatewayTests(unittest.TestCase):
 def run_request(self,path,method='GET',body=None,token=TOKEN):
  self.seen=[]
  async def upstream(scope,receive,send):
   self.seen.append(scope)
   req=await receive()
   if scope['path'].endswith('/auth'):
    result={'connected':True,'state':'connected','revision':2,'access_token':'must-not-leak'}
   elif scope['path']=='/muse-code/models':
    result={'data':[{'provider_model_ref':'openai/verified-model'},{'provider_model_ref':'openai_api/paid-model'}]}
   elif scope['path']=='/v1/messages':
    self.provider_body=json.loads(req['body']);result={'content':[{'type':'text','text':'Synthetic response'}]}
   else: result={'connected':False}
   await send({'type':'http.response.start','status':200})
   await send({'type':'http.response.body','body':json.dumps(result).encode()})
  gateway=Gateway(upstream,TOKEN,'inner-token')
  events=[];sent=False
  async def receive():
   return {'type':'http.request','body':json.dumps(body or {}).encode(),'more_body':False}
  async def send(x):events.append(x)
  headers=[(b'content-type',b'application/json')]
  if token:headers.append((b'authorization',('Bearer '+token).encode()))
  asyncio.run(gateway({'type':'http','path':path,'method':method,'headers':headers},receive,send))
  return events[0]['status'],json.loads(events[1]['body'])
 def test_no_auth_and_arbitrary_routes_never_reach_upstream(self):
  self.assertEqual(self.run_request('/bridge/status',token=None)[0],401);self.assertEqual(self.seen,[])
  for path in ['/admin','/v1/messages','/admin/api/config/apply','/bridge/../../admin','/code']:
   self.assertEqual(self.run_request(path)[0],404);self.assertEqual(self.seen,[])
 def test_status_excludes_credentials_and_models_exclude_paid_family(self):
  code,result=self.run_request('/bridge/status');self.assertEqual(code,200);self.assertNotIn('access_token',result);self.assertEqual(result['storage'],'ephemeral');self.assertIn('session_id',result)
  _,models=self.run_request('/bridge/models');self.assertEqual(models['models'],[{'id':'openai/verified-model','name':'openai/verified-model'}])
 def test_login_uses_fixed_device_flow_and_no_remote_admin_input(self):
  self.assertEqual(self.run_request('/bridge/login','POST',{'provider':'openai_api'})[0],400);self.assertEqual(self.seen,[])
  self.assertEqual(self.run_request('/bridge/login','POST')[0],200);self.assertEqual(self.seen[0]['client'][0],'127.0.0.1');self.assertEqual(self.seen[0]['path'],'/admin/api/providers/openai/auth/login')
 def test_only_advertised_openai_models_and_text_messages_reach_provider(self):
  body={'model':'openai/verified-model','messages':[{'role':'system','content':'Business only'},{'role':'user','content':'Hello'}],'max_tokens':20}
  code,result=self.run_request('/bridge/respond','POST',body);self.assertEqual(code,200);self.assertEqual(result['text'],'Synthetic response');self.assertNotIn('tools',self.provider_body)
  for change in [{'model':'openai_api/paid'},{'model':'openai/invented'},{'tools':[]},{'max_tokens':50000},{'messages':[{'role':'tool','content':'do work'}]}]:
   self.assertEqual(self.run_request('/bridge/respond','POST',{**body,**change})[0],400)
 def test_weak_transport_secret_is_rejected(self):
  with self.assertRaises(RuntimeError):Gateway(None,'short','inner')
if __name__=='__main__':unittest.main()
