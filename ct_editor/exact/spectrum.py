"""Guarded economy SVD; no intentional rank truncation."""
from dataclasses import dataclass
import json
from time import perf_counter

import numpy as np
from scipy.linalg import svd
from threadpoolctl import threadpool_limits

from ..images import InputError
from ..ct.service import digest
from ..ct.solver import frozen_array, SpectralCertificate

VERSION = 'economy_svd_gesdd_v1'
QUALITY_TOL = 1e-10


def workspace_bytes(m, n, candidates=0):
    q = min(m, n)
    # Dense input/copies, economy U/V, LAPACK and quality-check work, W,
    # solver checkpoints and compact search/history; heuristic, not an RSS cap.
    return 8 * (4*m*n + 2*m*q + 8*n*q + 120*n) + candidates*2048 + 1024**2


@dataclass(frozen=True)
class FullSpectrum:
    header: str
    singular_values: np.ndarray
    eigenvalues: np.ndarray
    vectors: np.ndarray
    squared_vectors: np.ndarray

    def metadata(self):
        return json.loads(self.header)

    def size(self):
        return len(self.header.encode()) + sum(a.nbytes for a in (
            self.singular_values, self.eigenvalues, self.vectors, self.squared_vectors))

    def certificate(self):
        m = self.metadata()
        return SpectralCertificate(m['operator_id'], m['csr_checksum'], m['lambda_max'], m['spectrum_id'])


def decompose(operator, check=lambda: None, progress=lambda *args: None, rank_multiplier=1.0):
    check()
    progress('Preparing exact spectrum — dense SVD; cancellation at next safe boundary', 0, None)
    started = perf_counter()
    sparse = operator.sparse_copy()
    m, n = sparse.shape
    with threadpool_limits(limits=1, user_api='blas'):
        dense = sparse.toarray()
        try:
            u, singular, vt = svd(dense, full_matrices=False, lapack_driver='gesdd', check_finite=True)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise InputError('Full SVD failed: '+str(exc), code='decomposition_failure', status=422) from exc
        check()
        q = len(singular)
        norm = float(np.linalg.norm(singular))
        residual = float(np.linalg.norm(dense - (u*singular) @ vt) / max(norm, np.finfo(float).tiny))
        orthogonality = float(np.linalg.norm(vt @ vt.T - np.eye(q), ord='fro') / max(1, q))
        del dense, u
        eigenvalues = singular*singular
        largest = float(eigenvalues[0])
        safe = operator.metadata()['L_safe']
        if (not np.isfinite(eigenvalues).all() or largest <= 0 or
                largest > safe*(1+QUALITY_TOL) or residual > QUALITY_TOL or orthogonality > QUALITY_TOL):
            raise InputError('Full spectrum failed its norm/residual/orthogonality checks.', code='decomposition_failure', status=422)
        tolerance = float(rank_multiplier * max(m, n)*np.finfo(float).eps*singular[0])
        rank = int(np.sum(singular > tolerance))
        ambiguous = int(np.sum((singular > 0) & (singular <= tolerance)))
        underflow = int(np.sum((singular > 0) & (eigenvalues == 0)))
        warnings = []
        if ambiguous: warnings.append('Positive modes below the diagnostic rank threshold remain in covariance; inverse/source checks may be unresolved.')
        if underflow: warnings.append('Squaring positive singular values underflowed; tiny-mode information is unresolved.')
        identity = dict(operator_id=operator.metadata()['operator_id'], csr_checksum=operator.metadata()['csr_checksum'],
                        version=VERSION, dtype='float64', quality_tolerance=QUALITY_TOL, rank_multiplier=rank_multiplier)
        meta = dict(**identity, spectrum_id=digest(identity), shape=[m,n], modes_requested=min(m,n), modes_computed=q,
                    modes_used=q, positive_modes=int(np.sum(eigenvalues > 0)), numerical_rank=rank,
                    rank_tolerance=tolerance, structural_nullity_lower_bound=max(0,n-m), ambiguous_modes=ambiguous,
                    eigenvalue_underflow_modes=underflow, lambda_max=largest, L_safe=safe,
                    decomposition_residual=residual, orthogonality_residual=orthogonality,
                    numerical_status='warnings' if warnings else 'checked', warnings=warnings,
                    workspace_estimate=workspace_bytes(m,n), elapsed_seconds=perf_counter()-started,
                    singular_values=singular.tolist(), eigenvalues=eigenvalues.tolist())
        vectors = frozen_array(vt.T)
        return FullSpectrum(json.dumps(meta, allow_nan=False), frozen_array(singular), frozen_array(eigenvalues),
                            vectors, frozen_array(vectors*vectors))
