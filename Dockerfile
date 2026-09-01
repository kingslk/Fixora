# Fixora API、Worker、Web 仍在 macOS 本机运行；此镜像只提供 PostgreSQL 和 Redis。
FROM postgres:16-bookworm

USER root

# Apple Container 命名卷可能包含 lost+found；使用子目录避免 initdb 拒绝初始化。
ENV PGDATA=/var/lib/postgresql/data/pgdata

RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-server \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 5432 6380

# PostgreSQL 沿用官方 entrypoint；Redis 队列无需持久卷，监听容器端口 6380。
CMD ["sh", "-c", "redis-server --daemonize yes --bind 0.0.0.0 --protected-mode no --port 6380 && exec docker-entrypoint.sh postgres"]
