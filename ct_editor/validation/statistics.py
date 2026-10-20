"""Streaming study statistics. Trials, never pixels, are independent replicates."""
import math
import numpy as np
from ..exact.numerics import quantile


def wilson(successes, count, alpha=.05):
    if not count:
        return None, None, None
    p=np.asarray(successes,dtype=float)/count;z=quantile(alpha);a=1+z*z/count
    c=(p+z*z/(2*count))/a;w=z/a*np.sqrt(p*(1-p)/count+z*z/(4*count*count))
    return np.maximum(0,c-w),np.minimum(1,c+w),np.sqrt(p*(1-p)/count)


class Statistics:
    def __init__(self,n):
        self.count=0;self.intervals=0;self.terminal=0;self.joint=0
        self.mean=np.zeros(n);self.m2=np.zeros(n);self.sse=np.zeros(n)
        self.inclusions=np.zeros(n,dtype=np.int64);self.width=np.zeros(n)
        self.fractions=[];self.rmse=[];self.ks={};self.outcomes={}

    def add(self,status,truth,center=None,half=None,k=None):
        self.terminal+=1;self.outcomes[status]=self.outcomes.get(status,0)+1
        if center is None:return
        x=np.asarray(center);self.count+=1;d=x-self.mean;self.mean+=d/self.count
        self.m2+=d*(x-self.mean);self.sse+=(x-truth)**2
        self.rmse.append(float(np.linalg.norm(x-truth)/math.sqrt(len(x))))
        self.ks[str(k)]=self.ks.get(str(k),0)+1
        if half is None:return
        self.intervals+=1;inside=(x-half<=truth)&(truth<=x+half)
        self.inclusions+=inside;self.width+=2*half;self.joint+=int(inside.all())
        self.fractions.append(float(inside.mean()))

    def result(self,planned,alpha=.05):
        lo,hi,se=wilson(self.inclusions,self.intervals,alpha)
        arrays=dict(mean=self.mean.copy() if self.count else None,
            empirical_variance=self.m2/(self.count-1) if self.count>1 else None,
            mse=self.sse/self.count if self.count else None,
            inclusion_count=self.inclusions.copy(),coverage=self.inclusions/self.intervals if self.intervals else None,
            mc_lower=lo,mc_upper=hi,mc_se=se,mean_width=self.width/self.intervals if self.intervals else None,
            returned_and_covered=self.inclusions/self.terminal if self.terminal else None)
        summary=dict(planned=planned,started=self.terminal,terminal=self.terminal,not_started=planned-self.terminal,
            numeric_centers=self.count,returned_intervals=self.intervals,outcomes=dict(self.outcomes),selected_k_counts=dict(self.ks),
            coverage_denominator=self.intervals,operational_denominator=self.terminal,
            coverage_label='Marginal coverage among returned intervals' if self.intervals<planned else 'Marginal repeated-trial coverage',
            mean_coverage=float(np.mean(arrays['coverage'])) if self.intervals else None,
            mean_full_width=float(np.mean(arrays['mean_width'])) if self.intervals else None,
            all_pixel_inclusion=self.joint/self.intervals if self.intervals else None,
            mean_trial_rmse=float(np.mean(self.rmse)) if self.count else None,
            sqrt_mean_mse=float(np.sqrt(np.mean(arrays['mse']))) if self.count else None,
            spatial_mean_mc_se=float(np.std(self.fractions,ddof=1)/math.sqrt(self.intervals)) if self.intervals>1 else None,
            mc_reporting_level=1-alpha,mc_method='Pointwise Wilson score; descriptive for partial/adaptive or selected-return samples')
        return summary,arrays
