FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RAYTRACER_DB=/data/raytracer.db

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 数据库存放目录（compose 中挂载为卷以持久化）
RUN mkdir -p /data

EXPOSE 8000

# 启动时 uvicorn 导入应用，startup 钩子自动建库（CREATE TABLE IF NOT EXISTS）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
