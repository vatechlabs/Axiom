"""Rule based folder selection shared by error, EDI, and RECON indexing."""
import os
import re
from datetime import datetime
from pathlib import Path
from django.conf import settings
from .models import FileSourceRule
from .highmark_837 import HIGHMARK_CODES, highmark_extensionless_837

TYPES = ('error_reports','835','837','recon_abc','recon_musgrove','recon')
DEFAULT_EXT = {'error_reports':'.txt','835':'.835,.edi,.txt','837':'.837,.edi,.txt','recon_abc':'.csv','recon_musgrove':'.csv','recon':'.pa7,.csv'}

def valid_root(path):
    resolved = Path(path).resolve()
    # Native Windows: allow absolute local and UNC paths that the service can read.
    # Docker/Linux: preserve explicitly approved roots.
    if os.name == 'nt':
        return resolved.is_absolute() and resolved.is_dir()
    return any(resolved.is_relative_to(Path(root).resolve()) for root in settings.EDI_ALLOWED_ROOTS)

def wgs_835_year_allowed(path):
    """WGS 835 filename positions 4-5 represent a two-digit year.

    Allow 25-99; ignore malformed names and years 00-24.
    """
    stem = path.stem
    return len(stem) >= 5 and stem[3:5].isdigit() and int(stem[3:5]) >= 25


def wgs_837_year_allowed(path):
    """WGS 837: accept MMDDYYYY embedded in filename, year >= 2025.

    Examples: ...mcsjaa10282024... -> False
              ...mcsjaa10282026... -> True
    Files without a valid MMDDYYYY date are excluded.
    """
    for match in re.finditer(
        r'(?<![0-9])((?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])(20[0-9]{2}))',
        path.stem,
    ):
        try:
            date = datetime.strptime(match.group(1), '%m%d%Y')
        except ValueError:
            continue
        if date.year >= 2025:
            return True
    return False


def match_file(rule, path):
    if (rule.file_type == '837' and rule.client.code.upper() in HIGHMARK_CODES
            and highmark_extensionless_837(path)):
        # Apply configured prefix/suffix/subfolder constraints, but don't
        # require an extension for the observed Highmark IPA files.
        stem=path.stem.casefold()
        prefixes=[x.strip().casefold() for x in rule.starts_with.split(',') if x.strip()]
        suffixes=[x.strip().casefold() for x in rule.ends_with.split(',') if x.strip()]
        return (not prefixes or any(stem.startswith(x) for x in prefixes)) and (not suffixes or any(stem.endswith(x) for x in suffixes))
    if rule.file_type == '837' and rule.client.code.upper() == 'WGS':
        if not wgs_837_year_allowed(path):
            return False
    if rule.file_type == '835' and rule.client.code.upper() == 'WGS':
        if not wgs_835_year_allowed(path):
            return False
    stem=path.stem.casefold()
    prefixes=[x.strip().casefold() for x in rule.starts_with.split(',') if x.strip()]
    suffixes=[x.strip().casefold() for x in rule.ends_with.split(',') if x.strip()]
    exts=[x.strip().lower() for x in rule.extensions.split(',') if x.strip()]
    return (not prefixes or any(stem.startswith(x) for x in prefixes)) and (not suffixes or any(stem.endswith(x) for x in suffixes)) and (not exts or path.suffix.lower() in exts)

def rule_files(rule):
    root=Path(rule.folder_path).resolve()
    if not valid_root(root):raise ValueError('Folder outside EDI_ALLOWED_ROOTS: '+str(root))
    if not root.is_dir():raise ValueError('Folder not accessible: '+str(root))
    include=[x.strip().replace('\\','/').casefold() for x in rule.include_subfolders.split(',') if x.strip()]
    exclude=[x.strip().replace('\\','/').casefold() for x in rule.exclude_subfolders.split(',') if x.strip()]
    # Target selected subfolders; avoid walking the entire SMB tree.
    if include and not rule.recursive:
        folders=[root]
        for relative in include:
            sub=root.joinpath(*relative.split('/'))
            if sub.is_dir() and sub.resolve().is_relative_to(root):
                folders.append(sub)
        iterator=(path for folder in folders for path in folder.rglob('*'))
    else:
        iterator=root.rglob('*') if rule.recursive else root.iterdir()
    for path in iterator:
        if path.is_symlink() or not path.is_file() or path.name.startswith('.'):continue
        parent=path.relative_to(root).parent.as_posix().casefold()
        if not rule.recursive and not include and parent!='.':continue
        if include and parent!='.' and not any(parent==x or parent.startswith(x+'/') for x in include):continue
        if exclude and any(parent==x or parent.startswith(x+'/') for x in exclude):continue
        resolved=path.resolve()
        if not resolved.is_relative_to(root):continue
        if match_file(rule,path):yield resolved

def rules_for(client,kind):
    return list(FileSourceRule.objects.filter(client=client,file_type=kind,enabled=True).order_by('id'))

def has_rules(client,kind):
    return FileSourceRule.objects.filter(client=client,file_type=kind).exists()


def scan_rule_entries(rule):
    """Yield filtered path and cached stat; avoid redundant SMB requests.

    Only configured directories are traversed, never sibling folders. No symlinks.
    Existing rule_files() remains unchanged for RECON.
    """
    import os
    root=Path(rule.folder_path).resolve()
    if not valid_root(root):
        raise ValueError('Folder unavailable or outside approved roots: '+str(root))
    included=[p.strip().replace('\\','/').strip('/') for p in rule.include_subfolders.split(',') if p.strip()]
    excluded=[p.strip().replace('\\','/').strip('/').casefold() for p in rule.exclude_subfolders.split(',') if p.strip()]
    target_dirs=[root]
    for rel in included:
        target=root.joinpath(*rel.split('/'))
        if not target.resolve().is_relative_to(root):
            raise ValueError('Included folder outside approved root')
        if target.is_dir():target_dirs.append(target)
    def enumerate_dir(directory, recursive):
        with os.scandir(directory) as directory_entries:
            entries=sorted(directory_entries,key=lambda e:e.name.casefold())
        for entry in entries:
            if entry.name.startswith('.') or entry.is_symlink():continue
            path=Path(entry.path)
            relative=path.relative_to(root).as_posix().casefold()
            if any(relative==x or relative.startswith(x+'/') for x in excluded):continue
            # Checking names before metadata saves expensive network round-trips.
            if recursive and entry.is_dir(follow_symlinks=False):
                yield from enumerate_dir(path,True)
            elif match_file(rule,path) and entry.is_file(follow_symlinks=False):
                yield path,entry.stat(follow_symlinks=False)
    if rule.recursive:
        yield from enumerate_dir(root,True)
    else:
        for directory in target_dirs:
            yield from enumerate_dir(directory,directory!=root)
