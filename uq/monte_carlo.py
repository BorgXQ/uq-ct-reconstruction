"""Independent measurement-space replicates passed through the fixed T_K."""
import numpy as np
from .covariance import finite_filter


def validate(a, values, vectors, truth, mean, variance, omega, k, sigma,
             trials, seed, radius=None, joint=None, eta=None):
    rng = np.random.default_rng(seed)
    f = finite_filter(values, omega, k)
    # Draw measurement-space noise, not independent image pixels. Batched exact
    # spectral T_K action retains spatial correlations and all spectral modes.
    draws = []
    for start in range(0, trials, 32):
        noise = rng.normal(0, sigma, (min(32, trials-start), a.shape[0])).T
        propagated = vectors @ (f[:, None] * (vectors.T @ (a.T @ noise)))
        draws.append((mean[:, None] + propagated).T)
    samples = np.concatenate(draws)
    empirical_mean = samples.mean(axis=0)
    empirical_var = samples.var(axis=0, ddof=1)
    errors = samples - truth
    mse_trials = np.sum(errors**2, axis=1)
    predicted_mse = float(np.sum((mean-truth)**2) + variance.sum())
    result = dict(trials=trials, seed=seed, empirical_mean=empirical_mean,
                  empirical_variance=empirical_var, empirical_bias=empirical_mean-truth,
                  mean_error_norm=float(np.linalg.norm(empirical_mean-mean)),
                  bias_error_norm=float(np.linalg.norm(empirical_mean-mean)),
                  variance_relative_error=float(np.linalg.norm(empirical_var-variance)/np.linalg.norm(variance)) if np.any(variance) else None,
                  observed_mse=float(mse_trials.mean()), predicted_mse=predicted_mse,
                  mse_se=float(mse_trials.std(ddof=1)/np.sqrt(trials)),
                  mean_rmse_prediction=float(np.sqrt(variance.sum()/trials)), coverage=None, joint_coverage=None)
    if radius is not None:
        hits = np.abs(errors) <= radius
        coverage = hits.mean(axis=0)
        # Pixel events in a single image are correlated: average across pixels
        # inside each trial before estimating SE of the average coverage.
        average_per_trial = hits.mean(axis=1)
        result['coverage'] = dict(pixel=coverage, pixel_se=np.sqrt(coverage*(1-coverage)/trials),
                                  average=float(coverage.mean()),
                                  average_se=float(average_per_trial.std(ddof=1)/np.sqrt(trials)))
    if joint and joint['available']:
        stats = np.sum((errors @ vectors)**2 / eta, axis=1)
        rate = float(np.mean(stats <= joint['threshold']))
        result['joint_coverage'] = dict(rate=rate, se=float(np.sqrt(rate*(1-rate)/trials)))
    return result
