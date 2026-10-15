"""Parallel-ray, piecewise-constant projector; lengths in [-1,1]^2."""
from dataclasses import dataclass
import hashlib
import json
import math
from time import perf_counter

import numpy as np
from scipy.sparse import csr_matrix

from ..images import InputError, canonical

TOL = 1e-12
BUILDER = 'ray_segments_v1'


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
        header = dict(resolution=resolution, image_shape=[resolution,resolution], domain=[[-1,1],[-1,1]],
            pixel_width=2/resolution, row_origin='top', column_origin='left', image_order='C',
            sinogram_shape=[len(angles),len(offsets)], measurement_order='angle_major', angle_units='radians', offset_units='dimensionless',
            model='parallel_piecewise_constant', builder=BUILDER, boundary='internal_right_below_outer_zero',
            tolerance=TOL, tolerance_policy='absolute_direction_and_scaled_coordinate_parameter_v1', dtype='float64', byte_order='little', range_degrees=range_degrees)
        a,b=angles.tobytes(),offsets.tobytes()
        return cls(resolution,a,b,json.dumps(header,sort_keys=True),hashlib.sha256(canonical(header,a+b)).hexdigest())

    @classmethod
    def request(cls, resolution, data):
        fields(data,('n_angles','n_detectors','start_degrees','end_degrees'),('n_angles','n_detectors','start_degrees','end_degrees'))
        na=integer(data['n_angles'],'n_angles',1,360); nd=integer(data['n_detectors'],'n_detectors',2,256)
        start=scalar(data['start_degrees'],'start_degrees',0,180); end=scalar(data['end_degrees'],'end_degrees',0,180)
        if start >= end: raise InputError('Angular start must be less than end.', 'start_degrees')
        return cls.arrays(resolution,np.deg2rad(np.linspace(start,end,na,endpoint=False)),np.linspace(-np.sqrt(2),np.sqrt(2),nd),[start,end])

    @property
    def angles(self): return np.frombuffer(self.angle_bytes,dtype='<f8')
    @property
    def offsets(self): return np.frombuffer(self.offset_bytes,dtype='<f8')
    @property
    def shape(self): return (len(self.angles)*len(self.offsets),self.resolution**2)
    def metadata(self):
        return {**json.loads(self.header), 'geometry_hash':self.geometry_hash, 'angles':self.angles.tolist(), 'offsets':self.offsets.tolist()}
    def estimate_bytes(self):
        # Preallocated COO buffers, CSR conversion/copies, indices and work vectors.
        m,n=self.shape
        return m*(2*self.resolution+1)*64 + (m+n)*64 + 16384


def ray_segments(n, theta, offset):
    """Slab clip, deduplicate crossings, assign midpoint; no pixel-center quadrature."""
    normal=np.array([math.cos(theta),math.sin(theta)])
    normal[np.abs(normal)<TOL]=0
    normal/=np.linalg.norm(normal)
    base=offset*normal; direction=np.array([-normal[1],normal[0]])
    enter,leave=-np.inf,np.inf
    for origin,d in zip(base,direction):
        if d == 0:
            if abs(origin) >= 1-TOL: return [],[]  # miss or outer-edge coincidence
        else:
            t0,t1=sorted(((-1-origin)/d,(1-origin)/d))
            enter=max(enter,t0);leave=min(leave,t1)
    eps=TOL*max(1,abs(enter),abs(leave))
    if leave-enter <= eps: return [],[]
    ts=[enter,leave]
    edges=np.linspace(-1,1,n+1)[1:-1]
    for origin,d in zip(base,direction):
        if d != 0:
            cuts=(edges-origin)/d
            ts.extend(cuts[(cuts>enter+eps)&(cuts<leave-eps)])
    ts.sort(); unique=[ts[0]]
    for t in ts[1:]:
        if t-unique[-1]>eps: unique.append(t)
    # Keep the exact clipping endpoint even when a crossing lies within tolerance.
    unique[-1]=leave
    indices=[];weights=[]
    for lo,hi in zip(unique,unique[1:]):
        if hi-lo <= eps: continue
        u,v=base+(lo+hi)/2*direction
        coordinates=np.array([(1-v)*n/2,(u+1)*n/2])
        nearest=np.rint(coordinates)
        on_internal=(nearest>0)&(nearest<n)&(np.abs(coordinates-nearest)<=TOL*n)
        coordinates=np.where(on_internal,nearest,coordinates)
        row,col=np.floor(coordinates).astype(int)
        if not (0<=row<n and 0<=col<n):
            raise InputError('Ray midpoint outside domain under the declared tolerance.',code='geometry_failure',status=422)
        indices.append(int(row*n+col));weights.append(float(hi-lo))
    return indices,weights


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


def build_operator(geometry, check=lambda:None, progress=lambda *args:None):
    start=perf_counter();m,n=geometry.shape;limit=m*(2*geometry.resolution+1)
    rows=np.empty(limit,np.int32);cols=np.empty(limit,np.int32);values=np.empty(limit,np.float64);used=0
    for j,theta in enumerate(geometry.angles):
        check()
        for ell,offset in enumerate(geometry.offsets):
            check()
            indices,weights=ray_segments(geometry.resolution,theta,offset);count=len(indices)
            rows[used:used+count]=j*len(geometry.offsets)+ell;cols[used:used+count]=indices;values[used:used+count]=weights;used+=count
        progress('Building operator',j+1,len(geometry.angles))
    check()
    matrix=csr_matrix((values[:used],(rows[:used],cols[:used])),shape=(m,n))
    return Operator(geometry,matrix,perf_counter()-start)
