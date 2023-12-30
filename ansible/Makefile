init:
	ansible-galaxy collection install ansible.posix -f
	ansible-galaxy collection install community.crypto -f
	ansible-galaxy collection install community.docker -f
	ansible-galaxy collection install community.general -f

setup-node:
	ansible-playbook playbooks/setup-node.yaml -kK

upgrade-all:
	ansible-playbook playbooks/upgrade-all.yaml

backup-setup:
	ansible-playbook playbooks/backup-setup.yaml
