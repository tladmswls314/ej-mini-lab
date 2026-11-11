import argparse,getpass,os,secrets,sqlite3
from pathlib import Path
import server
p=argparse.ArgumentParser();p.add_argument('command',choices=['init','create-teacher','reset-teacher','backup']);p.add_argument('--user',default='ejteacher');p.add_argument('--output');a=p.parse_args()
server.init()
if a.command=='init':
 password=os.environ.get('BOOTSTRAP_TEACHER_PASSWORD')
 with server.conn() as c:exists=c.execute("SELECT 1 FROM users WHERE role='teacher'").fetchone()
 if not exists:
  if not password:raise SystemExit('BOOTSTRAP_TEACHER_PASSWORD를 환경변수로 설정하세요. 기본 비밀번호는 없습니다.')
  server.create_teacher(os.environ.get('BOOTSTRAP_TEACHER_ID','ejteacher'),password)
 print('Database ready')
elif a.command=='create-teacher':
 password=getpass.getpass('새 교사 비밀번호 (12자 이상, 문자+숫자): ');server.create_teacher(a.user,password);print('교사 계정을 생성했습니다.')
elif a.command=='reset-teacher':
 password=getpass.getpass('새 비밀번호: ');server.check_password(password);salt=secrets.token_hex(16)
 with server.conn() as c:
  row=c.execute("SELECT id FROM users WHERE id=? AND role='teacher'",(a.user,)).fetchone()
  if not row:raise SystemExit('교사 계정이 없습니다.')
  c.execute('UPDATE users SET salt=?,pw=?,must_change=0,active=1 WHERE id=?',(salt,server.hp(password,salt),a.user));c.execute('DELETE FROM sessions WHERE uid=?',(a.user,))
 print('변경했습니다. 기존 로그인은 종료되었습니다.')
else:
 if not a.output:raise SystemExit('--output 백업파일 경로가 필요합니다.')
 dst=Path(a.output)
 if dst.exists():raise SystemExit('백업 파일이 이미 있습니다. 다른 이름을 사용하세요.')
 dst.parent.mkdir(parents=True,exist_ok=True)
 with server.conn() as src,sqlite3.connect(dst) as out:src.backup(out)
 os.chmod(dst,0o600);print('백업 완료. 개인정보가 포함되므로 접근을 제한하세요.')
