FROM python:3.12.13-slim

# The worker is supplied by the API as a read-only file, never baked with secrets.
RUN python -m pip install --no-cache-dir numpy==2.5.2 pandas==2.3.3 scipy==1.18.1
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 65534:65534
WORKDIR /work
