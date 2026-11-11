import re,io
import argparse,hashlib,hmac,json,os,secrets,sqlite3,time,random,unicodedata,mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse,parse_qs
from seed import seed,seeds,CAUSES,STRATEGIES
ROOT=Path(__file__).parent;DB=Path(os.environ.get('MINI_DB',ROOT/'fullcourse.sqlite3'))
SECURE=os.environ.get('MINI_SECURE','1')=='1'
ORIGIN=os.environ.get('APP_ORIGIN','').rstrip('/')
def conn():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA busy_timeout=20000");return c
def hp(p,s):return hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),600000).hex()
def norm(s):return ' '.join(unicodedata.normalize('NFKC',str(s)).casefold().split())
def init():
 DB.parent.mkdir(parents=True,exist_ok=True)
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,role TEXT,cohort INTEGER,cls TEXT,salt TEXT,pw TEXT,teacher TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,uid TEXT,csrf TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS lessons(day INTEGER PRIMARY KEY,body TEXT);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,uid TEXT,day INTEGER,kind TEXT,body TEXT,created REAL);
CREATE TABLE IF NOT EXISTS notes(uid TEXT,day INTEGER,qid TEXT,first TEXT,cause TEXT DEFAULT '',confirmed TEXT DEFAULT '',resolved INTEGER DEFAULT 0,PRIMARY KEY(uid,day,qid));
CREATE TABLE IF NOT EXISTS syntax(uid TEXT,day INTEGER,sid TEXT,answer TEXT,PRIMARY KEY(uid,day,sid));
CREATE TABLE IF NOT EXISTS ratings(uid TEXT,day INTEGER,rating TEXT,PRIMARY KEY(uid,day));
CREATE TABLE IF NOT EXISTS runs(token TEXT PRIMARY KEY,uid TEXT,day INTEGER,items TEXT,used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS limits(k TEXT PRIMARY KEY,n INTEGER,until REAL);''')
  columns={r[1] for r in c.execute('PRAGMA table_info(users)')}
  if 'active' not in columns:c.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
  if 'must_change' not in columns:c.execute('ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 1')
  c.execute('CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor TEXT, action TEXT, target TEXT, created REAL)')
  c.execute('CREATE INDEX IF NOT EXISTS attempts_user_day ON attempts(uid,day,kind)')
  c.execute('CREATE INDEX IF NOT EXISTS sessions_uid ON sessions(uid)')
  c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
  c.execute('DELETE FROM limits WHERE until<?',(time.time(),))
  for b in seeds():c.execute('INSERT OR IGNORE INTO lessons VALUES(?,?)',(b['day'],json.dumps(b,ensure_ascii=False)))
def user(u):return {k:u[k] for k in ['id','name','role','cohort','cls','active','must_change']}
def lesson(c,day,teacher=False):
 row=c.execute('SELECT body FROM lessons WHERE day=?',(day,)).fetchone()
 if not row:raise ValueError('아직 등록하지 않은 학습일입니다.')
 b=json.loads(row[0])
 if not teacher and (not b['approved'] or b['license'] not in ['original','permitted']):raise PermissionError('교사 검수와 이용 권한 확인 후 열립니다.')
 return b
def records(c,uid):
 a=[dict(r,body=json.loads(r['body'])) for r in c.execute('SELECT * FROM attempts WHERE uid=? ORDER BY id',(uid,))]
 return dict(attempts=a,notes=[dict(x) for x in c.execute('SELECT * FROM notes WHERE uid=?',(uid,))],syntax=[dict(x) for x in c.execute('SELECT * FROM syntax WHERE uid=?',(uid,))],ratings=[dict(x) for x in c.execute('SELECT * FROM ratings WHERE uid=?',(uid,))])
def validate(b):
 if type(b.get('day'))!=int or not 1<=b['day']<=10:raise ValueError('학습일은 1~10입니다.')
 if b.get('license') not in ['original','unknown','permitted']:raise ValueError('이용 권한 오류')
 for key in ['title','source','license_note']:
  if not isinstance(b.get(key),str) or not b[key].strip():raise ValueError(key+' 필수')
 for passage in b.get('passages',[]):
  if not all(passage.get(k) for k in ['id','text','title','source','question_ids']):raise ValueError('지문 출처·연결 문항·본문 필요')
  if not set(passage['question_ids']).issubset({q['id'] for q in b.get('questions',[])}):raise ValueError('지문 연결 문항 오류')
 for kind in ['vocab','syntax','questions']:
  if not isinstance(b.get(kind),list) or not b[kind]:raise ValueError(kind+' 항목 필요')
  ids=[x['id'] for x in b[kind]]
  if len(ids)!=len(set(ids)):raise ValueError('중복 ID')
 for v in b['vocab']:
  if v['mode'] not in ['en-ko','ko-en','mc'] or not v['word'] or not v['meaning'] or not isinstance(v['aliases'],list):raise ValueError('어휘 형식 오류')
  if not 0<=v['answer']<len(v['options']):raise ValueError('어휘 정답 오류')
 for s in b['syntax']:
  for key in ['sentence','analysis','translation','grammar']:
   if not s.get(key):raise ValueError('구문 필수항목 '+key)
 for q in b['questions']:
  if len(q['options'])!=5 or len(q['distractors'])!=5 or type(q['answer'])!=int or not 0<=q['answer']<5:raise ValueError('선지·해설은 5개, 정답은 0~4')
  for key in ['type','level','passage','prompt','evidence','explanation']:
   if not q.get(key) and not (key=='passage' and q.get('origin')=='paper'):raise ValueError('문항 필수항목 '+key)
def check_password(p):
 if not 12<=len(p)<=128 or not any(c.isalpha() for c in p) or not any(c.isdigit() for c in p):raise ValueError('비밀번호는 문자와 숫자를 포함한 12~128자로 정하세요.')
def create_teacher(uid,password):
 check_password(password)
 if not re.fullmatch(r'[a-z][a-z0-9_-]{2,31}',uid):raise ValueError('교사 아이디 형식 오류')
 with conn() as c:
  if c.execute("SELECT 1 FROM users WHERE role='teacher'").fetchone():raise ValueError('교사 계정이 이미 있습니다. 복구 명령을 사용하세요.')
  salt=secrets.token_hex(16);c.execute("INSERT INTO users(id,name,role,cohort,cls,salt,pw,teacher,must_change) VALUES(?,?,'teacher',0,'',?,?,?,0)",(uid,'EJ쌤',salt,hp(password,salt),uid))
def grade_question(c,uid,day,q,a):
 out=dict(qid=q['id'],choice=a,correct=a==q['answer'],question=q)
 c.execute('INSERT INTO attempts(uid,day,kind,body,created) VALUES(?,?,?,?,?)',(uid,day,'question',json.dumps(out,ensure_ascii=False),time.time()))
 if not out['correct']:
  first=next(json.loads(r[0])['choice'] for r in c.execute("SELECT body FROM attempts WHERE uid=? AND day=? AND kind='question' ORDER BY id",(uid,day)) if json.loads(r[0])['qid']==q['id'])
  c.execute('INSERT OR IGNORE INTO notes(uid,day,qid,first) VALUES(?,?,?,?)',(uid,day,q['id'],str(first)))
 c.execute('UPDATE notes SET resolved=? WHERE uid=? AND day=? AND qid=?',(int(out['correct']),uid,day,q['id']))
 return out
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def cookie(self,token,age=28800):
  return f"mini_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}"+('; Secure' if SECURE else '')
 def write_audit(self,c,u,action,target):
  c.execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(u['id'],action,target,time.time()))
 def limited(self,c,key,maximum=10):
  r=c.execute('SELECT n,until FROM limits WHERE k=?',(key,)).fetchone()
  return bool(r and r['n']>=maximum and r['until']>time.time())
 def bump(self,c,key):
  c.execute('INSERT INTO limits VALUES(?,1,?) ON CONFLICT(k) DO UPDATE SET n=CASE WHEN until>? THEN n+1 ELSE 1 END, until=CASE WHEN until>? THEN until ELSE excluded.until END',(key,time.time()+300,time.time(),time.time()))

 def send(self,data,status=200,cookie=None):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.common()
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(raw)
 def common(self):
  self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Robots-Tag','noindex, nofollow');self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
  if SECURE:self.send_header('Strict-Transport-Security','max-age=31536000')
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
 def session(self,c):
  ck=SimpleCookie();ck.load(self.headers.get('Cookie',''));t=ck.get('mini_session');return c.execute('SELECT u.*,s.csrf,s.token FROM sessions s JOIN users u ON u.id=s.uid WHERE token=? AND expires>? AND u.active=1',(t.value if t else '',time.time())).fetchone()
 def do_GET(self):
  p=urlparse(self.path);path=p.path
  if path=='/healthz':
   with conn() as c:c.execute('SELECT 1')
   return self.send({'ok':True})
  if not path.startswith('/api/'):
   rel='index.html' if path=='/' else 'mobile.html' if path=='/mobile-preview' else path.lstrip('/');f=(ROOT/'static'/rel).resolve();base=(ROOT/'static').resolve()
   if not f.is_relative_to(base) or not f.is_file():return self.send({'error':'없음'},404)
   self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(f))[0] or 'application/octet-stream');self.common();self.end_headers();self.wfile.write(f.read_bytes());return
  try:
   with conn() as c:
    u=self.session(c)
    if not u:return self.send({'error':'로그인하세요.'},401)
    if path=='/api/state':
     days=[]
     for r in c.execute('SELECT body FROM lessons ORDER BY day'):
      b=json.loads(r[0]);days.append({k:b[k] for k in ['day','title','approved','license']})
     out=dict(user=user(u),csrf=u['csrf'],days=days,records=records(c,u['id']),causes=CAUSES,strategies=STRATEGIES)
     if u['must_change']:return self.send(dict(user=user(u),csrf=u['csrf'],days=[],records=records(c,u['id']),causes=[],strategies=[]))
     if u['role']=='teacher':out.update(students=[dict(user=user(s),records=records(c,s['id'])) for s in c.execute("SELECT * FROM users WHERE role='student' AND teacher=?",(u['id'],))],content=[json.loads(r[0]) for r in c.execute('SELECT body FROM lessons ORDER BY day')])
     return self.send(out)
    if u['must_change']:raise PermissionError('먼저 비밀번호를 변경하세요.')
    if path=='/api/lesson':
     b=lesson(c,int(parse_qs(p.query).get('day',['1'])[0]),u['role']=='teacher')
     if u['role']!='teacher':
      b['questions']=[{k:v for k,v in q.items() if k not in ['answer','evidence','explanation','distractors']} for q in b['questions']]
      b['syntax']=[{k:v for k,v in s.items() if k in ['id','sentence','source']} for s in b['syntax']]
      b.pop('vocabulary_all',None)
      b['vocab']=[{k:v for k,v in w.items() if k not in ['answer','aliases']} for w in b['vocab']]
      b['passages']=[{k:v for k,v in p.items() if k in ['id','title','question_ids','source']} for p in b.get('passages',[])]
     return self.send(b)
    return self.send({'error':'접근할 수 없는 경로'},404)
  except PermissionError as e:self.send({'error':str(e)},403)
  except (ValueError,KeyError) as e:self.send({'error':str(e)},400)
 def do_POST(self):
  try:
   n=int(self.headers.get('Content-Length',0))
   if not 0<n<=1000000:raise ValueError('요청 크기 오류')
   if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON 요청만 허용합니다.')
   origin=self.headers.get('Origin')
   if SECURE and (not ORIGIN or origin!=ORIGIN):raise PermissionError('접속 주소를 확인하세요.')
   if not SECURE and origin and origin!='http://'+self.headers.get('Host',''):raise PermissionError('접속 주소를 확인하세요.')
   d=json.loads(self.rfile.read(n));path=urlparse(self.path).path
   if not isinstance(d,dict):raise ValueError('입력 형식 오류')
   with conn() as c:
    if path=='/api/login':
     uid=str(d.get('username',''))[:40];k='account:'+uid;ip='ip:'+self.client_address[0]
     if self.limited(c,k) or self.limited(c,ip,60):return self.send({'error':'5분 후 다시 시도하세요.'},429)
     u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
     password=str(d.get('password',''))[:256]
     check=hp(password,u['salt'] if u else '0'*32)
     if not u or not u['active'] or not hmac.compare_digest(u['pw'],check):
      self.bump(c,k);self.bump(c,ip);c.commit();return self.send({'error':'아이디·비밀번호를 확인하세요.'},401)
     t=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(t,uid,csrf,time.time()+28800));c.execute('DELETE FROM limits WHERE k=?',(k,));c.commit();return self.send({'csrf':csrf},cookie=self.cookie(t))
    u=self.session(c)
    if not u:return self.send({'error':'로그인 필요'},401)
    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),u['csrf']):raise PermissionError('요청 인증 오류')
    if path=='/api/logout':c.execute('DELETE FROM sessions WHERE token=?',(u['token'],));c.commit();return self.send({'ok':True},cookie=self.cookie('',0))
    if path=='/api/password':
     if self.limited(c,'password:'+u['id']):return self.send({'error':'5분 후 다시 시도하세요.'},429)
     if not hmac.compare_digest(u['pw'],hp(str(d.get('current',''))[:256],u['salt'])):
      self.bump(c,'password:'+u['id']);c.commit();return self.send({'error':'현재 비밀번호가 맞지 않습니다.'},400)
     new=str(d.get('new',''));check_password(new)
     if hmac.compare_digest(new,str(d.get('current',''))):raise ValueError('다른 비밀번호로 변경하세요.')
     salt=secrets.token_hex(16);c.execute('UPDATE users SET pw=?,salt=?,must_change=0 WHERE id=?',(hp(new,salt),salt,u['id']))
     c.execute('DELETE FROM sessions WHERE uid=?',(u['id'],));self.write_audit(c,u,'password_change',u['id']);c.commit()
     return self.send({'ok':True},cookie=self.cookie('',0))
    if u['must_change']:raise PermissionError('먼저 비밀번호를 변경하세요.')
    if path.startswith('/api/teacher/'):
     if u['role']!='teacher':raise PermissionError('교사 권한 필요')
     if path=='/api/teacher/create':
      uid=str(d.get('uid',''));name=str(d.get('name','')).strip()
      if not re.fullmatch(r'[a-z][a-z0-9_-]{2,31}',uid):raise ValueError('아이디: 영문 소문자로 시작하는 3~32자 영문·숫자·_·-')
      if not 1<=len(name)<=30:raise ValueError('별칭은 1~30자입니다.')
      if d.get('cohort') not in [1,2] or d.get('cls') not in ['A','B']:raise ValueError('회차·반 오류')
      if c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():raise ValueError('이미 사용 중인 아이디입니다.')
      pw=secrets.token_urlsafe(12);salt=secrets.token_hex(16)
      c.execute('INSERT INTO users(id,name,role,cohort,cls,salt,pw,teacher) VALUES(?,?,?,?,?,?,?,?)',(uid,name,'student',d['cohort'],d['cls'],salt,hp(pw,salt),u['id']))
      self.write_audit(c,u,'student_create',uid);c.commit();return self.send({'uid':uid,'temporary_password':pw})
     elif path in ['/api/teacher/reset','/api/teacher/active','/api/teacher/delete']:
      uid=d.get('uid');student=c.execute("SELECT * FROM users WHERE id=? AND role='student' AND teacher=?",(uid,u['id'])).fetchone()
      if not student:raise PermissionError('담당 학생만 관리 가능합니다.')
      if path.endswith('/reset'):
       pw=secrets.token_urlsafe(12);salt=secrets.token_hex(16);c.execute('UPDATE users SET salt=?,pw=?,must_change=1 WHERE id=?',(salt,hp(pw,salt),uid))
       c.execute('DELETE FROM sessions WHERE uid=?',(uid,));self.write_audit(c,u,'password_reset',uid);c.commit();return self.send({'uid':uid,'temporary_password':pw})
      if path.endswith('/active'):
       if type(d.get('active'))!=bool:raise ValueError('활성 상태 오류')
       c.execute('UPDATE users SET active=? WHERE id=?',(int(d['active']),uid));c.execute('DELETE FROM sessions WHERE uid=?',(uid,));self.write_audit(c,u,'student_active',uid)
      else:
       if student['active']:raise ValueError('계정을 먼저 중지하세요.')
       if d.get('confirm')!=uid:raise ValueError('삭제할 학생 아이디를 정확히 입력하세요.')
       for table in ['sessions','attempts','notes','syntax','ratings','runs']:c.execute('DELETE FROM '+table+' WHERE uid=?',(uid,))
       c.execute('DELETE FROM users WHERE id=?',(uid,));self.write_audit(c,u,'student_delete',uid)
     elif path=='/api/teacher/export':
      students=[dict(user=user(s),records=records(c,s['id'])) for s in c.execute("SELECT * FROM users WHERE role='student' AND teacher=?",(u['id'],))]
      self.write_audit(c,u,'records_export','all');c.commit();return self.send({'exported_at':time.time(),'students':students})
     elif path=='/api/teacher/content':
      b=d['content'];validate(b)
      if c.execute('SELECT 1 FROM attempts WHERE day=?',(b['day'],)).fetchone():raise ValueError('학습 기록이 있는 회차는 수정할 수 없습니다. 다음 학습일로 복사하세요.')
      b['approved']=False;c.execute('INSERT OR REPLACE INTO lessons VALUES(?,?)',(b['day'],json.dumps(b,ensure_ascii=False)))
     elif path=='/api/teacher/approve':
      b=lesson(c,d['day'],True);validate(b)
      if d.get('approved') and b['license']=='unknown':raise ValueError('이용 권한 미확인 콘텐츠는 승인할 수 없습니다.')
      b['approved']=bool(d.get('approved'));b['reviewer']=u['id'];b['reviewed']=time.time();c.execute('UPDATE lessons SET body=? WHERE day=?',(json.dumps(b,ensure_ascii=False),b['day']))
     elif path=='/api/teacher/student':
      s=c.execute('SELECT * FROM users WHERE id=? AND teacher=?',(d['uid'],u['id'])).fetchone()
      if not s or s['role']!='student':raise PermissionError('담당 학생만 관리 가능')
      if d['cohort'] not in [1,2] or d['cls'] not in ['A','B']:raise ValueError('반 오류')
      if c.execute('SELECT 1 FROM attempts WHERE uid=?',(d['uid'],)).fetchone():raise ValueError('학습 기록이 있는 학생의 반은 변경할 수 없습니다.')
      c.execute('UPDATE users SET cohort=?,cls=? WHERE id=?',(d['cohort'],d['cls'],d['uid']))
     elif path=='/api/teacher/confirm':
      if not c.execute('SELECT 1 FROM users WHERE id=? AND teacher=?',(d['uid'],u['id'])).fetchone():raise PermissionError('담당 학생 아님')
      if d['cause'] not in CAUSES:raise ValueError('원인 오류')
      c.execute('UPDATE notes SET confirmed=? WHERE uid=? AND day=? AND qid=?',(d['cause'],d['uid'],d['day'],d['qid']))
     else:return self.send({'error':'없음'},404)
     c.commit();return self.send({'ok':True})
    if u['role']!='student':raise PermissionError('학생 계정으로 진행하세요.')
    c.execute('BEGIN IMMEDIATE')
    day=d.get('day',1);b=lesson(c,day);uid=u['id'];out={'ok':True}
    if path=='/api/passage':
     passage=next(x for x in b.get('passages',[]) if x['id']==d.get('passage_id'))
     answered={json.loads(r[0])['qid'] for r in c.execute("SELECT body FROM attempts WHERE uid=? AND day=? AND kind='question'",(uid,day))}
     if not set(passage['question_ids']).issubset(answered):raise PermissionError('해당 교재 문항의 답안을 먼저 제출하세요. 정답이 반영된 복습용 지문입니다.')
     out=passage
    elif path=='/api/vocab/start':
     items=b['vocab']
     if d.get('retry'):
      prev=c.execute("SELECT body FROM attempts WHERE uid=? AND day=? AND kind='vocab' ORDER BY id DESC LIMIT 1",(uid,day)).fetchone()
      if not prev:raise ValueError('첫 시험을 먼저 보세요.')
      wrong={r['id'] for r in json.loads(prev[0])['rows'] if not r['correct']};items=[v for v in items if v['id'] in wrong]
      if not items:raise ValueError('재시험할 오답이 없습니다.')
     items=items[:];random.SystemRandom().shuffle(items);token=secrets.token_urlsafe(24);c.execute('INSERT INTO runs VALUES(?,?,?,?,0)',(token,uid,day,json.dumps(items,ensure_ascii=False)));out=dict(token=token,items=[dict(id=v['id'],mode=v['mode'],prompt=v['meaning'] if v['mode']=='ko-en' else v['word'],options=v['options'] if v['mode']=='mc' else []) for v in items])
    elif path=='/api/vocab/submit':
     run=c.execute('SELECT * FROM runs WHERE token=? AND uid=? AND day=?',(d['token'],uid,day)).fetchone()
     if not run or run['used']:raise ValueError('시험이 없거나 이미 제출했습니다.')
     rows=[]
     for v in json.loads(run['items']):
      ans=d['answers'].get(v['id'],'');accepted=[v['word']] if v['mode']=='ko-en' else [v['meaning']]+v['aliases'];ok=(type(ans)==int and ans==v['answer']) if v['mode']=='mc' else norm(ans) in [norm(x) for x in accepted]
      rows.append(dict(id=v['id'],word=v['word'],meaning=v['meaning'],input=ans,correct=ok,accepted=accepted))
     out=dict(rows=rows,correct=sum(x['correct'] for x in rows),total=len(rows));c.execute('INSERT INTO attempts(uid,day,kind,body,created) VALUES(?,?,?,?,?)',(uid,day,'vocab',json.dumps(out,ensure_ascii=False),time.time()));c.execute('UPDATE runs SET used=1 WHERE token=?',(d['token'],))
    elif path=='/api/syntax':
     s=next(x for x in b['syntax'] if x['id']==d['sid']);answer=str(d.get('answer','')).strip()
     if not answer:raise ValueError('먼저 구조나 해석을 적어 보세요.')
     c.execute('INSERT OR REPLACE INTO syntax VALUES(?,?,?,?)',(uid,day,s['id'],answer[:3000]));out=s
    elif path=='/api/paper':
     qs=[q for q in b['questions'] if q.get('origin')=='paper'];answers=d.get('answers',{})
     if not isinstance(answers,dict) or set(answers)!=set(q['id'] for q in qs) or any(type(a)!=int or not 0<=a<5 for a in answers.values()):raise ValueError('교재 답안 10개를 모두 선택하세요.')
     results=[grade_question(c,uid,day,q,answers[q['id']]) for q in qs];out={'results':results,'correct':sum(x['correct'] for x in results),'total':len(results)}
    elif path=='/api/question':
     q=next(x for x in b['questions'] if x['id']==d['qid']);a=d['choice']
     if type(a)!=int or not 0<=a<5:raise ValueError('선지를 선택하세요.')
     out=grade_question(c,uid,day,q,a)
    elif path=='/api/cause':
     if d['cause'] not in CAUSES:raise ValueError('원인 오류')
     c.execute("UPDATE notes SET cause=?,confirmed='' WHERE uid=? AND day=? AND qid=?",(d['cause'],uid,day,d['qid']))
    elif path=='/api/rating':
     if d['rating'] not in ['혼자서도 해결할 수 있다','해설을 보면 이해할 수 있다','추가 설명이 필요하다']:raise ValueError('평가 오류')
     c.execute('INSERT OR REPLACE INTO ratings VALUES(?,?,?)',(uid,day,d['rating']))
    else:return self.send({'error':'없음'},404)
    c.commit();self.send(out)
  except PermissionError as e:self.send({'error':str(e)},403)
  except (ValueError,KeyError,StopIteration,TypeError) as e:self.send({'error':str(e) or '입력 형식을 확인하세요.'},400)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8000);p.add_argument('--strictPort',action='store_true');a=p.parse_args();init();print(f'MINI LAB http://{a.host}:{a.port}',flush=True);ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()
