.PHONY: init check apply lint adopt

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

# Print the line `trusted_ssh_keys` wants for this laptop: the public key, proved
# against the private half beside it, and its fingerprint. It never edits
# group_vars — a reviewed file keeps its comments — and it never touches the
# network, so it runs on a laptop that cannot reach a host yet.
KEY ?= $(HOME)/.ssh/homelab_live.pub
# Exported so the recipe's shell can strip `.pub` from it: make passes only
# command-line and environment variables to a recipe, so without this a bare
# `make adopt` derives an empty private path and blames the wrong file.
export KEY
adopt:
	@test -f "$(KEY)" || { echo "adopt: $(KEY) is missing — ssh-keygen -t ed25519 -f $(KEY) creates it" >&2; exit 1; }
	@priv=$${KEY%.pub}; \
	 case "$$(cat "$(KEY)")" in \
	   ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-nistp256\ *|sk-ssh-ed25519@openssh.com\ *) ;; \
	   *) echo "adopt: $(KEY) is not an OpenSSH public key" >&2; exit 1 ;; \
	 esac; \
	 test -f "$$priv" || { echo "adopt: $$priv is missing; the public half proves nothing on its own" >&2; exit 1; }; \
	 derived=$$(ssh-keygen -y -P '' -f "$$priv" 2>/dev/null) \
	   || { echo "adopt: $$priv cannot be read without a passphrase" >&2; exit 1; }; \
	 test "$$(printf '%s\n' "$$derived" | cut -d' ' -f1,2)" = "$$(cut -d' ' -f1,2 "$(KEY)")" \
	   || { echo "adopt: $(KEY) does not match $$priv" >&2; exit 1; }; \
	 printf '  - "%s"\n' "$$(sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' "$(KEY)")"; \
	 printf '# fingerprint %s\n' "$$(ssh-keygen -lf "$(KEY)" | cut -d' ' -f2)"; \
	 printf '# add that line under trusted_ssh_keys in inventory/group_vars/all.yml,\n# then: ansible-playbook playbooks/site.yml --check --diff --limit nas\n'
