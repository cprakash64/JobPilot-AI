# People Who Can Help — five-job live validation

Date: 2026-07-27  
Reviewer labels: intentionally unassigned  
Search strategy: exact company only; no automatic retries or broadened searches

## Cohort

| Selection | Job ID | Job title | Company | Canonical domain | Run |
|---|---:|---|---|---|---:|
| Large technology company | 7363 | Software Engineer, Machine Learning Infrastructure | Stripe | stripe.com | 7 |
| Startup | 7600 | Software Engineer | Retell AI | retellai.com | 8 |
| Internship/new graduate | 7440 | Software Engineer New Grad - Software Engineer | Capital One | capitalone.com | 9 |
| Standard software engineering | 7637 | Senior Software Engineer | Okta | okta.com | 10 |
| AI/machine learning | 7624 | Machine Learning Engineer I | Abnormal Security | abnormalsecurity.com | 11 |

The normal exact-company workflow was invoked once per job. Four runs produced accepted
recommendations. Job 7624 produced a current `no_reliable_matches` result and is eligible for a
separate user-triggered broadened search; no broadened search was run.

## Aggregate funnel diagnostics

`Raw` is the provider search count, `dedup` is the unique count, `attempts` is selected for
enrichment, `matches` is successful enrichment, and `accepted` is displayed.

| Job | Category | Raw | Dedup | Attempts | Matches | Accepted | Suppression reasons |
|---:|---|---:|---:|---:|---:|---:|---|
| 7363 | Recruiter | 40 | 40 | 3 | 3 | 2 | enrichment budget exhausted: 37 |
| 7363 | Manager | 40 | 40 | 3 | 3 | 2 | enrichment budget exhausted: 37 |
| 7363 | Referral | 40 | 40 | 2 | 2 | 2 | enrichment budget exhausted: 38 |
| 7600 | Recruiter | 6 | 4 | 4 | 4 | 2 | duplicate: 2 |
| 7600 | Manager | 0 | 0 | 0 | 0 | 0 | no search results: 1 |
| 7600 | Referral | 10 | 8 | 4 | 4 | 4 | duplicate: 2; enrichment budget exhausted: 4 |
| 7440 | Recruiter | 60 | 57 | 3 | 3 | 2 | duplicate: 3; enrichment budget exhausted: 54 |
| 7440 | Manager | 40 | 40 | 3 | 3 | 2 | enrichment budget exhausted: 37 |
| 7440 | Referral | 40 | 40 | 2 | 2 | 2 | enrichment budget exhausted: 38 |
| 7637 | Recruiter | 30 | 26 | 3 | 3 | 2 | duplicate: 4; enrichment budget exhausted: 23 |
| 7637 | Manager | 40 | 40 | 3 | 3 | 2 | enrichment budget exhausted: 37 |
| 7637 | Referral | 31 | 31 | 2 | 2 | 2 | enrichment budget exhausted: 29 |
| 7624 | Recruiter | 14 | 12 | 3 | 3 | 0 | company-domain mismatch: 3; duplicate: 2; enrichment budget exhausted: 9 |
| 7624 | Manager | 29 | 29 | 3 | 3 | 0 | below relevance: 3; company-domain mismatch: 3; enrichment budget exhausted: 26 |
| 7624 | Referral | 34 | 28 | 2 | 2 | 0 | below relevance: 2; company-domain mismatch: 2; duplicate: 6; enrichment budget exhausted: 26 |

Each job consumed eight reported credits, for 40 total.

## Objective checks

- All 24 displayed records have an exact canonical-domain match to the selected hiring company.
- Displayed recruiter titles are recruiting/talent titles, manager titles are software-management
  titles, and referral titles are software or ML-infrastructure roles relevant to the job.
- Thirteen displayed records have a safe LinkedIn `/in/` link. Every displayed link is associated
  with the same Apollo identity and its slug contains a displayed-name token. The remaining eleven
  records have no safe link, so the UI does not render a LinkedIn action for them.
- Reopening every job returned the cached state. Apollo-run counts remained one per job and the
  provider factory was not constructed.
- Hunter, PDL fallback, email discovery, network matching, and broadened search remained unused.

## Human-review worksheet

Labels and notes are blank by design. A person must fill them in.

### Job 7363 — Stripe — Software Engineer, Machine Learning Infrastructure

| Category | Displayed name | Current title | Current company | Relevance | Data confidence | Reasons | Limitations | Reviewer label | Reviewer notes |
|---|---|---|---|---:|---:|---|---|---|---|
| Recruiter | Sudha Ramesh | Technical Recruiter | Stripe | 81.0 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Recruiter | Taylor Lavoy | Technical Recruiter | Stripe | 81.0 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Manager | Eileen Perry | Software Development Manager | Stripe | 72.2 | 0.900 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Manager | Gene Stanley | Software Development Manager | Stripe | 72.2 | 0.900 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Referral | Shashwat Pandey | Software Engineer - ML Infrastructure | Stripe | 71.7 | 0.900 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Aditya K | Software Engineer: ML Infrastructure | Stripe | 67.7 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |

### Job 7600 — Retell AI — Software Engineer

| Category | Displayed name | Current title | Current company | Relevance | Data confidence | Reasons | Limitations | Reviewer label | Reviewer notes |
|---|---|---|---|---:|---:|---|---|---|---|
| Recruiter | Saeed A | Senior Technical Recruiter | Retell | 78.3 | 0.650 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Recruiter | Tim Zhou | Senior Technical Recruiter \| AI | Retell | 77.3 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Manager | — | — | — | — | — | No exact-company search results | — | ___ | ___ |
| Referral | Aishwarya M | Software Engineer | Retell | 68.1 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Colin P | Software Engineer | Retell | 67.7 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Zhongren S | Senior Software Engineer | Retell | 63.3 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Fedor K | Senior Software Engineer | Retell | 62.9 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |

### Job 7440 — Capital One — Software Engineer New Grad

| Category | Displayed name | Current title | Current company | Relevance | Data confidence | Reasons | Limitations | Reviewer label | Reviewer notes |
|---|---|---|---|---:|---:|---|---|---|---|
| Recruiter | Nicole McKenna | Technical Recruiter | Capital One | 81.0 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Recruiter | Sophia Yousofy | Technical Recruiter | Capital One | 81.0 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Manager | Niveditha G | Software Development Manager | Capital One | 83.6 | 0.900 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Manager | Rajeev C | Director of Software Engineering | Capital One | 81.3 | 0.650 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Referral | Dawit Admassu | Software Engineer | Capital One | 69.7 | 0.900 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Si C | Software Engineer | Capital One | 67.7 | 0.650 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |

### Job 7637 — Okta — Senior Software Engineer

| Category | Displayed name | Current title | Current company | Relevance | Data confidence | Reasons | Limitations | Reviewer label | Reviewer notes |
|---|---|---|---|---:|---:|---|---|---|---|
| Recruiter | Manasa Kola | Senior Technical Recruiter | Okta | 82.3 | 0.900 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Recruiter | Michael A | Senior Technical Recruiter | Okta | 78.0 | 0.650 | Current at hiring company; relevant recruiting title | Specific-opening responsibility unconfirmed | ___ | ___ |
| Manager | Matt D | Senior Director of Software Engineering | Okta | 73.9 | 0.650 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Manager | Yogesh D | Software Development Manager | Okta | 72.3 | 0.650 | Current at hiring company; related managerial function | Hiring responsibility/team membership unconfirmed | ___ | ___ |
| Referral | Tannu Kumari | Senior Software Engineer | Okta | 71.7 | 0.900 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |
| Referral | Rudra Sharma | Senior Software Engineer | Okta | 71.7 | 0.900 | Current at hiring company; closely related role | Referral willingness unconfirmed | ___ | ___ |

### Job 7624 — Abnormal Security — Machine Learning Engineer I

| Category | Displayed name | Current title | Current company | Relevance | Data confidence | Reasons | Limitations | Reviewer label | Reviewer notes |
|---|---|---|---|---:|---:|---|---|---|---|
| Recruiter | — | — | — | — | — | No candidate passed exact-company gates | — | ___ | ___ |
| Manager | — | — | — | — | — | No candidate passed exact-company gates | — | ___ | ___ |
| Referral | — | — | — | — | — | No candidate passed exact-company gates | — | ___ | ___ |

## Findings

- Job-level no-result rate: 1/5 (20%).
- Empty category rate: 4/15 (26.7%): Retell manager plus all three Abnormal Security categories.
- The dominant suppression reason is the bounded enrichment budget. This occurred across all five
  jobs and reflects the configured eight-enrichment cost cap rather than a ranking rejection.
- Exact-company mismatches occurred only for Abnormal Security: all eight enriched records had a
  domain mismatch and were correctly suppressed. No displayed record had a company mismatch.
- Duplicate search records occurred in four category funnels but were removed before enrichment.
- No cross-job category-assignment defect is evident. The only repeated quality limitation is that
  eleven accepted records have abbreviated names and no safe LinkedIn link. The UI safely omits
  links for these records, but they may be less actionable during human review.
- No ranking-threshold or implementation change is recommended from this five-job sample. A larger
  labeled review could evaluate whether actionable-identity evidence should become a separate
  display requirement; that would be a product-quality policy change, not a correction justified
  by this run alone.
