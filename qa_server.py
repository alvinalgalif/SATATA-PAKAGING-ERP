# Isolated browser QA workspace. Never uses the live workspace database.
import tempfile,os
from pathlib import Path
import server as app
from test_erp import ERPTests
from http.server import ThreadingHTTPServer
fixture=ERPTests();fixture.setUp();pi,_,_=fixture.workflow();fixture.action('/api/pis/approve',{'id':pi['id']})
with app.database(True) as c:
 company=app.setting(c,'company');company['name']='DEMO - SATATA PACKAGING';c.execute('UPDATE settings SET value=? WHERE key=?',(app.dumps(company),'company'))
print('Isolated DEMO browser QA on http://127.0.0.1:8766',flush=True)
try:ThreadingHTTPServer(('127.0.0.1',8766),app.Handler).serve_forever()
finally:fixture.tearDown()
