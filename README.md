# devinhackathon

Failure-to-Env: turn browser-agent failures into resettable training environments.

- [Vision](vision.md) and [implementation plan](PLAN.md)
- [Connected A → B → C run: setup, validation, and current limitations](docs/integration.md)
- [Implementation log](docs/devlog.md)
- [Remaining work and acceptance gates](docs/remaining-work.md)
- [Browser demo / LLM experiment runbook](docs/demo-runbook.md)

The integrated toolshop demo uses A's browser strategies, B's session-scoped
environment, and C's rollout/memory loop. The scripted browser rehearsal has
been verified through a real-site cart mutation. Live LLM generation and
learning still need a credentialed run.
