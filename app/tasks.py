from celery import shared_task
import time


@shared_task
def sample_task(x, z):
    time.sleep(25)
    return x + z
