#!/usr/bin/env python3
"""Steady fake users for the shop: RATE requests per second to /checkout. Errors and slow answers are expected during the demo."""
import os
import random
import threading
import time
import urllib.request

TARGET = os.environ.get("TARGET", "http://app:8080")
RATE = float(os.environ.get("RATE", "20"))
WORKERS = 12


def worker():
    period = WORKERS / RATE
    while True:
        started = time.time()
        try:
            urllib.request.urlopen(f"{TARGET}/checkout?user=u{random.randint(1, 500)}", timeout=5).read()
        except Exception:
            pass
        time.sleep(max(0.0, period - (time.time() - started)) * random.uniform(0.8, 1.2))


if __name__ == "__main__":
    for _ in range(WORKERS):
        threading.Thread(target=worker, daemon=True).start()
    print(f"traffic: {RATE}/s to {TARGET}", flush=True)
    while True:
        time.sleep(3600)
