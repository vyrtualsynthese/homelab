# Repo
```bash
helm repo add self-hosted https://vyrtualsynthese.github.io/selfhosted-helmcharts/
helm repo update
```

# Deploy
```bash
helm upgrade dashy selfhosted-helmcharts/dashy -f values.yaml  --set-file static.configMapContent.conf=secret.yml -n dashy
```
