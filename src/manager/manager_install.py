"""Journaled direct installation and rollback. Backups are data, not alternate game installations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def read(path,default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.new')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def below(root,relative):
    if not relative or '\\' in relative or ':' in relative or relative.startswith('/'):
        raise ValueError('安装清单路径无效。')
    p=(root/relative).resolve()
    if not p.is_relative_to(root.resolve()) or p==root.resolve():raise ValueError('安装路径越界。')
    return p


def replace_file(source,target,duplicate=None):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.totk-new-'+uuid.uuid4().hex)
    try:
        copy_file(source, temporary, duplicate)
        if sha(temporary)!=sha(source):raise OSError('写入后校验失败。')
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def copy_file(source, target, duplicate=None):
    if duplicate is not None:
        try:
            os.link(duplicate, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def copy_grouped(source, target, copies, copy):
    # Keep the official overlays' shared files together inside each copy, including an isolated backup.
    stat = source.stat()
    if stat.st_nlink <= 1 or not stat.st_ino:
        return copy(source, target)
    key = stat.st_dev, stat.st_ino
    if key in copies:
        return copy(source, target, copies[key])
    copy(source, target)
    copies[key] = target


def restore(root,home,backup,notify):
    journal=read(backup/'journal.json')
    if not journal or not isinstance(journal.get('plan'),list):raise ValueError('安装备份清单无效。')
    # Check every backup before touching the installed program.
    for item in journal['plan']:
        if item['before'] and sha(below(backup/'files',item['path']))!=item['before']:
            raise ValueError('备份校验失败，未开始还原。')
    copies = {}
    for item in reversed(journal['plan']):
        target=below(root,item['path'])
        if target.is_file() and sha(target) not in (item['before'],item['after']):
            # Preserve changes from other patch tools instead of discarding them on rollback.
            conflict=below(backup/'later-changes',item['path'])
            conflict.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,conflict)
        if item['before']:
            copy_grouped(below(backup/'files',item['path']), target, copies, replace_file)
        elif target.is_file():
            target.unlink()
    save(home/'active.json',journal['previous_state'])
    save(home/'rollback.json',journal['previous_rollback'])
    (home/'pending.json').unlink(missing_ok=True)
    notify('已还原更新前的游戏程序和补丁文件；存档与个人设置不回退。')
    return journal['previous_state']


def recover(root,home,notify):
    pending=read(home/'pending.json',{})
    if pending:
        notify('发现上次未完成的安装，正在从已校验的备份恢复。')
        restore(root,home,below(home,pending['backup']),notify)


def install(root,home,files,tracked,metadata,notify=lambda message:None,removals=()):
    root=root.resolve();home=home.resolve()
    previous=read(home/'active.json',{})
    old_files=previous.get('files',{}) if previous.get('mode')=='direct' else {}
    current={name:sha(source) for name,source in files.items()}
    plan=[]
    for name in sorted(set(files)|set(old_files)|set(removals)):
        # Configuration, saves and the user's RomFS index never become installation targets.
        if name=='totk.ini' or (name=='romfs-index.bin' and not metadata.get('local_index_regenerated')) or name.startswith(('profile/','save/','.totk-extensions/')):
            # A preceding official restoration owns a regenerated index. A normal
            # extension update leaves it in place instead of retiring or rejecting it.
            if name=='romfs-index.bin' and name not in files and name not in removals:
                continue
            raise ValueError('安装清单意外包含个人配置或资源索引。')
        target=below(root,name)
        if target.exists() and not target.is_file():raise ValueError('目标文件与已有目录冲突：'+name)
        before=sha(target) if target.is_file() else None
        after=current.get(name)
        if name not in files and name not in removals and before!=old_files.get(name):
            # A later mod's unrelated replacement is kept; it is no longer managed by this component.
            continue
        if before!=after:plan.append(dict(path=name,before=before,after=after))
    backup=home/'backups'/('install-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    copies = {}
    for item in plan:
        if item['before']:
            target=below(root,item['path']);saved=below(backup/'files',item['path'])
            saved.parent.mkdir(parents=True,exist_ok=True)
            copy_grouped(target, saved, copies, copy_file)
            if sha(saved)!=item['before']:raise OSError('安装前备份校验失败。')
    journal=dict(game_root=str(root),plan=plan,previous_state=previous,previous_rollback=read(home/'rollback.json',{}))
    save(backup/'journal.json',journal)
    save(home/'pending.json',dict(backup=backup.relative_to(home).as_posix()))
    try:
        copies = {}
        for item in plan:
            target=below(root,item['path'])
            if item['after']:copy_grouped(files[item['path']], target, copies, replace_file)
            elif target.is_file():
                target.unlink()
                parent=target.parent
                while parent!=root and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir();parent=parent.parent
        result=dict(metadata,mode='direct',files={name:current[name] for name in tracked},backup=backup.relative_to(home).as_posix())
        save(home/'active.json',result)
        save(home/'rollback.json',dict(backup=backup.relative_to(home).as_posix()))
        (home/'pending.json').unlink(missing_ok=True)
    except BaseException:
        notify('安装未完成，正在自动还原。')
        restore(root,home,backup,notify)
        raise
    notify('已直接更新所选游戏目录。备份：'+str(backup))
    return result


def rollback(root,home,notify=lambda message:None):
    checkpoint=read(home/'rollback.json',{})
    if not checkpoint:raise ValueError('没有可回退的直接安装记录。')
    return restore(root.resolve(),home.resolve(),below(home,checkpoint['backup']),notify)
