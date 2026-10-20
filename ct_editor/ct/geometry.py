"""MATLAB-style Radon: four subpixels deposited into unit-spaced detector bins.

Forward units are pixels; the image display domain remains [-1,1]^2.
The reconstruction adjoint is the exact transpose, not unfiltered iradon.
"""
from dataclasses import dataclass
import hashlib
import json
import math
from time import perf_counter

import numpy as np
from scipy.sparse import csr_matrix, vstack

from ..images import InputError, canonical

TOL = 1e-12
BUILDER = 'matlab_subpixel_bins_v1'


def native_detector_offsets(resolution):
    """Square-image MATLAB radial extent, including one padding bin per side."""
    radius = math.ceil(math.sqrt(2) * (resolution // 2)) + 1
    return np.arange(-radius, radius + 1, dtype=np.float64)


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InputError(f'{name} must be an integer from {low} to {high}.', name)
    return value


def scalar(value, name, low=0, high=float('inf')):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise InputError(f'{name} must be a finite number in [{low}, {high}].', name)
    return float(value)


def fields(data, allowed, required=()):
    if not isinstance(data, dict):
        raise InputError('Expected an object.')
    extra = set(data) - set(allowed)
    missing = set(required) - set(data)
    if extra or missing:
        field = sorted(extra or missing)[0]
        raise InputError('Unsupported field.' if extra else 'Required field is missing.', field)


def identifier(value, name):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isprintable():
        raise InputError('Expected a printable identifier of 1–128 characters.', name)
    return value


@dataclass(frozen=True)
class Geometry:
    resolution: int
    angle_bytes: bytes
    offset_bytes: bytes
    header: str
    geometry_hash: str

    @classmethod
    def arrays(cls, resolution, angles, offsets, range_degrees=None):
        angles = np.asarray(angles, dtype='<f8'); offsets = np.asarray(offsets, dtype='<f8')
        if resolution < 1 or angles.ndim != 1 or offsets.ndim != 1 or not angles.size or not offsets.size or not np.isfinite(angles).all() or not np.isfinite(offsets).all():
            raise InputError('Invalid geometry arrays.')
        if offsets.size > 1 and not np.all(np.diff(offsets) > 0):
            raise InputError('Detector offsets must be strictly increasing.')
        header = dict(resolution=resolution, image_shape=[resolution,resolution], domain=[[-1,1],[-1,1]],
            pixel_width=2/resolution, row_origin='top', column_origin='left', image_order='C',
            sinogram_shape=[len(angles),len(offsets)], measurement_order='angle_major', angle_units='radians', offset_units='pixels',
            model='parallel_subpixel_detector_bins', builder=BUILDER,
            projection_pixel_width=1.0, projection_origin_index=(resolution+1)//2-1,
            subpixel_offsets=[-.25,.25], subpixel_weight=.25,
            detector_sampling='linear_resample_native_bins_zero_outside',
            boundary='zero_outside_native_detector_extent',
            tolerance=TOL, tolerance_policy='float64_linear_bin_weights', dtype='float64', byte_order='little', range_degrees=range_degrees)
        a,b=angles.tobytes(),offsets.tobytes()
        return cls(resolution,a,b,json.dumps(header,sort_keys=True),hashlib.sha256(canonical(header,a+b)).hexdigest())

    @classmethod
    def request(cls, resolution, data):
        fields(data,('n_angles','n_detectors','start_degrees','end_degrees'),('n_angles','n_detectors','start_degrees','end_degrees'))
        na=integer(data['n_angles'],'n_angles',1,360); nd=integer(data['n_detectors'],'n_detectors',2,256)
        start=scalar(data['start_degrees'],'start_degrees',0,180); end=scalar(data['end_degrees'],'end_degrees',0,180)
        if start >= end: raise InputError('Angular start must be less than end.', 'start_degrees')
        native = native_detector_offsets(resolution)
        return cls.arrays(resolution,np.deg2rad(np.linspace(start,end,na,endpoint=False)),
            np.linspace(native[0],native[-1],nd),[start,end])

    @property
    def angles(self): return np.frombuffer(self.angle_bytes,dtype='<f8')
    @property
    def offsets(self): return np.frombuffer(self.offset_bytes,dtype='<f8')
    @property
    def shape(self): return (len(self.angles)*len(self.offsets),self.resolution**2)
    def metadata(self):
        return {**json.loads(self.header), 'geometry_hash':self.geometry_hash, 'angles':self.angles.tolist(), 'offsets':self.offsets.tolist()}
    def estimate_bytes(self):
        # Each native pixel touches at most three bins per angle. Resampling
        # can extend the three nonzero bins to a support width of four
        # native-bin units. Bound output
        # storage, construction copies, and the per-angle COO workspace.
        m,n=self.shape
        spacing=np.diff(self.offsets)
        if np.array_equal(self.offsets,native_detector_offsets(self.resolution)):
            per_pixel=3
        else:
            per_pixel=(min(len(self.offsets), math.ceil(4/spacing.min()))
                       if spacing.size else 1)
        entries=len(self.angles)*n*per_pixel
        return entries*40 + n*8*32 + (m+n)*64 + 16384


class Operator:
    def __init__(self, geometry, matrix, duration=0):
        self.geometry=geometry
        a=csr_matrix(matrix,dtype=np.float64,copy=True);a.sum_duplicates();a.eliminate_zeros();a.sort_indices()
        if a.shape != geometry.shape or not np.isfinite(a.data).all() or (a.data<0).any():
            raise InputError('Invalid sparse operator.',code='geometry_failure',status=422)
        # Immutable bytes prevent accidental writes even through CSR array views.
        a.data=np.frombuffer(a.data.astype('<f8').tobytes(),dtype='<f8')
        a.indices=np.frombuffer(a.indices.astype('<i4').tobytes(),dtype='<i4')
        a.indptr=np.frombuffer(a.indptr.astype('<i4').tobytes(),dtype='<i4')
        self._matrix=a
        one=float(np.asarray(abs(a).sum(axis=0)).max());inf=float(np.asarray(abs(a).sum(axis=1)).max())
        safe=one*inf
        self._metadata=dict(operator_id=geometry.geometry_hash,geometry_hash=geometry.geometry_hash,shape=list(a.shape),nnz=a.nnz,
            storage_bytes=a.data.nbytes+a.indices.nbytes+a.indptr.nbytes, norm_one=one,norm_infinity=inf,L_safe=safe,
            step_policy='safe_induced_norm_v1',build_seconds=duration,builder=BUILDER,
            csr_checksum=hashlib.sha256(canonical({'shape':list(a.shape),'data':'<f8','indices':'<i4','indptr':'<i4'},a.data.tobytes()+a.indices.tobytes()+a.indptr.tobytes())).hexdigest())
    def metadata(self): return dict(self._metadata,shape=list(self._matrix.shape))
    def sparse_copy(self): return self._matrix.copy()
    @property
    def shape(self): return self._matrix.shape
    def _vector(self, vector, size):
        vector=np.asarray(vector,dtype=np.float64)
        if vector.shape != (size,) or not np.isfinite(vector).all(): raise InputError('Expected a finite compatible vector.',code='incompatible_dimensions')
        return vector
    def forward(self,x): return self._matrix @ self._vector(x,self.shape[1])
    def adjoint(self,z): return self._matrix.T @ self._vector(z,self.shape[0])
    def normal(self,x): return self.adjoint(self.forward(x))
    def _block(self, value, size):
        value=np.asarray(value,dtype=np.float64)
        if value.ndim!=2 or value.shape[0]!=size or not 1<=value.shape[1]<=64 or not np.isfinite(value).all():
            raise InputError('Expected a finite compatible block of at most 64 columns.',code='incompatible_dimensions')
        return value
    def forward_block(self,x): return self._matrix @ self._block(x,self.shape[1])
    def adjoint_block(self,x): return self._matrix.T @ self._block(x,self.shape[0])


def detector_resampling(native, offsets):
    """Linear sampling of native bin values; no extrapolation or amplitude fit."""
    q=offsets-native[0]
    lo=np.floor(q).astype(int);fraction=q-lo
    rows=[];cols=[];values=[]
    inside=(q>=0)&(q<=len(native)-1)
    for shift,weight in ((0,1-fraction),(1,fraction)):
        col=lo+shift
        valid=inside&(col>=0)&(col<len(native))&(weight>0)
        rows.extend(np.flatnonzero(valid));cols.extend(col[valid]);values.extend(weight[valid])
    return csr_matrix((values,(rows,cols)),shape=(len(offsets),len(native)))


def build_operator(geometry, check=lambda:None, progress=lambda *args:None):
    """Deposit four quarter-intensity subpixels into neighboring native bins.

    Matches the documented MATLAB radon discretization at native integer
    detector offsets. Custom detector counts sample that native profile.
    https://www.mathworks.com/help/images/ref/radon.html
    """
    start=perf_counter();side=geometry.resolution;n=side**2
    native=native_detector_offsets(side)
    sampling=detector_resampling(native,geometry.offsets)
    row,col=np.indices((side,side),dtype=np.float64)
    origin=(side+1)//2-1
    x=col.ravel()-origin;y=origin-row.ravel()
    pixels=np.arange(n,dtype=np.int32)
    blocks=[]
    for j,theta in enumerate(geometry.angles):
        check()
        cosine,sine=np.cos(theta),np.sin(theta)
        rows=[];cols=[];values=[]
        for dx in (-.25,.25):
            for dy in (-.25,.25):
                check()
                position=(x+dx)*cosine+(y+dy)*sine-native[0]
                lower=np.floor(position).astype(np.int32);fraction=position-lower
                for shift,weight in ((0,.25*(1-fraction)),(1,.25*fraction)):
                    detector=lower+shift
                    valid=(detector>=0)&(detector<len(native))&(weight>0)
                    rows.append(detector[valid]);cols.append(pixels[valid]);values.append(weight[valid])
        block=csr_matrix((np.concatenate(values),(np.concatenate(rows),np.concatenate(cols))),
                         shape=(len(native),n))
        blocks.append(sampling@block)
        progress('Building operator',j+1,len(geometry.angles))
    check()
    matrix=vstack(blocks,format='csr')
    del blocks
    return Operator(geometry,matrix,perf_counter()-start)
