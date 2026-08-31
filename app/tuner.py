from threading import Lock

_lock = Lock()

# We'll support multiple tuners later.
# For now we have one logical recorder.

busy = False


def acquire():
    global busy

    with _lock:
        if busy:
            return False

        busy = True
        return True


def release():
    global busy

    with _lock:
        busy = False


def is_busy():
    return busy