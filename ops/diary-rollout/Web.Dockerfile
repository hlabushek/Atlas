FROM atlas-web:pre-diary-20261004
COPY frontend/dist/ /usr/share/nginx/html/
RUN chmod -R a+rX /usr/share/nginx/html
