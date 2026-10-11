ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
RUN chmod -R a+rX /usr/share/nginx/html && chmod a+r /etc/nginx/conf.d/default.conf
