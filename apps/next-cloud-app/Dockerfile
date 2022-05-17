FROM nextcloud:fpm-alpine

RUN mkdir /data; \
    chown -R www-data:root /data; \
    chmod -R g=u /data;

COPY php.ini-production /usr/local/etc/php/php.ini-production
COPY www.conf /usr/local/etc/php-fpm.d/www.conf
