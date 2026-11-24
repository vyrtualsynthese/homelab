.PHONY: init check apply lint

# Pinned, and the pins are the point: `community.general` 14 needs ansible-core
# 2.19 and the hosts' controller here is 2.18, so an unpinned install prints a
# support warning and then uses a collection built against a newer core. Bump a
# range only after `ansible --version` on the machine that runs this.
init:
	ansible-galaxy collection install "ansible.posix:>=2.0.0,<3.0.0" -f
	ansible-galaxy collection install "community.crypto:>=3.0.0,<4.0.0" -f
	ansible-galaxy collection install "community.docker:>=5.0.0,<6.0.0" -f
	ansible-galaxy collection install "community.general:>=13.0.0,<14.0.0" -f

# The drift report. A host that is correct prints no `changed`.
check:
	ansible-playbook playbooks/site.yml --check --diff

apply:
	ansible-playbook playbooks/site.yml

lint:
	ansible-lint
