"""Sample FastAPI app for testing py_stack_dump.

Run: uv run python examples/fastapi_app.py
This launches a server on :8000 with a background worker thread,
providing a multi-threaded target process to inspect with py_stack_dump.
"""

import threading
import time
from contextlib import asynccontextmanager

import numpy as np
import uvicorn
from fastapi import FastAPI


def matrix_worker():
    while True:
        a = np.random.rand(100, 100)
        b = np.random.rand(100, 100)
        c = a @ b
        time.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=matrix_worker, daemon=True, name="matrix-worker")
    t.start()
    yield
    t.join(timeout=1)


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
