# Isolated browser QA workspace. Never uses the live workspace database.
import tempfile,os
from pathlib import Path
import server as app
from test_erp import ERPTests
from webapp import create_app,create_http_server,close_http_server
fixture=ERPTests();fixture.setUp();pi,_,_=fixture.workflow();fixture.action('/api/pis/approve',{'id':pi['id']})
with app.database(True) as c:
 company=app.setting(c,'company');company['name']='DEMO - SATATA PACKAGING';c.execute('UPDATE settings SET value=? WHERE key=?',(app.dumps(company),'company'))
print('Isolated DEMO browser QA on http://127.0.0.1:8766',flush=True)
http=create_http_server(create_app(),port=8766)
try:http.run()
finally:close_http_server(http);fixture.tearDown()
