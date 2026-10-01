# ADRs

Decisions specific to Github-api belong here. Family decisions stay in the meta-repo's
`docs/adr/` -- in particular [ADR-0015](https://github.com/tochi-mba/LUCY-assistant/blob/main/docs/adr/0015-jobs-and-signals.md)
(jobs and signals) and [ADR-0016](https://github.com/tochi-mba/LUCY-assistant/blob/main/docs/adr/0016-repos-capability-and-port-8011.md)
(the `repos` capability, this service, port 8011).

Decisions made while building the service, recorded where they are explained:

- How subscriptions are watched without storing a credential -- [jobs.md](../jobs.md#who-looks-and-under-whose-credential).
- GraphQL for multi-part reads, REST for writes -- [architecture.md](../architecture.md#reads-are-graphql-where-it-saves-calls).
