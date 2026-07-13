# Compliance

JobPilot AI is designed as an assistant, not an automation bot.

The platform must not:

- Scrape restricted platforms such as LinkedIn, Indeed, or portals that prohibit scraping or automation.
- Automate login to third-party job portals.
- Submit applications automatically except through approved official APIs or a clearly user-controlled flow.
- Implement fake typing delays, mouse movement simulation, captcha bypass, proxy rotation, or stealth browser automation.
- Generate false resume content.

Allowed ingestion starts with public structured sources such as Greenhouse public boards, Lever public postings, and configurable company career pages that expose structured data.

Every generated resume must be grounded in user-supplied facts. Unsupported claims are flagged by the hallucination checker.

