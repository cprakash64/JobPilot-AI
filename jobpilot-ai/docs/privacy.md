# Privacy

Users can export account data as JSON and delete their account. Optional demographic information can be deleted separately.

JobPilot AI stores normal career profile data separately from sensitive demographic data. Sensitive demographic fields are optional, default to "Prefer not to answer", and are not used for job-fit scoring, ranking, resume generation, or cover-letter generation.

Generated documents are saved per job so users can review what was created and decide what to use. The product is designed around user review and manual submission.

Before production deployment, configure strong secret management, database backups, encryption at rest, log retention, and a malware scanning implementation for uploads.

