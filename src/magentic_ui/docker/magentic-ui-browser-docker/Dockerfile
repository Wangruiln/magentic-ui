FROM node:lts-slim

# 避免apt提示
ENV DEBIAN_FRONTEND=noninteractive
ENV PW_CHROMIUM_NO_SANDBOX=0
ENV CHROME_DEVEL_SANDBOX=/usr/local/sbin/chrome-devel-sandbox
ENV SUPERVISOR_LOGFILE="/app/logs/supervisord.log"
ENV SUPERVISOR_PIDFILE="/app/logs/supervisord.pid"

# 创建非root用户
RUN groupadd -r pwuser && useradd -r -g pwuser -d /home/pwuser -s /sbin/nologin -c "pwuser" pwuser \
    && mkdir -p /home/pwuser && chown -R pwuser:pwuser /home/pwuser

# 安装依赖
RUN apt-get update && apt-get install -y \
    git \
    xvfb \
    x11vnc \
    openbox \
    supervisor \
    python3 \
    python3-pip \
    novnc \
    websockify \
    procps \
    xdg-utils \
    python3-xdg \
    x11-xserver-utils \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 安装noVNC
RUN git clone --depth 1 --branch v1.6.0 https://github.com/novnc/noVNC.git /usr/local/novnc \
    && git clone --depth 1 --branch v0.13.0 https://github.com/novnc/websockify /usr/local/novnc/utils/websockify

RUN echo "kernel.apparmor_restrict_unprivileged_userns=0" > /etc/sysctl.d/60-apparmor-namespace.conf \
    && echo "kernel.unprivileged_userns_clone=1" > /etc/sysctl.d/90-unprivileged-userns.conf

# 设置工作目录
WORKDIR /app

# 安装Playwright（使用root用户）
COPY package.json package-lock.json* ./
RUN npm install .

# 安装浏览器及其依赖（使用root用户）
RUN npx playwright@1.53 install --with-deps chromium

# 配置沙箱权限
RUN chown root:root /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome_sandbox \
    && chmod 4755 /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome_sandbox \
    && cp -p /root/.cache/ms-playwright/chromium-*/chrome-linux/chrome_sandbox /usr/local/sbin/chrome-devel-sandbox

# 创建非root用户的Playwright缓存目录
RUN mkdir -p /home/pwuser/.cache/ms-playwright \
    && cp -r /root/.cache/ms-playwright/* /home/pwuser/.cache/ms-playwright/ \
    && chown -R pwuser:pwuser /home/pwuser/.cache

RUN mkdir -p /app/logs /var/log/supervisor /var/run/supervisor \
    && chown -R pwuser:pwuser /app /var/log/supervisor /var/run/supervisor
# 设置supervisord配置
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 复制脚本
COPY start.sh /app/start.sh
COPY playwright-server.js /app/playwright-server.js
COPY x11-setup.sh /app/x11-setup.sh

# 使脚本可执行
RUN chmod +x /app/start.sh /app/x11-setup.sh

# 创建openbox配置
RUN mkdir -p /home/pwuser/.config/openbox \
    && chown -R pwuser:pwuser /home/pwuser/.config
COPY openbox-rc.xml /home/pwuser/.config/openbox/rc.xml

RUN mkdir -p /workspace && chown -R pwuser:pwuser /workspace
# 切换到非root用户
USER pwuser

# 暴露端口：noVNC Web界面和Playwright服务器
EXPOSE 6080 37367

ENV PLAYWRIGHT_WS_PATH="default"
ENV HOME=/home/pwuser

# 设置显示环境变量
ENV DISPLAY=:99

WORKDIR /workspace

COPY --chown=pwuser:pwuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

LABEL org.opencontainers.image.source=https://github.com/microsoft/magentic-ui
LABEL org.opencontainers.image.description="Magentic UI Browser Docker container with Playwright and noVNC"
LABEL org.opencontainers.image.licenses=MIT

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# 启动服务
CMD ["/app/start.sh"]