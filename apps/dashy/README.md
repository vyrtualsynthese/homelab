# Dashy project
This is a simple project to generate a static version of dashy using docker and serving it with nging server.  
Image is multi architecture.

```
docker build --rm --target dev -t home-dashy:dev .
docker run -it -v ${PWD}/conf.yml:/app/public/conf.yml -p 8080:80 home-dashy:dev
```
