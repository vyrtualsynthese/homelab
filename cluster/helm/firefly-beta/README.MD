# Repo
```bash
helm repo add firefly-iii https://firefly-iii.github.io/kubernetes
helm repo update
```

# Deploy
```bash
helm install firefly-iii firefly-iii/firefly-iii-stack
helm install firefly-iii firefly-iii/firefly-iii-stack -f values.yaml -n firefly-iii
```
