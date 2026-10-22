"""M1's authoritative block-area conversion; no tomography or inference."""
import base64
import binascii
import hashlib
import json
import struct

import numpy as np

VERSION = "block_area_v1"
SOURCE_SIDE = 512
RESOLUTIONS = (16, 32, 64, 128)


class InputError(ValueError):
    def __init__(self, message, field=None, code="invalid_input", status=400):
        super().__init__(message)
        self.field, self.code, self.status = field, code, status


def canonical(header, payload):
    """Sorted compact ASCII JSON, 8-byte big-endian length, then payload."""
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return struct.pack(">Q", len(encoded)) + encoded + payload


def block_area(source, resolution):
    """Average square uint8 blocks directly into C-contiguous float64."""
    source = np.asarray(source)
    if (source.ndim != 2 or source.shape[0] != source.shape[1]
            or source.dtype != np.uint8 or type(resolution) is not int
            or resolution < 1 or source.shape[0] == 0 or source.shape[0] % resolution):
        raise InputError("Expected a square uint8 raster divisible by the positive target size.")
    q = source.shape[0] // resolution
    sums = source.reshape(resolution, q, resolution, q).sum(axis=(1, 3), dtype=np.uint64)
    return np.ascontiguousarray(sums.astype(np.float64) / (255 * q * q))


def pixel_center(row, column, resolution):
    width = 2 / resolution
    return (-1 + (column + 0.5) * width, 1 - (row + 0.5) * width)


def parse_request(data):
    if not isinstance(data, dict):
        raise InputError("Expected a JSON object.")
    for field in ("request_id", "editor_document_id"):
        value = data.get(field)
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isprintable():
            raise InputError("Use a nonempty printable identifier of at most 128 characters.", field)
    revision = data.get("editor_revision")
    if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
        raise InputError("Use a nonnegative safe integer revision.", "editor_revision")
    for field, allowed in (("source_side", (SOURCE_SIDE,)), ("numerical_resolution", RESOLUTIONS)):
        if type(data.get(field)) is not int or data[field] not in allowed:
            raise InputError(f"Allowed values: {allowed}.", field)
    for field, expected in (("source_encoding", "grayscale_u8_base64"), ("preprocessing_version", VERSION)):
        if data.get(field) != expected:
            raise InputError(f"Expected {expected}.", field, "unsupported_format", 422)
    encoded = data.get("source_data")
    expected_length = 4 * ((SOURCE_SIDE**2 + 2) // 3)
    if not isinstance(encoded, str) or len(encoded) != expected_length:
        raise InputError("Supply base64 for exactly 512 × 512 scalar bytes.", "source_data")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InputError("Invalid base64 raster.", "source_data") from exc
    if len(raw) != SOURCE_SIDE**2:
        raise InputError("Decoded raster must contain exactly 262144 bytes.", "source_data")
    identity = {key: data[key] for key in ("request_id", "editor_document_id", "editor_revision")}
    return raw, data["numerical_resolution"], identity


def convert(raw, resolution):
    """Return metadata and immutable little-endian array bytes."""
    raw = bytes(raw)
    if len(raw) != SOURCE_SIDE**2 or type(resolution) is not int or resolution not in RESOLUTIONS:
        raise InputError("Invalid source length or numerical resolution.")
    source = np.frombuffer(raw, dtype=np.uint8).reshape(SOURCE_SIDE, SOURCE_SIDE)
    pixels = block_area(source, resolution)
    if not np.isfinite(pixels).all() or pixels.min() < 0 or pixels.max() > 1:
        raise RuntimeError("Invalid numerical conversion output")
    header = {
        "hash_schema": "numerical_image_v1", "resolution": resolution,
        "shape": [resolution, resolution], "dtype": "float64", "byte_order": "little",
        "flatten_order": "C", "domain": {"u": [-1, 1], "v": [-1, 1]},
        "row_origin": "top", "column_origin": "left", "pixel_width": 2 / resolution,
        "preprocessing": {"method": "block_area", "version": VERSION,
            "source_side": SOURCE_SIDE, "target_side": resolution,
            "normalization": "divide_by_255", "raster_interpretation": "scalar_density_no_gamma"},
    }
    payload = pixels.astype("<f8", copy=False).tobytes(order="C")
    source_header = {"source_side": SOURCE_SIDE, "source_encoding": "grayscale_u8", "flatten_order": "C"}
    metadata = {**header, "schema_version": "image_snapshot_v1", "source_side": SOURCE_SIDE,
        "source_encoding": "grayscale_u8", "preprocessing_version": VERSION,
        "source_hash": hashlib.sha256(canonical(source_header, raw)).hexdigest(),
        "content_hash": hashlib.sha256(canonical(header, payload)).hexdigest(),
        "pixel_count": resolution**2,
        "statistics": {"minimum": float(pixels.min()), "maximum": float(pixels.max()),
            "mean": float(pixels.mean()), "nonzero_count": int(np.count_nonzero(pixels)),
            "integrated_density": float(4 * pixels.mean())}}
    return metadata, payload


def serialize(metadata, payload):
    return {**metadata, "pixels": np.frombuffer(payload, dtype="<f8").tolist()}
