"""Rule based folder selection shared by error, EDI, and RECON indexing."""
import os
from pathlib import Path
from django.conf import settings
from .models import FileSourceRule

TYPES = ('error_reports','835','837','recon_abc','recon_musgrove','recon')
DEFAULT_EXT = {'error_reports':'.txt','835':'.835,.edi,.txt','837':'.837,.edi,.txt','recon_abc':'.csv','recon_musgrove':'.csv','recon':'.pa7,.csv'}

def valid_root(path):
    resolved = Path(path).resolve()
    # Native Windows: allow absolute local and UNC paths that the service can read.
    # Docker/Linux: preserve explicitly approved roots.
    if os.name == 'nt':
        return resolved.is_absolute() and resolved.is_dir()
    return any(resolved.is_relative_to(Path(root).resolve()) for root in settings.EDI_ALLOWED_ROOTS)

def match_file(rule, path):
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
    iterator=root.rglob('*') if rule.recursive or include else root.iterdir()
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
