"""Immutable snapshot storage with atomic capacity checks."""
from datetime import datetime, timezone
import json
from threading import Lock
from uuid import uuid4

import numpy as np

from .images import InputError, serialize


class SnapshotStore:
    def __init__(self, max_count=50, max_bytes=64 * 1024**2, budget=None):
        self.max_count, self.max_bytes = max_count, max_bytes
        self._budget = budget
        self._leases = []
        self._records = {}
        self._size = 0
        self._lock = Lock()

    def insert(self, metadata, payload, source, identity):
        payload, source = bytes(payload), bytes(source)
        record = {**metadata, "snapshot_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_document_id": identity["editor_document_id"],
            "source_editor_revision": identity["editor_revision"]}
        encoded = json.dumps(record, allow_nan=False)
        size = len(payload) + len(source) + len(encoded.encode())
        with self._lock:
            if len(self._records) >= self.max_count or self._size + size > self.max_bytes:
                raise InputError("Snapshot storage is full. Existing snapshots are preserved; restart the server to clear the store.", code="storage_limit", status=507)
            if self._budget:
                self._leases.append(self._budget.reserve(size))
            self._records[record["snapshot_id"]] = (encoded, payload, source)
            self._size += size
        return self.get(record["snapshot_id"])

    def _get(self, snapshot_id):
        with self._lock:
            record = self._records.get(snapshot_id)
        if record is None:
            raise InputError("Snapshot unavailable. It may belong to a previous server process.", "snapshot_id", "snapshot_not_found", 404)
        return record

    def get(self, snapshot_id):
        metadata, payload, _ = self._get(snapshot_id)
        return serialize(json.loads(metadata), payload)

    def array(self, snapshot_id):
        """M2 boundary: a read-only float64 view backed by immutable bytes."""
        metadata, payload, _ = self._get(snapshot_id)
        return np.frombuffer(payload, dtype="<f8").reshape(json.loads(metadata)["shape"])

    def list(self):
        with self._lock:
            return [json.loads(record[0]) for record in self._records.values()]
