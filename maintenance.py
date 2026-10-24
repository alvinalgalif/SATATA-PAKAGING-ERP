"""Operator-only encrypted SQLite backup, verification and non-overwriting restore."""
import argparse,hashlib,json,os,secrets,sqlite3,tempfile
from datetime import datetime,timezone
from pathlib import Path
from contextlib import closing
import server as core
from cryptography.fernet import Fernet,InvalidToken
from security import local_key

MAX_BACKUP_BYTES=128*1024*1024
def backup_key():
 value=os.getenv('ERP_BACKUP_KEY','')
 if value:return value.encode()
 if os.getenv('ERP_ENV','local')=='production':raise ValueError('Production backup requires ERP_BACKUP_KEY from the secret store')
 return local_key('backup.key')
def checked_sqlite(path):
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
  if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
  if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Backup foreign-key check failed')
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if not {'users','pis','lcs','batches','audit'}<=tables:raise ValueError('Not a recognized ERP database')
  return {table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['users','pis','lcs','batches']}
def write_private(path,raw):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 except Exception:
  path.unlink(missing_ok=True);raise

def make_backup(output_dir,key=None):
 if not core.DB.is_file():raise ValueError('ERP database does not exist')
 output_dir=Path(output_dir).resolve();output_dir.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix='.erp-backup-',suffix='.sqlite3',dir=output_dir);os.close(fd);plain=Path(name)
 try:
  with closing(sqlite3.connect(core.DB.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(plain)) as target:source.backup(target)
  counts=checked_sqlite(plain)
  if plain.stat().st_size>MAX_BACKUP_BYTES:raise ValueError('Database exceeds this helper limit; configure a streaming/provider-managed backup')
  cipher=Fernet(key or backup_key());encrypted=cipher.encrypt(plain.read_bytes());target=output_dir/('erp-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(4)+'.sqlite3.fernet');write_private(target,encrypted)
  return target,counts
 finally:plain.unlink(missing_ok=True)

def inspect_backup(archive,key=None):
 raw=Path(archive).read_bytes()
 if len(raw)>MAX_BACKUP_BYTES*2:raise ValueError('Encrypted backup exceeds the supported size')
 try:plain=Fernet(key or backup_key()).decrypt(raw)
 except InvalidToken:raise ValueError('Backup authentication failed: wrong key or damaged archive')
 return plain

def restore_backup(archive,destination,key=None):
 destination=Path(destination).resolve()
 if destination.exists():raise ValueError('Restore destination must not already exist; live data is never overwritten')
 destination.parent.mkdir(parents=True,exist_ok=True);plain=inspect_backup(archive,key);write_private(destination,plain)
 try:
  counts=checked_sqlite(destination)
  # Recovered databases must require a new sign-in on every device.
  with closing(sqlite3.connect(destination)) as c:
   c.execute('DELETE FROM sessions')
   if 'mfa_pending' in {row[1] for row in c.execute('PRAGMA table_info(users)')}:c.execute("UPDATE users SET mfa_pending='',mfa_pending_until=0,mfa_recovery='[]'")
   c.commit()
  return counts
 except Exception:destination.unlink(missing_ok=True);raise

def verify_backup(archive,key=None):
 with tempfile.TemporaryDirectory() as folder:return restore_backup(archive,Path(folder)/'verified.sqlite3',key)

def generate_secrets(folder):
 folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
 values={'setup.token':secrets.token_urlsafe(48).encode(),'mfa.key':Fernet.generate_key(),'backup.key':Fernet.generate_key()}
 if any((folder/name).exists() for name in values):raise ValueError('Secret files already exist; refusing to replace keys')
 for name,value in values.items():write_private(folder/name,value)
 return [str(folder/name) for name in values]

def main():
 parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--backup',metavar='DIRECTORY');group.add_argument('--verify',metavar='ARCHIVE');group.add_argument('--restore',metavar='ARCHIVE');group.add_argument('--generate-secrets',metavar='DIRECTORY');parser.add_argument('--destination');args=parser.parse_args()
 if args.generate_secrets:print('Secret files created (values not printed):\n'+'\n'.join(generate_secrets(args.generate_secrets)))
 elif args.backup:
  path,counts=make_backup(args.backup);print('Encrypted backup saved:',path);print('Verified record counts:',json.dumps(counts))
 elif args.verify:print('Backup verified:',json.dumps(verify_backup(args.verify)))
 elif args.restore:
  if not args.destination:parser.error('--restore requires a new --destination path')
  print('Restored to new database:',json.dumps(restore_backup(args.restore,args.destination)))
if __name__=='__main__':main()
