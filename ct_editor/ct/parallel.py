"""BLAS parallelism for the app's single numerical worker."""
import os
from threadpoolctl import threadpool_limits


def available_cpu_count():
    """Use logical CPUs available to this process, respecting CPU affinity."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def numerical_threads():
    """Allow BLAS/LAPACK to use all available CPUs during numerical work.

    This does not create concurrent jobs or parallelize Python/sparse loops.
    The context restores the previous BLAS settings on completion or error.
    """
    return threadpool_limits(limits=available_cpu_count(), user_api='blas')
