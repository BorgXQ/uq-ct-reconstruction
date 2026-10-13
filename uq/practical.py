"""Paper §3.6: randomized normal operator, residual probes, and exact audits.

Exact small-grid references are used to check assumptions, not to silently
replace the candidate interval or its stopping criterion.
"""
from functools import lru_cache
import numpy as np
from scipy.linalg import eigh, qr
from .covariance import geometry, covariance_eigenvalues
from .bias import spectral_bound


@lru_cache(maxsize=4)
def approximation(n, angles, retained_rank, oversampling, sketch_seed, probes, probe_seed):
    a, _, _, _, values, vectors, _ = geometry(n, angles)
    b = (a.T @ a).toarray()
    size = len(values)
    # Symmetric randomized range finder and Rayleigh–Ritz eigendecomposition
    # (Halko Algorithm 5.3). No change to the Landweber point estimator.
    rng = np.random.default_rng(np.random.SeedSequence([sketch_seed, 7163]))
    width = min(size, retained_rank + oversampling)
    q, _ = qr(b @ rng.normal(size=(size, width)), mode='economic')
    small = q.T @ b @ q
    eigenvalues, eigenvectors = eigh((small + small.T)/2)
    order = np.argsort(eigenvalues)[::-1][:retained_rank]
    lam = np.maximum(eigenvalues[order], 0)
    u = q @ eigenvectors[:, order]
    probe_rng = np.random.default_rng(np.random.SeedSequence([probe_seed, 9281]))
    w = probe_rng.choice([-1., 1.], size=(size, probes))
    # Precompute linear coefficients for diag Monte Carlo actions, permitting
    # fixed probes to be reused at all k, including the stopping search.
    exact_coeff = vectors * (w @ (vectors.T @ w).T / probes)
    approx_coeff = u * (w @ (u.T @ w).T / probes)
    residual = b - (u*lam) @ u.T
    approximation_error = float(np.max(np.abs(eigh(residual, eigvals_only=True))))
    descending = values[::-1]
    tail = descending[retained_rank:]
    beta = (float((1+16*np.sqrt(1+retained_rank/(oversampling+1)))*tail[0]
                  + 8*np.sqrt(retained_rank+oversampling)/(oversampling+1)*np.linalg.norm(tail))
            if tail.size else 0.)
    return lam, u, exact_coeff, approx_coeff, approximation_error, beta


class Practical:
    def __init__(self, config, truth, source):
        self.c, self.truth, self.source = config, truth, source
        _, _, _, _, self.values, self.vectors, _ = geometry(config['n'], config['angles'])
        self.omega = 1/(2*self.values[-1])
        (self.lam, self.u, exact_coeff, approx_coeff, self.approx_error, self.beta) = approximation(
            config['n'], config['angles'], config['retained_rank'], config['oversampling'],
            config['sketch_seed'], config['probes'], config['probe_seed'])
        self.coeff = np.concatenate((exact_coeff, -approx_coeff), axis=1)
        self.positive = np.maximum(self.coeff, 0)
        self.negative = np.minimum(self.coeff, 0)
        self.u2 = self.u*self.u

    def weights(self, k):
        return np.concatenate((covariance_eigenvalues(self.values, self.omega, k, self.c['sigma']),
                               covariance_eigenvalues(self.lam, self.omega, k, self.c['sigma'])))

    def evaluate(self, k):
        weights = self.weights(k)
        approximate = self.u2 @ weights[len(self.values):]
        residual_estimate = self.coeff @ weights
        corrected = approximate + np.abs(residual_estimate)/(1-self.c['probe_epsilon'])
        bias = spectral_bound(self.lam, self.omega, k, self.c['nu'], self.source['M'])
        return bias, approximate, residual_estimate, corrected

    def interval_sd_ceiling(self, low, high):
        """Certified envelope, without assuming |estimated residual| is monotone."""
        a, b = self.weights(low), self.weights(high)
        residual_low = self.positive @ a + self.negative @ b
        residual_high = self.positive @ b + self.negative @ a
        upper = self.u2 @ b[len(self.values):] + np.maximum(np.abs(residual_low), np.abs(residual_high))/(1-self.c['probe_epsilon'])
        return float(np.sqrt(np.max(upper)))

    def stopping(self, budget):
        c0 = self.c['c0']
        # Search left first. An interval can be discarded only if even its
        # smallest retained bias exceeds an upper envelope for its variance.
        # The probe-corrected SD need not be monotone, so binary search is unsafe.
        stack = [(1, budget)]
        first = None
        while stack:
            low, high = stack.pop()
            bias = spectral_bound(self.lam, self.omega, high, self.c['nu'], self.source['M'])
            if bias > c0*self.interval_sd_ceiling(low, high):
                continue
            if low == high:
                b, _, _, upper = self.evaluate(low)
                if b <= c0*np.sqrt(np.max(upper)):
                    first = low
                    break
                continue
            middle = (low+high)//2
            stack.extend([(middle+1, high), (low, middle)])
        ks = sorted(set([1+i*(budget-1)//100 for i in range(101)] + ([first] if first else [])))
        pairs = [(k, self.evaluate(k)) for k in ks]
        return dict(iterations=ks, bound=[float(r[0]) for _, r in pairs],
                    max_sd=[float(np.sqrt(np.max(r[3]))) for _, r in pairs],
                    first=first, c0=c0, criterion='Eq. 3.31: retained bias versus corrected SD')

    def audit(self, k, exact_variance):
        b, approximate, estimate, upper = self.evaluate(k)
        epsilon, delta = self.c['probe_epsilon'], self.c['probe_delta']
        factor = np.exp(k*np.log1p(-self.omega*self.values))
        projected = self.u @ (self.u.T @ self.truth)
        retained = float(np.linalg.norm(factor*(self.vectors.T @ projected)))
        omitted = float(np.linalg.norm(factor*(self.vectors.T @ (self.truth-projected))))
        allowance = (1+self.c['tail_eta'])*b
        bias_tol = 1e-12*max(1., float(np.linalg.norm(self.truth)))
        retained_ok = retained <= b + bias_tol
        tail_ok = omitted <= self.c['tail_eta']*retained + bias_tol
        exact_weights = covariance_eigenvalues(self.values, self.omega, k, self.c['sigma'])
        approx_weights = covariance_eigenvalues(self.lam, self.omega, k, self.c['sigma'])
        d = (self.vectors*exact_weights) @ self.vectors.T - (self.u*approx_weights) @ self.u.T
        exact_residual = exact_variance - approximate
        variance_tol = 1e-12*max(float(np.max(exact_variance)), np.finfo(float).tiny)
        dominance = upper + variance_tol >= exact_variance
        probe_event = np.abs(estimate-exact_residual) <= epsilon*np.abs(exact_residual)+variance_tol
        off_diagonal = np.maximum(0., np.sum(d*d, axis=1)-np.diag(d)**2)
        ratio = np.divide(off_diagonal, exact_residual**2, out=np.full_like(exact_residual, np.inf), where=exact_residual!=0)
        ratio[(off_diagonal<=variance_tol**2) & (np.abs(exact_residual)<=variance_tol)] = 0
        required = float(np.max(2/epsilon**2*ratio*np.log(2/delta)))
        probe_budget_sufficient = np.isfinite(required) and self.c['probes'] >= required
        issues = []
        if not retained_ok: issues.append('The retained true bias exceeds its randomized bias approximation (Assumption 3.9).')
        if not tail_ok: issues.append('Omitted bias exceeds η times retained bias (Assumption 3.9). Increase rank or justify a larger η.')
        if not np.all(dominance): issues.append('The probe-corrected variance underestimates exact variance at some pixels. Increase probes or ε.')
        # Conditional labeling relies on the directly audited realized bias and
        # variance inequalities, not on pretending a chosen probe count certifies δ.
        audit = dict(retained_bias_norm=retained, omitted_bias_norm=omitted,
                     observed_tail_ratio=omitted/retained if retained>0 else (0. if omitted==0 else None),
                     retained_bound=float(b), allowance=float(allowance), tail_eta=self.c['tail_eta'],
                     retained_pass=bool(retained_ok), tail_pass=bool(tail_ok),
                     covariance_pass=bool(np.all(dominance)), covariance_failed_pixels=int(np.count_nonzero(~dominance)),
                     probe_event_pass=bool(np.all(probe_event)),
                     required_probes=required if np.isfinite(required) else None,
                     probe_budget_sufficient=bool(probe_budget_sufficient),
                     fixed_k_reference_floor=float(self.c['level']*(1-3*np.exp(-self.c['oversampling']))*(1-delta)),
                     approximation_error=self.approx_error, approximation_beta=self.beta,
                     covariance_relative_error=float(np.linalg.norm(approximate-exact_variance)/np.linalg.norm(exact_variance)) if np.any(exact_variance) else 0.,
                     issues=issues,
                     coverage_basis='Conditional on numerically audited bias and covariance inequalities for this fixed drawing and sketch; not a general certificate for unknown images.')
        return audit, approximate, estimate, upper
