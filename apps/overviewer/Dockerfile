FROM ubuntu:22.04 AS builder

WORKDIR /app/overviewer

RUN apt update -y && apt install python3 wget build-essential python3-dev python3-pil python3-numpy python3-dev zip unzip -y

RUN wget https://github.com/overviewer/Minecraft-Overviewer/archive/refs/heads/master.zip
RUN unzip master -d /app/overviewer/src

WORKDIR /app/overviewer/src/Minecraft-Overviewer-master

RUN python3 setup.py build

RUN mkdir -p ~/.minecraft/versions/1.19/ && wget https://overviewer.org/textures/1.19 -O ~/.minecraft/versions/1.19/1.19.jar

COPY config .

CMD ["./overviewer.py", "--config=config"]
