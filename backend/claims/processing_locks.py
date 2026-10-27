"""Serialize scans and LAG uploads per client without blocking normal reads."""
from contextlib import contextmanager
from threading import Lock
from django.db import connection

_local_locks={}
class ClientProcessingBusy(ValueError):
    pass

@contextmanager
def client_processing_lock(client_id):
    # PostgreSQL session advisory locks survive the small indexing transactions.
    if connection.vendor=='postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s,%s)',[764231,int(client_id)])
            acquired=cursor.fetchone()[0]
        if not acquired:
            raise ClientProcessingBusy('This client is already processing files or a LAG upload. Retry when processing completes.')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s,%s)',[764231,int(client_id)])
    else:
        lock=_local_locks.setdefault(client_id,Lock())
        if not lock.acquire(blocking=False):
            raise ClientProcessingBusy('Client processing is busy; retry after it finishes.')
        try:
            yield
        finally:
            lock.release()
