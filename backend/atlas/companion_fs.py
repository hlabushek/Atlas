"""Bounded filesystem operations: lock Windows ancestors and no-follow POSIX dirfds."""
import os,stat,re,hashlib,uuid
from contextlib import contextmanager
from pathlib import Path
from .desktop import DesktopInput

class WriteOutcomeUnknown(RuntimeError):pass

@contextmanager
def opened(root,relative):
    raw=Path(root).absolute()
    if not raw.is_dir() or raw!=raw.resolve():raise ValueError('Root contains links')
    parts=list(raw.parts[1:])+([x for x in relative.split('/') if x] if relative else [])
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];api.CreateFileW.restype=wintypes.HANDLE
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        api.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
        class Attributes(ctypes.Structure):_fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        handles=[];current=Path(raw.anchor)
        try:
            for index,part in enumerate([None]+parts):
                if part:current=current/part
                # No sharing of write/delete keeps parent directory names stable while reading.
                handle=api.CreateFileW(str(current),0x80000000,1,None,3,0x02000000|0x00200000,None)
                if handle==ctypes.c_void_p(-1).value:raise OSError('Cannot securely open path')
                handles.append(handle);attrs=Attributes()
                if not api.GetFileInformationByHandleEx(handle,9,ctypes.byref(attrs),ctypes.sizeof(attrs)) or attrs.attributes & 0x400:raise ValueError('Reparse point prohibited')
            import msvcrt
            # Keep byte counts and digests identical to the contents on disk on Windows.
            fd=msvcrt.open_osfhandle(int(handles.pop()),os.O_RDONLY|getattr(os,'O_BINARY',0))
            try:yield fd,current
            finally:os.close(fd)
        finally:
            for handle in reversed(handles):api.CloseHandle(handle)
    else:
        handles=[]
        try:
            fd=os.open(raw.anchor,os.O_RDONLY|os.O_DIRECTORY);handles.append(fd)
            for index,part in enumerate(parts):
                flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
                if index<len(parts)-1:flags|=os.O_DIRECTORY
                fd=os.open(part,flags,dir_fd=fd);handles.append(fd)
            yield fd,raw/relative
        finally:
            for handle in reversed(handles):os.close(handle)

def execute(config,args):
    from datetime import datetime,timezone
    a=DesktopInput.model_validate(args)
    expiry=datetime.fromisoformat(config['expires_at'])
    if not config.get('enabled') or expiry.tzinfo is None or expiry<=datetime.now(timezone.utc) or a.root not in config['roots'] or a.operation not in config['operations']:raise PermissionError('Local grant denied')
    if a.operation=='create_text':return create_text(config['roots'][a.root],a.path,a.content)
    with opened(config['roots'][a.root],a.path) as (fd,path):
        st=os.fstat(fd)
        if a.operation=='launch_file':
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:raise ValueError('Regular file required')
            return launch_file(config,a,path)
        if a.operation=='list':
            if not stat.S_ISDIR(st.st_mode):raise ValueError('Directory required')
            entries=[]
            # Windows handle locks path; POSIX scans the already opened directory descriptor.
            with os.scandir(path if os.name=='nt' else fd) as rows:
                for row in rows:
                    if row.name.startswith('.') or row.is_symlink():continue
                    info=row.stat(follow_symlinks=False)
                    if getattr(info,'st_file_attributes',0)&0x400:continue
                    entries.append({'name':row.name,'directory':stat.S_ISDIR(info.st_mode)})
                    if len(entries)>=100:break
            return {'entries':entries,'truncated':len(entries)==100}
        if not stat.S_ISREG(st.st_mode) or st.st_size>65536 or st.st_nlink!=1:raise ValueError('Small regular text file required')
        raw=os.read(fd,65537)
        if len(raw)>65536 or b'\x00' in raw:raise ValueError('Binary/large file prohibited')
        text=raw.decode('utf-8')
        if re.search(r'(?i)(sk-[a-z0-9_-]{12,}|(?:password|пароль|api[_ -]?key|token|токен)\s*[:=]\s*\S+|-----BEGIN .*PRIVATE KEY)',text):raise ValueError('Secret file prohibited')
        return {'text':text,'sha256':hashlib.sha256(raw).hexdigest()}

def launch_file(config,args,path):
    """Start one locally selected application with one checked document path; no shell."""
    if os.name!='nt':raise ValueError('File launch is supported only on Windows')
    applications=config.get('applications') or {}
    chosen=applications.get(args.app)
    if not isinstance(chosen,dict):raise PermissionError('Application alias denied')
    executable=Path(chosen.get('executable',''))
    extensions=chosen.get('extensions',[])
    if not executable.is_absolute() or executable.suffix.lower()!='.exe' or not executable.is_file() or executable.is_symlink():raise PermissionError('Configured application is invalid')
    if args.path.rsplit('.',1)[-1].lower() not in extensions:raise PermissionError('Extension denied for application')
    if executable.name.lower() in {'cmd.exe','powershell.exe','pwsh.exe','wscript.exe','cscript.exe','mshta.exe','rundll32.exe','regsvr32.exe'}:raise PermissionError('Command interpreter denied')
    import subprocess
    try:process=subprocess.Popen([str(executable),str(path)],shell=False,close_fds=True)
    except Exception as exc:raise WriteOutcomeUnknown('Application launch outcome unknown') from exc
    return {'launched':True,'app':args.app,'path':args.path,'pid':process.pid}

def create_text(root,relative,text):
    """Atomically create a new small text file; never replace an existing path."""
    raw=text.encode('utf-8')
    if len(raw)>65536 or b'\x00' in raw:raise ValueError('Invalid text content')
    parent_rel,_,leaf=relative.rpartition('/')
    if not leaf:raise ValueError('File name required')
    from contextlib import contextmanager
    @contextmanager
    def parent_open():
        base=Path(root).absolute()
        if not base.is_dir() or base!=base.resolve():raise ValueError('Root contains links')
        dirs=list(base.parts[1:])+([p for p in parent_rel.split('/') if p] if parent_rel else [])
        if os.name!='nt':
            handles=[]
            try:
                fd=os.open(base.anchor,os.O_RDONLY|os.O_DIRECTORY);handles.append(fd)
                for part in dirs:
                    fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);handles.append(fd)
                yield fd,None
            finally:
                for handle in reversed(handles):os.close(handle)
            return
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];api.CreateFileW.restype=wintypes.HANDLE
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        api.GetFileInformationByHandleEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
        class Attributes(ctypes.Structure):_fields_=[('attributes',wintypes.DWORD),('tag',wintypes.DWORD)]
        handles=[];current=Path(base.anchor)
        try:
            for part in [None]+dirs:
                if part:current=current/part
                # Keep every ancestor from rename/delete while allowing creation of a child.
                handle=api.CreateFileW(str(current),0x80000000,3,None,3,0x02000000|0x00200000,None)
                if handle==ctypes.c_void_p(-1).value:raise OSError('Cannot securely open parent directory')
                handles.append(handle);attrs=Attributes()
                if not api.GetFileInformationByHandleEx(handle,9,ctypes.byref(attrs),ctypes.sizeof(attrs)) or attrs.attributes&0x400:raise ValueError('Reparse point prohibited')
            yield None,base.joinpath(*([p for p in parent_rel.split('/') if p] if parent_rel else []))
        finally:
            for handle in reversed(handles):api.CloseHandle(handle)

    temp='.atlas-'+uuid.uuid4().hex+'.tmp'
    with parent_open() as (dirfd,parent_path):
        temp_path=(parent_path/temp) if parent_path else None
        target_path=(parent_path/leaf) if parent_path else None
        # Windows CRT otherwise translates LF to CRLF while writing through os.write.
        flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
        fd=os.open(temp_path if temp_path else temp,flags,0o600,**({'dir_fd':dirfd} if dirfd is not None else {}))
        try:
            view=memoryview(raw)
            while view:
                written=os.write(fd,view)
                if written<=0:raise OSError('Short write')
                view=view[written:]
            os.fsync(fd)
        except Exception:
            os.close(fd)
            try:
                if dirfd is not None:os.unlink(temp,dir_fd=dirfd)
                else:os.unlink(temp_path)
            except OSError:pass
            raise
        else:os.close(fd)
        linked=False
        try:
            if dirfd is not None:os.link(temp,leaf,src_dir_fd=dirfd,dst_dir_fd=dirfd,follow_symlinks=False)
            else:os.link(temp_path,target_path)
            linked=True
        except FileExistsError:raise ValueError('Existing files are never overwritten') from None
        except Exception:
            raise WriteOutcomeUnknown('Create result unknown') from None
        finally:
            try:
                if dirfd is not None:os.unlink(temp,dir_fd=dirfd)
                else:os.unlink(temp_path)
            except OSError:pass
        try:
            with opened(root,relative) as (created_fd,_):
                check=os.fstat(created_fd)
                if not stat.S_ISREG(check.st_mode) or check.st_nlink!=1 or check.st_size!=len(raw):raise ValueError('Created file verification failed')
                actual=hashlib.sha256(os.read(created_fd,65537)).hexdigest()
                if actual!=hashlib.sha256(raw).hexdigest():raise ValueError('Created file verification failed')
        except Exception:
            if linked:raise WriteOutcomeUnknown('Create result unknown') from None
            raise
    return {'created':True,'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
