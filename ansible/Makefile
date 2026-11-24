.PHONY: init check apply lint

init:
	ansible-galaxy collection install ansible.posix community.crypto community.docker community.general -f

# The drift report. A host that is correct prints no `changed`.
check:
	ansible-playbook playbooks/site.yml --check --diff

apply:
	ansible-playbook playbooks/site.yml

lint:
	ansible-lint
