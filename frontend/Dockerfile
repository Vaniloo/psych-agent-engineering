FROM nginx:1.27-alpine
COPY index.html styles.css *.js /usr/share/nginx/html/
COPY vendor/ /usr/share/nginx/html/vendor/
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
