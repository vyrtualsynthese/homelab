docker build --rm -t overviewer .
docker run -it --rm -v ${PWD}/world:/app/overviewer/src/Minecraft-Overviewer-master/world/world -v ${PWD}/render:/app/overviewer/src/Minecraft-Overviewer-master/render overviewer
