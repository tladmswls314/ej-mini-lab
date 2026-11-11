"""Gunicorn WSGI adapter. No network server is started on import."""
import io,json,os,logging
from email.message import Message
from urllib.parse import urlsplit
import server

class Response(server.Handler):
 def send_response(self,code,message=None):
  self.status=code;self.output_headers=[]
 def send_header(self,name,value):self.output_headers.append((name,value))
 def end_headers(self):pass

def application(environ,start_response):
 h=object.__new__(Response);h.status=500;h.output_headers=[];h.wfile=io.BytesIO();h.rfile=environ['wsgi.input'];h.headers=Message()
 for k,v in environ.items():
  if k.startswith('HTTP_'):h.headers[k[5:].replace('_','-')]=str(v)
 h.headers['Content-Length']=environ.get('CONTENT_LENGTH','0') or '0';h.headers['Content-Type']=environ.get('CONTENT_TYPE','')
 h.path=environ.get('PATH_INFO','/')+('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')
 h.client_address=(environ.get('REMOTE_ADDR','unknown'),0)
 method=environ.get('REQUEST_METHOD','GET')
 try:
  if server.SECURE and (not server.ORIGIN.startswith('https://') or urlsplit(server.ORIGIN).netloc!=environ.get('HTTP_HOST')):
   h.send({'error':'운영 주소 설정을 확인하세요.'},503)
  elif method=='GET':h.do_GET()
  elif method=='POST':h.do_POST()
  else:h.send({'error':'지원하지 않는 요청입니다.'},405)
 except Exception:
  logging.exception('Unhandled request failure')
  h.wfile=io.BytesIO();h.send({'error':'잠시 후 다시 시도하세요.'},500)
 from http import HTTPStatus
 start_response(str(h.status)+' '+HTTPStatus(h.status).phrase,h.output_headers)
 return [h.wfile.getvalue()]
