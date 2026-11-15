"""Rebuild a replacement pod's working index and unexpired media from private Supabase Storage."""
import json
from apps.api.archive import SupabaseArchive,restore_archive
from apps.api.db import init

if __name__=='__main__':
    init(recover=False);store=SupabaseArchive()
    try:
        store.ensure_private();print(json.dumps({'restored_incidents':restore_archive(store)}))
    finally:store.close()
