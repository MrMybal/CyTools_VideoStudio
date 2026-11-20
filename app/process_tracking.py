"""Register a spawned process with the Core task's artifact lifetime."""
import psutil


def track(context, child):
    try:
        process = psutil.Process(child.pid)
        entry = {'pid': process.pid, 'created': process.create_time()}
    except psutil.NoSuchProcess:
        return
    runtime = context.runtime
    with runtime._cv:
        job = runtime._jobs[context.job_id]
        job.setdefault('artifactProcesses', []).append(entry)
        runtime._save(job)
