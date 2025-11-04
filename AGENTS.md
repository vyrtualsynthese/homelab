# Repository Guidelines

## Project Structure & Module Organization
The repo is split by toolchain. `ansible/` hosts playbooks under `playbooks/`, reusable Jinja templates, and a `Makefile` orchestrating installs. Kubernetes manifests live in `cluster/`: `helm/` is organized by app (e.g., `cluster/helm/monitoring`), `argocd/` tracks GitOps configs, and `infrastructure/` captures base cluster services. Utility manifests that can be applied ad hoc go in `kubectl/`, including the smoke-test deployment at `kubectl/test-pod/TestDeployment.yaml`. Keep new assets grouped alongside the tool they belong to.

## Build, Test, and Development Commands
Bootstrap Ansible collections with `make init` from `ansible/`. Use `make setup-node` for first-time host prep, `make upgrade-all` to patch managed nodes, and `make backup-setup` to install restic jobs. When iterating Helm charts, run `helm template <chart-dir> --values values.yaml` from inside the chart folder to lint syntax, then `kubectl apply --dry-run=client -f <file>` for raw manifests.

## Coding Style & Naming Conventions
YAML files follow two-space indentation and lower-case keys; keep arrays aligned and quote strings with special characters. Chart directories and playbooks use hyphenated names (`setup-node.yaml`). Place secrets in encrypted vaults or external stores—never plain text. Shell snippets should lean on POSIX sh for portability unless Kubernetes tooling demands otherwise.

## Testing Guidelines
Validate Ansible changes with `ansible-playbook playbooks/<file>.yaml --check --diff` and target the specific inventory group when possible. For Kubernetes updates, render charts locally (`helm lint` / `helm template`) and run `kubectl --context <cluster> apply --dry-run=server -f kubectl/test-pod/TestDeployment.yaml` to confirm cluster connectivity. Aim to keep smoke tests green before pushing; raise explicit TODOs when automated coverage is not feasible.

## Commit & Pull Request Guidelines
Commits typically use a gitmoji prefix plus a concise imperative summary (`♻️ Refactor authentik ArgoCD sync`). Squash noisy work-in-progress commits before opening a PR. Each PR should describe the change, reference related issue IDs or runbooks, and include any screenshots or command transcripts that prove deployments succeed. Tag reviewers responsible for the affected stack (e.g., networking vs. apps) and note post-merge tasks such as manual syncs or credential rotations.
