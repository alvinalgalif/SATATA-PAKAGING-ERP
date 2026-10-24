FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ERP_ENV=production ERP_BIND=0.0.0.0 PORT=8080 ERP_DB=/var/lib/satata/erp.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt && groupadd --gid 10001 erp && useradd --uid 10001 --gid erp --no-create-home erp && mkdir -p /var/lib/satata && chown erp:erp /var/lib/satata
COPY server.py security.py webapp.py maintenance.py ./
COPY dist/ ./dist/
USER 10001:10001
VOLUME ["/var/lib/satata"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"
CMD ["python", "webapp.py"]
