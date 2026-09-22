"""Cross-platform package acceptance; only executes the supplied harmless fixture."""
import argparse, json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.request

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',required=True)
    parser.add_argument('--fixture',required=True)
    options=parser.parse_args()
    suffix='.exe' if os.name=='nt' else ''
    reports=[]
    with tempfile.TemporaryDirectory(prefix='gs smoke ') as scratch:
        root=pathlib.Path(scratch)
        package=root/'original space'/'GameScheduler-Portable'
        shutil.copytree(options.package,package)
        external=root/'external helpers 游戏';external.mkdir()
        fixture=external/('fake helper'+suffix);shutil.copy2(options.fixture,fixture)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        config={'addr':f'127.0.0.1:{port}','data_dir':'${ROOT}/../Data','db_path':'${DATA}/scheduler.db','helpers_dir':'${ROOT}/../Helpers','runtime_dir':'${ROOT}/../Runtime','manifest_dirs':['${ROOT}/../Config/helpers','${DATA}/helpers'],'monitor_enabled':False,'max_concurrent':1}
        (package/'Config').mkdir(exist_ok=True)
        (package/'Config'/'config.json').write_text(json.dumps(config),encoding='utf-8')
        for name in ['Helpers','Data','Logs']:(package/name).mkdir(exist_ok=True)
        managed=package/'Helpers'/('managed helper'+suffix);shutil.copy2(fixture,managed)
        def api(path,method='GET',data=None):
            req=urllib.request.Request(url+path,method=method,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
        def health():
            try:return api('/healthz')
            except (OSError,ValueError):return None
        process=None;log=None
        def start(use_launcher=False):
            nonlocal process,log
            log=open(package/'Logs'/'smoke.log','ab')
            if use_launcher and os.name=='nt':
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'App'/'Portable.ps1'),'-Action','Start','-NoBrowser'],stdout=log,stderr=log,timeout=45)
                assert result.returncode==0,(package/'Logs'/'smoke.log').read_text(errors='replace')
                process=None
            else:
                process=subprocess.Popen([str(package/'App'/('server'+suffix)),'-config',str(package/'Config'/'config.json')],cwd=root,stdout=log,stderr=log)
            for _ in range(150):
                if health():return
                if process is not None and process.poll() is not None:raise AssertionError((package/'Logs'/'smoke.log').read_text())
                time.sleep(.1)
            raise AssertionError('health timeout')
        def stop():
            nonlocal process,log
            if health():
                if os.name=='nt':
                    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'App'/'Portable.ps1'),'-Action','Stop'],stdout=log,stderr=log,timeout=30)
                    assert result.returncode==0,(package/'Logs'/'smoke.log').read_text(errors='replace')
                else:api('/api/server/stop','POST',{})
            if process is not None:
                process.wait(timeout=20);assert process.returncode==0
            for _ in range(100):
                if not health():break
                time.sleep(.1)
            else:raise AssertionError('server did not stop')
            time.sleep(.3)
            if log:log.close();log=None
            process=None
        def check(condition,label):
            assert condition,label
            reports.append(label)
            print('PASS: '+label,file=sys.stderr,flush=True)
        def preflight(instance,kind='raw',params=None):return api('/api/helpers/'+instance+'/preflight','POST',{'type':kind,'params':{'raw_args':['space value','literal; echo NO','中文','${ROOT}','']} if params is None else params})
        def run_task(task):
            execution=api('/api/tasks/'+str(task['id'])+'/run','POST',{})
            for _ in range(100):
                row=api('/api/executions/'+str(execution['id']))
                if row['status'] in ['success','failed','cancelled']:break
                time.sleep(.1)
            check(row['status']=='success','harmless task success')
            return json.loads(row['stdout']),api('/api/executions/'+str(execution['id'])+'/diagnostics')
        try:
            start()
            check(health()['status']=='ok','package health')
            duplicate=subprocess.run([str(package/'App'/('server'+suffix)),'-config',str(package/'Config'/'config.json')],capture_output=True,text=True,timeout=10)
            check(duplicate.returncode!=0 and 'another server owns' in duplicate.stdout+duplicate.stderr,'duplicate server rejected before database reconciliation')
            if os.name=='nt':
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(package/'App'/'Portable.ps1'),'-Action','Start','-NoBrowser'],stdout=log,stderr=log,timeout=45)
                check(result.returncode==0,'launcher recognizes existing instance')
            for identity,path,mode in [('external',str(fixture),'external'),('managed','${HELPERS}/'+managed.name,'managed')]:
                api('/api/helpers','POST',{'id':identity,'helper_id':'ok-nte','name':identity,'location_mode':mode,'executable':path,'enabled':True})
                check(preflight(identity)['ready'],identity+' preflight')
            check(preflight('external')['executable']==str(fixture),'exact external path')
            stable_nte_args=['-t','DailyRoutineTask','-e','-h']
            check(preflight('external','task',{'task_index':2,'exit':True})['args']==stable_nte_args,'NTE legacy params normalize to named headless lifecycle')
            check(preflight('external','task',{'task_index':2,'exit':False})['args']==stable_nte_args,'NTE legacy exit flag cannot disable lifecycle exit')
            check(preflight('external','task',{'task_index':0})['args']==stable_nte_args,'NTE legacy task index cannot select the wrong task')
            api('/api/games','POST',{'id':'smoke','name':'Smoke','adapter':'ok-nte','enabled':True})
            tasks={}
            for identity in ['external','managed']:
                tasks[identity]=api('/api/tasks','POST',{'game_id':'smoke','name':identity,'type':'raw','params':json.dumps({'helper_instance_id':identity,'raw_args':['space value','literal; echo NO','中文','${ROOT}','']}),'enabled':True,'timeout_sec':5})
                output,diag=run_task(tasks[identity]);check(output['args']==['space value','literal; echo NO','中文','${ROOT}',''],'exact child argv '+identity)
                check(diag['executable']==preflight(identity)['executable'],'persisted diagnostics '+identity)
            check(len(api('/api/helpers'))==2,'two independent instances')
            item=api('/api/helpers/external');item['enabled']=False;api('/api/helpers/external','PUT',item)
            check(not preflight('external')['ready'] and preflight('managed')['ready'],'independent enabled states')
            item['enabled']=True;api('/api/helpers/external','PUT',item)
            manifest={'schema_version':1,'id':'future','display_name':'Future','task_types':{'raw':{'raw_args':True}}}
            (package/'Data'/'helpers').mkdir();(package/'Data'/'helpers'/'future.json').write_text(json.dumps(manifest),encoding='utf-8')
            check(any(d['id']=='future' for d in api('/api/helper-definitions/reload','POST',{})),'manifest reload without rebuild')
            api('/api/helper-settings/discovery','PUT',{'roots':[str(external)],'max_depth':4,'scan_all_local_drives':False})
            stop()
            moved=root/'relocated space 中文'/'GameScheduler-Portable';moved.parent.mkdir();shutil.move(str(package),str(moved));package=moved
            start(use_launcher=True)
            check(len(api('/api/helpers'))==2,'instance persistence after restart')
            check(api('/api/helper-settings/discovery')['roots']==[str(external)],'discovery persistence')
            check(preflight('external')['executable']==str(fixture),'external path unchanged after relocation')
            managed_pf=preflight('managed')
            expected_managed=package/'Helpers'/managed.name
            check(managed_pf['ready'] and os.path.samefile(managed_pf['executable'],expected_managed),'managed path relocated (same file in moved folder)')
            for task in tasks.values():run_task(task)
            api('/api/helpers/external','DELETE')
            check(preflight('managed')['ready'] and fixture.exists(),'deleting registration preserves other instance and external files')
        finally:
            stop()
    print(json.dumps({'status':'PASS','checks':reports},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
