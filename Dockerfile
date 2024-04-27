FROM python:3.11

RUN mkdir -p /app
WORKDIR /app
RUN git clone https://github.com/heiparta/cbase2influxdb.git build
RUN pip wheel ./build
RUN pip install cbase2influxdb-*.whl

CMD ["cbase2influxdb", "/app/config.yaml"]
