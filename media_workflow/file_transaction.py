"""Preserve and atomically replace an explicit set of project files with recovery."""

import os
import re
from pathlib import Path

import dubbing_state as state
from paired_delivery_transaction import canonical, checked, durable_json, snapshot, sync_directory
from .speech_variants import require


KIND = 'explicit-project-file-transaction-v1'
ACTIVE = {'prepared', 'installing', 'rolling_back', 'recovery_required'}


def rebase(bound, path):
    return {**bound, 'path': str(path)}


def observed(path):
    path = canonical(path)
    require(not path.is_symlink() and (not path.exists() or path.is_file()), 'Destination must be a regular file or absent')
    return state.identity(path) if path.exists() else None


class FileTransaction:
    def __init__(self, directory):
        self.directory = canonical(directory)
        self.journal_path = self.directory / 'transaction.json'
        self.journal = state.load(self.journal_path)
        require(self.journal['kind'] == KIND and self.journal['state'] in ACTIVE | {'committed', 'rolled_back'}
            and self.journal['implementation'] == state.identity(__file__), 'Unknown/stale file transaction')
        checked(self.journal['prepared'])
        self.prepared = state.load(self.journal['prepared']['path'])
        require(self.journal['prepared']['path'] == str(self.directory / 'prepared.json')
            and self.prepared['kind'] == KIND and self.prepared['directory'] == str(self.directory)
            and self.prepared['implementation'] == state.identity(__file__)
            and set(self.journal['versions']) == set(self.prepared['files']), 'Invalid transaction authority')
        for key, entry in self.prepared['files'].items():
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', key) and '..' not in key,
                'Unsafe transaction entry name')
            canonical(entry['destination'])
            if entry['original'] is not None:
                require(entry['original']['path'] == entry['destination'] and
                    entry['archive'] == rebase(entry['original'], self.directory / 'archive' / key), 'Wrong archive route')
                checked(entry['archive'])
            else:
                require(entry['archive'] is None, 'Absent original cannot have a fabricated archive')
            if entry['source'] is not None:
                require(entry['staged'] == rebase(entry['source'], self.directory / 'staged' / key), 'Wrong staged source route')
                checked(entry['staged'])
            else:
                require(entry['staged'] is None, 'Generated file cannot have a fabricated initial source')
            for version in self.journal['versions'][key]:
                staged = Path(version['staged']['path'])
                require(staged.parent == self.directory / 'staged' and version['desired'] ==
                    rebase(version['staged'], entry['destination']), 'Wrong registered generated version route')
                checked(version['staged'])

    @classmethod
    def prepare(cls, directory, work, files, evidence):
        directory, work = canonical(directory), canonical(work)
        require(not directory.exists() and not directory.is_relative_to(work) and not work.is_relative_to(directory),
            'Use a fresh transaction directory outside the live project')
        require(1 <= len(files) <= 128 and len({entry['destination'] for entry in files.values()}) == len(files),
            'Incomplete or overlapping transaction destinations')
        for key, entry in files.items():
            require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', key) and '..' not in key
                and set(entry) == {'destination', 'original', 'source'}, 'Invalid explicit transaction entry')
            destination = canonical(entry['destination'])
            require(destination.parent == work or (destination.parent == work.parent and destination.suffix == '.mp4'),
                'Transaction destination escapes this project')
            require(observed(destination) == entry['original'], 'Destination changed before transaction preparation')
            if entry['source'] is not None:
                checked(entry['source'])
        for reference in evidence.values():
            checked(reference)
        directory.mkdir(parents=True)
        (directory / 'archive').mkdir()
        (directory / 'staged').mkdir()
        entries, versions = {}, {}
        for key, entry in files.items():
            archive = snapshot(entry['original'], directory / 'archive' / key) if entry['original'] is not None else None
            staged = snapshot(entry['source'], directory / 'staged' / key) if entry['source'] is not None else None
            require(observed(entry['destination']) == entry['original'], 'Destination changed during exact snapshots')
            entries[key] = {**entry, 'archive': archive, 'staged': staged}
            versions[key] = [{'staged': staged, 'desired': rebase(staged, entry['destination'])}] if staged else []
        prepared = {'kind': KIND, 'implementation': state.identity(__file__), 'directory': str(directory),
            'work': str(work), 'created_at': state.now(), 'files': entries, 'evidence': evidence}
        durable_json(directory / 'prepared.json', prepared)
        durable_json(directory / 'transaction.json', {'kind': KIND, 'implementation': state.identity(__file__),
            'prepared': state.identity(directory / 'prepared.json'), 'state': 'prepared', 'versions': versions,
            'intents': [], 'created_at': state.now()})
        return cls(directory)

    def save(self):
        self.journal['updated_at'] = state.now()
        durable_json(self.journal_path, self.journal)

    def current(self):
        result = {}
        for key, entry in self.prepared['files'].items():
            actual = observed(entry['destination'])
            permitted = [entry['original'], *[version['desired'] for version in self.journal['versions'][key]]]
            require(actual in permitted, f'Refuse to overwrite an unregistered change: {key}')
            result[key] = actual
        return result

    def replace(self, key, source, direction):
        entry = self.prepared['files'][key]
        destination = Path(entry['destination'])
        before = self.current()[key]
        desired = rebase(source, destination) if source is not None else None
        if before == desired:
            return
        self.journal['intents'].append({'key': key, 'direction': direction, 'before': before,
            'desired': desired, 'created_at': state.now()})
        self.save()
        if source is None:
            require(direction == 'rollback' and entry['original'] is None, 'Only a known newly created file may be removed')
            require(observed(destination) == before, 'Destination changed before removal')
            destination.unlink()
            sync_directory(destination.parent)
        else:
            pending = self.pending(key, direction)
            if pending.exists() or pending.is_symlink():
                checked(rebase(source, pending))
            else:
                snapshot(source, pending)
            require(observed(destination) == before, 'Destination changed before atomic replacement')
            os.replace(pending, destination)
            sync_directory(destination.parent)
        require(observed(destination) == desired, 'Atomic replacement readback differs')

    def pending(self, key, direction):
        destination = Path(self.prepared['files'][key]['destination'])
        identity = state.signature(str(self.directory))[:12]
        return destination.with_name(f'.editorial-{identity}-{key}-{direction}.pending')

    def install(self):
        require(self.journal['state'] == 'prepared', 'Do not restart an applied or interrupted transaction')
        self.current()
        self.journal['state'] = 'installing'
        self.save()
        for key, entry in self.prepared['files'].items():
            if entry['staged'] is not None:
                self.replace(key, entry['staged'], 'install')

    def write(self, key, value, text=False):
        require(self.journal['state'] == 'installing' and key in self.prepared['files']
            and self.prepared['files'][key]['source'] is None, 'Only declared generated outputs may be written')
        self.current()
        path = self.directory / 'staged' / f'{key}-generated-{len(self.journal["versions"][key]):04}'
        require(not path.exists(), 'Preserve an unreceipted generated version')
        if text:
            require(isinstance(value, str), 'Generated text must be Unicode')
            with path.open('x', encoding='utf-8') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(path.parent)
        else:
            durable_json(path, value)
        bound = state.identity(path)
        self.journal['versions'][key].append({'staged': bound,
            'desired': rebase(bound, self.prepared['files'][key]['destination'])})
        self.save()
        self.replace(key, bound, 'install')

    def commit(self, verification):
        require(self.journal['state'] == 'installing', 'Only a validated live installation can commit')
        for reference in self.prepared['evidence'].values():
            checked(reference)
        checked(verification)
        require(state.load(verification['path'])['passed'] is True, 'Installation verification did not pass')
        current = self.current()
        for key in self.prepared['files']:
            versions = self.journal['versions'][key]
            require(versions and current[key] == versions[-1]['desired'], 'Missing or stale declared installation output')
        self.journal.update(state='committed', verification=verification, committed_at=state.now())
        self.save()
        return self.check()

    def rollback(self):
        require(self.journal['state'] in ACTIVE | {'rolled_back'}, 'A committed installation is not implicitly rolled back')
        self.current()
        if self.journal['state'] != 'rolled_back':
            self.journal['state'] = 'rolling_back'
            self.save()
            for key, entry in reversed(list(self.prepared['files'].items())):
                self.replace(key, entry['archive'], 'rollback')
            for key, entry in self.prepared['files'].items():
                versions = [entry['archive'], *[version['staged'] for version in self.journal['versions'][key]]]
                for direction in ('install', 'rollback'):
                    pending = self.pending(key, direction)
                    if pending.exists() or pending.is_symlink():
                        require(observed(pending) in [rebase(bound, pending) for bound in versions if bound is not None],
                            'Unknown pending file cannot be removed during recovery')
                        directory = self.directory / 'recovered-pending'
                        directory.mkdir(exist_ok=True)
                        destination = directory / pending.name
                        require(not destination.exists(), 'Preserve an already archived pending version')
                        os.replace(pending, destination)
                        sync_directory(pending.parent)
                        sync_directory(directory)
            self.journal.update(state='rolled_back', rolled_back_at=state.now())
            self.save()
        require(all(self.current()[key] == entry['original'] for key, entry in self.prepared['files'].items()),
            'Rollback did not restore all originals and original absences')
        return {'state': 'rolled_back', 'journal': state.identity(self.journal_path), 'old_versions_preserved': True}

    def check(self):
        require(self.journal['state'] == 'committed', 'Transaction is not committed')
        for reference in self.prepared['evidence'].values():
            checked(reference)
        checked(self.journal['verification'])
        require(state.load(self.journal['verification']['path'])['passed'] is True, 'Committed verification changed')
        current = self.current()
        require(all(self.journal['versions'][key] and current[key] == self.journal['versions'][key][-1]['desired']
            for key in self.prepared['files']), 'Committed destination no longer matches its last registered version')
        return {'state': 'committed', 'journal': state.identity(self.journal_path),
            'files': len(current), 'all_old_versions_preserved': True}
