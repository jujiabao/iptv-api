FROM python:3.13 AS builder

# 设置时区环境变量
ENV TZ=Asia/Shanghai

ARG LITE=False

WORKDIR /app

COPY Pipfile* ./

RUN pip install pipenv \
  && PIPENV_VENV_IN_PROJECT=1 pipenv install --deploy \
  && if [ "$LITE" = False ]; then pipenv install selenium; fi

# 设置 Tsinghua 镜像源
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye main contrib non-free" > /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-backports main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-backports main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ bullseye-security main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian-security/ bullseye-security main contrib non-free" >> /etc/apt/sources.list

RUN apt-get update && apt-get install -y --no-install-recommends wget tar xz-utils

RUN mkdir /usr/bin-new \
    && ARCH=$(dpkg --print-architecture) \
    && wget -O /tmp/ffmpeg.tar.gz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${ARCH}-static.tar.xz \
    && tar -xvf /tmp/ffmpeg.tar.gz -C /usr/bin-new/

FROM python:3.13-slim

ARG APP_WORKDIR=/iptv-api
ARG LITE=False
ARG APP_PORT=8000

# 设置时区环境变量
ENV TZ=Asia/Shanghai
ENV APP_WORKDIR=$APP_WORKDIR
ENV LITE=$LITE
ENV APP_PORT=$APP_PORT
ENV PATH="/.venv/bin:$PATH"

WORKDIR $APP_WORKDIR

COPY . $APP_WORKDIR

COPY --from=builder /app/.venv /.venv

COPY --from=builder /usr/bin-new/* /usr/bin

# 设置 Tsinghua 镜像源
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye main contrib non-free" > /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-backports main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian/ bullseye-backports main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ bullseye-security main contrib non-free" >> /etc/apt/sources.list \
    && echo "deb-src https://mirrors.tuna.tsinghua.edu.cn/debian-security/ bullseye-security main contrib non-free" >> /etc/apt/sources.list

RUN apt-get update && apt-get install -y --no-install-recommends cron \
  && if [ "$LITE" = False ]; then apt-get install -y --no-install-recommends chromium chromium-driver; fi \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# 设置TZ上海时区只在python中生效，crontab调用的还是/etc/timezone中的UTC时区
RUN (crontab -l ; \
  echo "0 22 * * * cd $APP_WORKDIR && /.venv/bin/python main.py"; \
  echo "0 10 * * * cd $APP_WORKDIR && /.venv/bin/python main.py") | crontab -

EXPOSE $APP_PORT

COPY entrypoint.sh /iptv-api-entrypoint.sh

COPY config /iptv-api-config

RUN chmod +x /iptv-api-entrypoint.sh
RUN sed -i 's/\r$//' /iptv-api-entrypoint.sh

ENTRYPOINT /iptv-api-entrypoint.sh