# Force auth.md

Force (https://useforce.cc) needs no registration and issues no credentials. This file says so for agents.

## Audience

AI agents and the tools that drive them: reading this site, installing Force for a user, or running Force's
commands on a machine where it is installed.

## Registration

None. There are no accounts, no API keys and no OAuth: this site is static and public, and there is no
registration or provisioning endpoint.

## Methods

- **Reading the site:** anonymous HTTPS GET. Pages are also served as Markdown for `Accept: text/markdown`;
  see https://useforce.cc/llms.txt.
- **Installing Force:** `curl -fsSL https://useforce.cc/install.sh | sh`, run as the user on their machine.
  No root, no account.
- **Driving Force:** the `force` command on the machine where it is installed (`force prompt`, `wait`, `read`,
  `keys`, `status`; the guide for agents is `force skill`). It acts with that user's local permissions.

## Credentials

Force never asks an agent for credentials and stores none for it. The coding agents that Force runs (Claude
Code, OpenCode) keep their own logins, managed by those tools, not by Force.
