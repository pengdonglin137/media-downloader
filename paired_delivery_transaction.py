"""四个交付路径的可恢复文件事务；不提供生产包验证器或直接安装 CLI。"""

import fcntl
import json
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import dubbing_state as state


FILE_KEYS = ('master', 'upload', 'render_state', 'upload_manifest')
IDENTITY_KEYS = {'path', 'bytes', 'mtime_ns', 'sha256'}
TERMINAL_STATES = {'committed', 'automatic_rollback', 'rolled_back', 'recovered_originals', 'failed_before_install'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(), '路径必须绝对且不能经过符号链接')
    return path


def rebase(observation, path):
    return {**observation, 'path': str(path)}


def checked(observation):
    require(isinstance(observation, dict) and set(observation) == IDENTITY_KEYS, '文件身份字段不完整')
    path = canonical(observation['path'])
    require(path.is_file() and not path.is_symlink() and state.matches(observation), '文件身份已变化：' + str(path))
    return path


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def snapshot(observation, destination):
    source = checked(observation)
    require(not destination.exists() and not destination.is_symlink(), '不能覆盖事务快照')
    shutil.copy2(source, destination)
    with destination.open('rb') as stream:
        os.fsync(stream.fileno())
    checked(observation)
    expected = rebase(observation, destination)
    checked(expected)
    sync_directory(destination.parent)
    return expected


@contextmanager
def project_lock(work):
    lock_path = work / '.workflow.lock'
    require(not lock_path.is_symlink(), '工程锁不能是符号链接')
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('工程正被其他流程占用') from error
        yield


def destinations(work, originals):
    require(set(originals) == set(FILE_KEYS), '事务必须恰好覆盖四个交付路径')
    paths = {key: canonical(originals[key]['path']) for key in FILE_KEYS}
    master, upload = paths['master'], paths['upload']
    require(master.parent == upload.parent == work.parent
            and master.name.endswith('_中文配音.mp4') and upload.name.endswith('_中文上传版.mp4')
            and master.name.removesuffix('_中文配音.mp4') == upload.name.removesuffix('_中文上传版.mp4')
            and paths['render_state'] == work / 'render_state.json'
            and paths['upload_manifest'] == work / 'upload_export.json'
            and len(set(paths.values())) == len(FILE_KEYS), '交付路径不属于当前工程双版本')
    return paths


def transaction_path(work, path):
    work, path = canonical(work), canonical(path)
    require(path.name == 'transaction.json' and path.parent.parent == work / 'paired_revisions'
            and re.fullmatch('[0-9a-f]{32}', path.parent.name), '事务路径或工程归属无效')
    return path


def pending_transactions(work, excluding=None):
    root = canonical(work / 'paired_revisions')
    for path in sorted(root.glob('*/transaction.json')):
        transaction_path(work, path)
        require(not path.is_symlink(), '事务日志不能是符号链接')
        journal = state.load(path)
        require(journal.get('version') == 4 and journal.get('state') in TERMINAL_STATES | {
            'preparing', 'prepared', 'installing', 'rolling_back', 'recovery_required'}, '未知事务版本或状态')
        if path != excluding:
            require(journal['state'] in TERMINAL_STATES, '先恢复未完成双版本事务：' + str(path))


class ArchiveRouter:
    def __init__(self, work, directory, originals, archived):
        work, directory = canonical(work), canonical(directory)
        transaction_path(work, directory / 'transaction.json')
        destinations(work, originals)
        require(set(archived) == set(originals) == set(FILE_KEYS), '归档路由只能包含原四个路径')
        self.originals = originals
        self.archived = archived
        for key in FILE_KEYS:
            expected = rebase(originals[key], directory / 'archive' / key)
            require(archived[key] == expected, '归档必须保持原身份且位于本事务：' + key)
            checked(expected)

    def resolve(self, observation):
        for key in FILE_KEYS:
            if observation == self.originals[key]:
                checked(self.archived[key])
                return self.archived[key]
        checked(observation)
        return observation

    def verify_references(self, value):
        if isinstance(value, dict):
            if IDENTITY_KEYS.issubset(value):
                self.resolve(value)
            else:
                for child in value.values():
                    self.verify_references(child)
        elif isinstance(value, list):
            for child in value:
                self.verify_references(child)


def load_transaction(work, path):
    path = transaction_path(work, path)
    checked(state.identity(path))
    journal = state.load(path)
    require(journal.get('version') == 4 and journal.get('state') in TERMINAL_STATES | {
        'prepared', 'installing', 'rolling_back', 'recovery_required'}, '事务状态不可读取')
    reference = journal['prepared']
    require(reference['path'] == str(path.parent / 'prepared.json'), '事务准备清单路径不符')
    checked(reference)
    prepared = state.load(reference['path'])
    require(prepared['version'] == 4 and prepared['work'] == str(work)
            and prepared['kind'] == 'paired-delivery-file-transaction'
            and prepared['implementation'] == state.identity(__file__), '事务实现或工程身份变化')
    paths = destinations(work, prepared['original'])
    ArchiveRouter(work, path.parent, prepared['original'], prepared['archived'])
    require(set(prepared['sources']) == set(prepared['staged']) == set(prepared['desired']) == set(FILE_KEYS),
            '新文件集合不完整')
    for key in FILE_KEYS:
        require(prepared['staged'][key] == rebase(prepared['sources'][key], path.parent / 'staged' / key)
                and prepared['desired'][key] == rebase(prepared['sources'][key], paths[key]), '暂存或目标身份无效')
        checked(prepared['sources'][key])
        checked(prepared['staged'][key])
    for reference in prepared['evidence'].values():
        checked(reference)
    return prepared, journal


def current_versions(prepared):
    versions = {}
    for key in FILE_KEYS:
        actual = state.identity(checked_path(prepared['original'][key]['path']))
        require(actual in (prepared['original'][key], prepared['desired'][key]), '拒绝覆盖事务外修改：' + key)
        versions[key] = 'old' if actual == prepared['original'][key] else 'new'
    return versions


def checked_path(path):
    path = canonical(path)
    require(path.is_file() and not path.is_symlink(), '交付文件缺失或成为符号链接')
    return path


def pending_path(prepared, directory, key, direction):
    destination = Path(prepared['original'][key]['path'])
    return destination.with_name(f'.pair-v4-{directory.name}-{key}-{direction}.pending')


def replace_from(prepared, directory, key, direction):
    destination = checked_path(prepared['original'][key]['path'])
    observation = prepared['staged' if direction == 'install' else 'archived'][key]
    pending = pending_path(prepared, directory, key, direction)
    if pending.exists() or pending.is_symlink():
        checked(rebase(observation, pending))
    else:
        snapshot(observation, pending)
    require(state.identity(destination) in (prepared['original'][key], prepared['desired'][key]),
            '替换前存在事务外修改：' + key)
    os.replace(pending, destination)
    sync_directory(destination.parent)


def clean_pending(prepared, directory):
    for key in FILE_KEYS:
        for direction, collection in [('install', 'staged'), ('restore', 'archived')]:
            pending = pending_path(prepared, directory, key, direction)
            if pending.exists() or pending.is_symlink():
                checked(rebase(prepared[collection][key], pending))
                pending.unlink()
                sync_directory(pending.parent)


def verify_set(prepared, collection):
    for observation in prepared[collection].values():
        checked(observation)


def prepare(work, originals, replacements, *, evidence):
    work = canonical(work)
    require(work.is_dir(), '工程目录不存在')
    with project_lock(work):
        pending_transactions(work)
        paths = destinations(work, originals)
        require(set(replacements) == set(FILE_KEYS), '替换文件必须恰好四个')
        require(isinstance(evidence, dict) and evidence, '必须显式绑定候选验证证据')
        for observation in [*originals.values(), *replacements.values(), *evidence.values()]:
            checked(observation)
        require(len({entry['path'] for entry in replacements.values()}) == len(FILE_KEYS)
                and not set(paths.values()) & {Path(entry['path']) for entry in replacements.values()},
                '替换来源必须独立且不能是正式交付路径')
        root = canonical(work / 'paired_revisions')
        root.mkdir(exist_ok=True)
        sync_directory(work)
        directory = root / uuid.uuid4().hex
        directory.mkdir()
        sync_directory(root)
        require(all(path.parent.stat().st_dev == directory.stat().st_dev for path in paths.values()),
                '逐文件原子替换要求目标与事务位于同一文件系统')
        journal_path = directory / 'transaction.json'
        journal = {'version': 4, 'state': 'preparing', 'created_at': state.now(), 'work': str(work),
                   'implementation': state.identity(__file__), 'original': originals}
        durable_json(journal_path, journal)
        try:
            for name in ['archive', 'staged']:
                (directory / name).mkdir()
            sync_directory(directory)
            archived = {key: snapshot(originals[key], directory / 'archive' / key) for key in FILE_KEYS}
            staged = {key: snapshot(replacements[key], directory / 'staged' / key) for key in FILE_KEYS}
            prepared = {'version': 4, 'kind': 'paired-delivery-file-transaction', 'work': str(work),
                        'implementation': state.identity(__file__), 'original': originals, 'archived': archived,
                        'sources': replacements, 'staged': staged, 'evidence': evidence,
                        'desired': {key: rebase(replacements[key], paths[key]) for key in FILE_KEYS},
                        'multi_file_atomic_visibility_claimed': False, 'media_encoded': False}
            prepared_path = directory / 'prepared.json'
            durable_json(prepared_path, prepared)
            verify_set(prepared, 'original')
            journal.update(state='prepared', prepared=state.identity(prepared_path))
            durable_json(journal_path, journal)
        except BaseException as error:
            journal.update(state='failed_before_install', error=f'{type(error).__name__}: {error}')
            durable_json(journal_path, journal)
            raise
        return {'state': 'prepared', 'transaction': str(journal_path), 'production_applied': False}


def restore_originals(prepared, path, journal, verify_original, final_state):
    current_versions(prepared)
    journal.update(state='rolling_back')
    durable_json(path, journal)
    try:
        for key in FILE_KEYS:
            if not state.matches(prepared['original'][key]):
                replace_from(prepared, path.parent, key, 'restore')
        verify_set(prepared, 'original')
        verify_original(prepared)
        verify_set(prepared, 'original')
        clean_pending(prepared, path.parent)
        journal.update(state=final_state, finished_at=state.now())
        durable_json(path, journal)
    except BaseException as error:
        journal.update(state='recovery_required', recovery_error=f'{type(error).__name__}: {error}')
        durable_json(path, journal)
        raise


def promote(work, transaction, *, verify_prepared, verify_current, verify_original, apply=False):
    require(type(apply) is bool and all(callable(callback) for callback in [
        verify_prepared, verify_current, verify_original]), '需要明确的 apply 布尔值及三个版本验证器')
    work, path = canonical(work), transaction_path(work, transaction)
    with project_lock(work):
        pending_transactions(work, path)
        prepared, journal = load_transaction(work, path)
        if journal['state'] == 'committed':
            verify_set(prepared, 'desired')
            verify_current(prepared)
            verify_set(prepared, 'desired')
            return {'state': 'already_current', 'production_applied': False, 'transaction': str(path)}
        require(journal['state'] == 'prepared', '先恢复中断事务，不得直接续装')
        verify_set(prepared, 'original')
        verify_prepared(prepared)
        load_transaction(work, path)
        verify_set(prepared, 'original')
        if not apply:
            return {'state': 'planned', 'production_applied': False, 'transaction': str(path)}
        journal.update(state='installing', installed=[])
        durable_json(path, journal)
        try:
            for key in FILE_KEYS:
                replace_from(prepared, path.parent, key, 'install')
                journal['installed'].append(key)
                durable_json(path, journal)
            verify_set(prepared, 'desired')
            verify_current(prepared)
            load_transaction(work, path)
            verify_set(prepared, 'desired')
            journal.update(state='committed', finished_at=state.now())
            durable_json(path, journal)
        except BaseException as error:
            journal['error'] = f'{type(error).__name__}: {error}'
            try:
                load_transaction(work, path)
                restore_originals(prepared, path, journal, verify_original, 'automatic_rollback')
            except BaseException as recovery_error:
                journal.update(state='recovery_required', recovery_error=f'{type(recovery_error).__name__}: {recovery_error}')
                durable_json(path, journal)
                raise recovery_error from error
            raise
        return {'state': 'committed', 'production_applied': True, 'transaction': str(path)}


def rollback(work, transaction, *, verify_original, apply=False):
    require(type(apply) is bool and callable(verify_original), '需要明确的 apply 布尔值及原版本验证器')
    work, path = canonical(work), transaction_path(work, transaction)
    with project_lock(work):
        pending_transactions(work, path)
        prepared, journal = load_transaction(work, path)
        require(journal['state'] != 'failed_before_install', '准备失败，无正式替换可回滚')
        current_versions(prepared)
        if journal['state'] in {'automatic_rollback', 'rolled_back', 'recovered_originals'}:
            verify_set(prepared, 'original')
            verify_original(prepared)
            verify_set(prepared, 'original')
            return {'state': 'already_rolled_back', 'production_applied': False, 'transaction': str(path)}
        if not apply:
            return {'state': 'rollback_planned', 'production_applied': False, 'transaction': str(path)}
        restore_originals(prepared, path, journal, verify_original, 'rolled_back')
        return {'state': 'rolled_back', 'production_applied': True, 'transaction': str(path)}


def recover(work, transaction, *, verify_current, verify_original):
    require(callable(verify_current) and callable(verify_original), '恢复必须提供新旧版本验证器')
    work, path = canonical(work), transaction_path(work, transaction)
    with project_lock(work):
        pending_transactions(work, path)
        require(not path.is_symlink(), '事务日志不能是符号链接')
        journal = state.load(path)
        if journal.get('state') in {'preparing', 'failed_before_install'} and 'prepared' not in journal:
            require(journal.get('version') == 4 and journal.get('work') == str(work)
                    and journal.get('implementation') == state.identity(__file__), '中断准备的身份无效')
            destinations(work, journal['original'])
            for observation in journal['original'].values():
                checked(observation)
            verify_original(journal)
            for observation in journal['original'].values():
                checked(observation)
            if journal['state'] == 'failed_before_install':
                return {'state': 'verified_failed_before_install', 'production_applied': False, 'transaction': str(path)}
            journal.update(state='failed_before_install', finished_at=state.now())
            durable_json(path, journal)
            return {'state': 'failed_before_install', 'production_applied': False, 'transaction': str(path)}
        prepared, journal = load_transaction(work, path)
        if journal['state'] == 'committed':
            verify_set(prepared, 'desired')
            verify_current(prepared)
            verify_set(prepared, 'desired')
            return {'state': 'verified_committed', 'production_applied': False, 'transaction': str(path)}
        restore_originals(prepared, path, journal, verify_original, 'recovered_originals')
        return {'state': 'recovered_originals', 'production_applied': True, 'transaction': str(path)}
