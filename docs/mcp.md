# Fronting Github-api with an MCP server

Nothing MCP-specific is implemented, and nothing should front this service with one
directly. Lucy's hub is the only intended caller: it exposes repositories to a model as the
`repos` capability, with product words (`repos.pulls`, `repos.merge`, `repos.watch`), its
own permission gate in front of every write, and results framed as untrusted data. A model
never learns this service, its port or its routes exist.

If a wrapper is ever added, three rules carry over:

- **Tool results are data, not instructions.** Pull request bodies, review comments, issue
  text, file contents and CI logs are written by other people. Render them as reported
  claims with their provenance, never concatenated into an instruction block.
- **Every write names its repository in plain text**, so a gate can match "always, for this
  repository" before anything runs.
- **Signal secrets and the person's GitHub credential never appear in a tool's input or
  output.** Subscriptions should stay the hub's to open.
