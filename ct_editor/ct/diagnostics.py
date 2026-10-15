"""Validation only. These values cannot control the solver."""
import numpy as np
from .solver import frozen_array

class ImageDiagnostics:
    def __init__(self, truth):
        self.truth=frozen_array(truth).ravel();self.norm=float(np.linalg.norm(self.truth));self.history=[]
    def record(self,k,x):
        error=float(np.linalg.norm(x-self.truth))
        self.history.append(dict(iteration=k,error_norm=error,rmse=error/np.sqrt(x.size),
            relative_error=error/self.norm if self.norm else None,
            relative_error_reason=None if self.norm else 'zero_truth_norm'))
