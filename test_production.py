import os,sys,tempfile,unittest,subprocess,socket,time,json,urllib.request,urllib.error,http.cookiejar,concurrent.futures,io,sqlite3
from pathlib import Path
os.environ['MINI_SECURE']='0'
import server,wsgi
class Client:
 def __init__(self,base):self.base=base;self.csrf='';self.cookies=http.cookiejar.CookieJar();self.op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
 def call(self,p,d=None,origin=None):
  req=urllib.request.Request(self.base+p,data=json.dumps(d).encode() if d is not None else None,headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf,'Origin':origin or self.base})
  try:
   with self.op.open(req) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def login(self,uid,pw):
  code,d=self.call('/api/login',dict(username=uid,password=pw))
  if code==200:self.csrf=d['csrf']
  return code,d
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();server.DB=Path(cls.tmp.name)/'records.sqlite3';server.init();server.create_teacher('ejteacher','Teacher-Test-12345')
  sock=socket.socket();sock.bind(('127.0.0.1',0));cls.port=sock.getsockname()[1];sock.close();cls.base='http://127.0.0.1:'+str(cls.port);cls.start();cls.teacher=Client(cls.base);assert cls.teacher.login('ejteacher','Teacher-Test-12345')[0]==200
 @classmethod
 def start(cls):
  cls.process=subprocess.Popen([sys.executable,'-m','gunicorn','wsgi:application','--bind','127.0.0.1:'+str(cls.port),'--workers','2','--threads','2','--error-logfile',str(Path(cls.tmp.name)/'gunicorn.log')],env=dict(os.environ,MINI_DB=str(server.DB)))
  for _ in range(100):
   try:
    if urllib.request.urlopen(cls.base+'/healthz').status==200:return
   except Exception:time.sleep(.05)
  raise RuntimeError('Gunicorn start failed')
 @classmethod
 def tearDownClass(cls):cls.process.terminate();cls.process.wait(timeout=10);cls.tmp.cleanup()
 def create(self,uid,cohort=1,cl='A'):
  code,x=self.teacher.call('/api/teacher/create',dict(uid=uid,name='가상 '+uid,cohort=cohort,cls=cl));self.assertEqual(code,200,x)
  s=Client(self.base);self.assertEqual(s.login(uid,x['temporary_password'])[0],200);return s,x['temporary_password']
 def test_01_accounts(self):
  self.assertEqual(Client(self.base).login('teacher','Teach-2026!')[0],401)
  s,pw=self.create('student01');self.assertEqual(s.call('/api/lesson?day=1')[0],403);self.assertTrue(s.call('/api/state')[1]['user']['must_change'])
  self.assertEqual(s.call('/api/password',dict(current=pw,new='Student-New-12345'))[0],200);self.assertEqual(s.call('/api/state')[0],401);self.assertEqual(s.login('student01','Student-New-12345')[0],200)
  self.assertEqual(s.call('/api/teacher/create',{'uid':'hack'})[0],403);self.assertEqual(s.call('/api/state?uid=ejteacher')[1]['user']['id'],'student01')
 def test_02_five_students_ten_days(self):
  for d in range(1,11):self.assertEqual(self.teacher.call('/api/teacher/approve',dict(day=d,approved=True))[0],200)
  for i in range(5):
   s,pw=self.create('class'+str(i),i%2+1,'A' if i<3 else 'B');s.call('/api/password',dict(current=pw,new='School-New-12345'));s.login('class'+str(i),'School-New-12345')
   for b in server.seeds():
    d=b['day'];code,l=s.call('/api/lesson?day='+str(d));self.assertEqual(code,200);self.assertNotIn('vocabulary_all',l);self.assertNotIn('answer',l['vocab'][0]);self.assertNotIn('analysis',l['passages'][0])
    qs=[q for q in b['questions'] if q.get('origin')=='paper'];answers={q['id']:q['answer'] for q in qs};answers[qs[0]['id']]=(qs[0]['answer']+1)%5
    code,x=s.call('/api/paper',dict(day=d,answers=answers));self.assertEqual(code,200,x);self.assertEqual(x['correct'],9)
    answers[qs[0]['id']]=qs[0]['answer'];self.assertEqual(s.call('/api/paper',dict(day=d,answers=answers))[1]['correct'],10)
    for p in b['passages']:self.assertEqual(s.call('/api/passage',dict(day=d,passage_id=p['id']))[1]['translation'],p['translation'])
    run=s.call('/api/vocab/start',{'day':d})[1];vs={v['id']:v for v in b['vocab']};a={v['id']:(v['answer'] if v['mode']=='mc' else v['word'] if v['mode']=='ko-en' else v['meaning']) for v in b['vocab']};a['v2']='wrong'
    self.assertEqual(s.call('/api/vocab/submit',dict(day=d,token=run['token'],answers=a))[1]['correct'],17)
    retry=s.call('/api/vocab/start',dict(day=d,retry=True))[1];self.assertEqual(len(retry['items']),1);a['v2']=vs['v2']['word'];self.assertEqual(s.call('/api/vocab/submit',dict(day=d,token=retry['token'],answers=a))[1]['correct'],1)
    self.assertEqual(s.call('/api/syntax',dict(day=d,sid=b['syntax'][0]['id'],answer='가상 구문 분석'))[0],200)
   rec=s.call('/api/state')[1]['records'];self.assertEqual(len(rec['notes']),10);self.assertTrue(all(x['resolved'] for x in rec['notes']))
  state=self.teacher.call('/api/state')[1];self.assertEqual(len(state['students']),6);self.assertEqual({(x['user']['cohort'],x['user']['cls']) for x in state['students']},{(1,'A'),(2,'A'),(1,'B'),(2,'B')})
 def test_03_atomic_replay(self):
  s=Client(self.base);s.login('class0','School-New-12345');before=s.call('/api/state')[1]['records'];self.assertEqual(s.call('/api/paper',dict(day=1,answers={'p01':0}))[0],400);self.assertEqual(before,s.call('/api/state')[1]['records'])
  run=s.call('/api/vocab/start',{'day':1})[1];cookies='; '.join(c.name+'='+c.value for c in s.cookies)
  def send(_):
   req=urllib.request.Request(self.base+'/api/vocab/submit',data=json.dumps(dict(day=1,token=run['token'],answers={})).encode(),headers={'Content-Type':'application/json','Origin':self.base,'Cookie':cookies,'X-CSRF-Token':s.csrf})
   try:return urllib.request.urlopen(req).status
   except urllib.error.HTTPError as e:return e.code
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:codes=list(pool.map(send,range(2)))
  self.assertEqual(sorted(codes),[200,400])
 def test_04_reset_disable_delete(self):
  s,pw=self.create('removable');s.call('/api/password',dict(current=pw,new='Remove-User-12345'));s.login('removable','Remove-User-12345');x=self.teacher.call('/api/teacher/reset',{'uid':'removable'})[1]
  self.assertEqual(s.call('/api/state')[0],401);self.assertEqual(s.login('removable','Remove-User-12345')[0],401);self.assertEqual(s.login('removable',x['temporary_password'])[0],200)
  self.assertEqual(self.teacher.call('/api/teacher/active',dict(uid='removable',active=False))[0],200);self.assertEqual(s.call('/api/state')[0],401);self.assertEqual(s.login('removable',x['temporary_password'])[0],401)
  self.assertEqual(self.teacher.call('/api/teacher/delete',dict(uid='removable',confirm='wrong'))[0],400);self.assertEqual(self.teacher.call('/api/teacher/delete',dict(uid='removable',confirm='removable'))[0],200)
 def test_05_csrf_rate(self):
  self.assertEqual(self.teacher.call('/api/teacher/approve',dict(day=1,approved=True),origin='https://evil.invalid')[0],403)
  csrf=self.teacher.csrf;self.teacher.csrf='bad';self.assertEqual(self.teacher.call('/api/teacher/approve',dict(day=1,approved=True))[0],403);self.teacher.csrf=csrf
  a=Client(self.base)
  for i in range(10):self.assertEqual(a.login('nonexistent','wrong')[0],401)
  self.assertEqual(a.login('nonexistent','wrong')[0],429)
 def test_06_export_backup_restart(self):
  self.assertEqual(len(self.teacher.call('/api/teacher/export',{})[1]['students']),6);backup=Path(self.tmp.name)/'backup.db';subprocess.run([sys.executable,'manage.py','backup','--output',str(backup)],env=dict(os.environ,MINI_DB=str(server.DB)),check=True,capture_output=True)
  with sqlite3.connect(backup) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertGreater(c.execute('SELECT count(*) FROM attempts').fetchone()[0],1000)
  before=self.teacher.call('/api/state')[1]['students'];self.process.terminate();self.process.wait(timeout=10);type(self).start();self.assertEqual(before,self.teacher.call('/api/state')[1]['students'])
 def test_07_parallel_submissions(self):
  clients=[]
  for i in range(5):
   c=Client(self.base);self.assertEqual(c.login('class'+str(i),'School-New-12345')[0],200);clients.append(c)
  b=server.seeds()[0];answers={q['id']:q['answer'] for q in b['questions'] if q.get('origin')=='paper'}
  def submit(i):return clients[i%5].call('/api/paper',dict(day=1,answers=answers))
  with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(submit,range(20)))
  self.assertTrue(all(code==200 and x['correct']==10 for code,x in results),results)
 def test_08_secure_cookie(self):
  old,origin=server.SECURE,server.ORIGIN;server.SECURE=True;server.ORIGIN='https://school.example'
  try:
   got=[];body=json.dumps(dict(username='ejteacher',password='Teacher-Test-12345')).encode();env={'REQUEST_METHOD':'POST','PATH_INFO':'/api/login','HTTP_HOST':'school.example','HTTP_ORIGIN':'https://school.example','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),'REMOTE_ADDR':'127.0.0.2'}
   wsgi.application(env,lambda status,headers:got.append((status,dict(headers))));self.assertTrue(got[0][0].startswith('200'));self.assertIn('Secure',got[0][1]['Set-Cookie']);self.assertIn('HttpOnly',got[0][1]['Set-Cookie']);self.assertIn('Strict-Transport-Security',got[0][1])
  finally:server.SECURE,server.ORIGIN=old,origin
if __name__=='__main__':unittest.main(verbosity=2)
