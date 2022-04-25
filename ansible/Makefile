init:
	ansible-galaxy collection install ansible.posix
	ansible-galaxy collection install community.crypto

setup-node:
	ansible-playbook playbooks/setup-node.yaml -kK

upgrade-all:
	ansible-playbook playbooks/upgrade-all.yaml

backup-setup:
	ansible-playbook playbooks/backup-setup.yaml
