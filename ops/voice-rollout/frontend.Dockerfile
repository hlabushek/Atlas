# Собранный и проверенный локально интерфейс; npm не запускается на VPS.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
