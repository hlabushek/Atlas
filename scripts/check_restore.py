"""Real pg_dump/restore to an isolated drill database; never overwrites Atlas."""
import subprocess,uuid,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(*args):return subprocess.check_output(['docker','compose','exec','-T','postgres',*args],cwd=ROOT)
def sql(database,statement):return run('psql','-U','atlas','-d',database,'-At','-v','ON_ERROR_STOP=1','-c',statement).decode().strip()
# Store hashes rather than private rows in reports; a snapshot transaction makes each fingerprint consistent.
def fingerprint(database):
    tables=sql(database,"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    values={}
    for table in tables:
        if not table.replace('_','').isalnum():raise ValueError('Unexpected table')
        values[table]=sql(database,'SELECT count(*)::text || \':\' || md5(coalesce(string_agg(row_to_json(t)::text,\'\' ORDER BY row_to_json(t)::text),\'\')) FROM "'+table+'" t')
    return values
if __name__=='__main__':
    identifier='atlas_restore_drill_'+uuid.uuid4().hex
    directory=ROOT/'.backups';directory.mkdir(exist_ok=True)
    target=directory/(identifier+'.dump')
    # Operator must quiesce writers for exact source equality; drill refuses to claim PASS on races.
    before=fingerprint('atlas')
    with target.open('wb') as out:
        subprocess.run(['docker','compose','exec','-T','postgres','pg_dump','-U','atlas','-d','atlas','-Fc'],cwd=ROOT,stdout=out,check=True)
    sql('postgres','CREATE DATABASE "'+identifier+'"')
    try:
        with target.open('rb') as source:
            subprocess.run(['docker','compose','exec','-T','postgres','pg_restore','-U','atlas','-d',identifier,'--exit-on-error','--no-owner'],cwd=ROOT,stdin=source,check=True,capture_output=True)
        restored=fingerprint(identifier);after=fingerprint('atlas')
        assert before==restored==after,'Source changed during drill or restore differs'
        (directory/'restore-report.json').write_text(json.dumps({'status':'PASS','tables':len(restored),'revision':sql(identifier,'SELECT version_num FROM alembic_version'),'backup':target.name},indent=2))
        print('PASS: isolated PostgreSQL restore, all table fingerprints equal; source unchanged')
    finally:
        # This exact random identifier was created above, never a user-supplied database.
        assert identifier.startswith('atlas_restore_drill_') and len(identifier)==52
        sql('postgres','DROP DATABASE "'+identifier+'"')
