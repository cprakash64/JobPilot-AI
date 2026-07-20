/** Local ATS-like form fixtures for unit tests. No live sites are ever used. */

export const GREENHOUSE_FIXTURE = `
  <div id="grnhse_app">
    <form id="application_form">
      <label for="first_name">First Name *</label>
      <input id="first_name" name="job_application[first_name]" type="text" required />

      <label for="last_name">Last Name *</label>
      <input id="last_name" name="job_application[last_name]" type="text" required />

      <label for="email">Email *</label>
      <input id="email" name="job_application[email]" type="email" autocomplete="email" required />

      <label for="phone">Phone</label>
      <input id="phone" name="job_application[phone]" type="tel" />

      <label for="linkedin">LinkedIn Profile</label>
      <input id="linkedin" name="job_application[linkedin]" type="url" />

      <label for="resume">Resume/CV *</label>
      <input id="resume" name="job_application[resume]" type="file" required />

      <label for="cover">Cover Letter</label>
      <input id="cover" name="job_application[cover_letter]" type="file" />

      <label for="work_auth">Are you legally authorized to work in the US?</label>
      <select id="work_auth" name="job_application[work_authorization]">
        <option value="">Select...</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>

      <label for="why">Why do you want to work here?</label>
      <textarea id="why" name="job_application[why]"></textarea>

      <fieldset>
        <legend>Voluntary Self-Identification</legend>
        <label for="gender">Gender</label>
        <select id="gender" name="job_application[gender]">
          <option value="">Decline to self-identify</option>
          <option value="f">Female</option>
          <option value="m">Male</option>
        </select>
      </fieldset>

      <input type="text" name="honeypot_leave_blank" style="left:-9999px" />
      <input id="disabled_field" name="disabled_field" type="text" disabled />

      <button id="submit_app" type="submit">Submit Application</button>
    </form>
  </div>
`;

export const LEVER_FIXTURE = `
  <form class="application-form" data-qa="application-form">
    <input name="name" placeholder="Full name" type="text" autocomplete="name" />
    <input name="email" placeholder="Email" type="email" />
    <input name="phone" placeholder="Phone" type="tel" />
    <input name="urls[LinkedIn]" placeholder="LinkedIn URL" type="url" />
    <input name="resume" type="file" />
    <textarea name="comments" placeholder="Additional information"></textarea>
    <button class="template-btn-submit" type="submit">Submit application</button>
  </form>
`;

export const ASHBY_FIXTURE = `
  <div class="ashby-application-form-container">
    <form class="_form_abc">
      <label for="name">Name</label>
      <input id="name" name="name" type="text" />
      <label for="email">Email</label>
      <input id="email" name="email" type="email" />
      <label for="school">Which school did you attend?</label>
      <input id="school" name="education" type="text" />
      <button type="submit">Submit</button>
    </form>
  </div>
`;

export const GENERIC_FIXTURE = `
  <form>
    <label for="fullname">Your name</label>
    <input id="fullname" name="fullname" type="text" />
    <label for="mail">E-mail address</label>
    <input id="mail" name="mail" type="text" />
    <label for="city">City</label>
    <input id="city" name="city" type="text" />
    <button type="submit">Apply</button>
  </form>
`;

export const EEO_FIXTURE = `
  <form>
    <label for="veteran">Protected Veteran Status</label>
    <select id="veteran" name="veteran"><option value="">Choose</option><option>I am a veteran</option></select>
    <label for="disability">Do you have a disability?</label>
    <select id="disability" name="disability"><option value="">Choose</option><option>Yes</option></select>
    <label for="criminal">Have you ever been convicted of a felony?</label>
    <input id="criminal" name="criminal" type="text" />
    <label for="salhist">What is your current salary?</label>
    <input id="salhist" name="salary_history" type="text" />
  </form>
`;

/**
 * Sanitized approximation of the CURRENT (job-boards.greenhouse.io) React form
 * used by the Affirm reproduction: namespaced job_application[…] names, a custom
 * Country combobox (not a native select), and file inputs hidden behind "Attach"
 * buttons. No live markup, no PII. Used to test the new-form path end to end.
 */
export const AFFIRM_GREENHOUSE_FIXTURE = `
  <main>
    <script src="https://boards.greenhouse.io/embed/job_board/js"></script>
    <form id="application-form" aria-label="Application for Software Engineer">
      <div class="field">
        <label for="first_name">First Name *</label>
        <input id="first_name" name="job_application[first_name]" type="text" required />
      </div>
      <div class="field">
        <label for="last_name">Last Name *</label>
        <input id="last_name" name="job_application[last_name]" type="text" required />
      </div>
      <div class="field">
        <label for="email">Email *</label>
        <input id="email" name="job_application[email]" type="email" autocomplete="email" required />
      </div>
      <div class="field">
        <label for="phone">Phone *</label>
        <input id="phone" name="job_application[phone]" type="tel" autocomplete="tel" required />
      </div>
      <div class="field">
        <label id="country-label" for="country">Country</label>
        <input id="country" name="job_application[country]" role="combobox"
               aria-labelledby="country-label" aria-autocomplete="list"
               aria-controls="country-listbox" aria-expanded="false" type="text" />
        <ul id="country-listbox" role="listbox"></ul>
      </div>
      <div class="field">
        <label for="linkedin">LinkedIn Profile</label>
        <input id="linkedin" name="job_application[linkedin]" type="url" />
      </div>
      <div class="field">
        <label for="website">Website</label>
        <input id="website" name="job_application[website]" type="url" />
      </div>

      <div class="field" data-field="resume">
        <label id="resume-label">Resume/CV *</label>
        <button type="button" aria-describedby="resume-label">Attach</button>
        <input id="resume-input" name="job_application[resume]" type="file"
               aria-label="Attach Resume/CV" style="display:none" />
      </div>
      <div class="field" data-field="cover_letter">
        <label id="cover-label">Cover Letter</label>
        <button type="button" aria-describedby="cover-label">Attach</button>
        <input id="cover-input" name="job_application[cover_letter]" type="file"
               aria-label="Attach Cover Letter" style="display:none" />
      </div>

      <div class="field">
        <label for="q_auth">Are you legally authorized to work in the US? *</label>
        <select id="q_auth" name="job_application[answers][auth]">
          <option value="">Select...</option>
          <option value="Yes">Yes</option>
          <option value="No">No</option>
        </select>
      </div>
      <div class="field">
        <label for="q_why">Why do you want to work at Affirm?</label>
        <textarea id="q_why" name="job_application[answers][why]"></textarea>
      </div>

      <button id="submit_app" type="submit">Submit Application</button>
    </form>
  </main>
`;

export const MULTISTEP_STEP2_FIXTURE = `
  <form>
    <h2>Step 2 of 3: Experience</h2>
    <label for="company">Current Company</label>
    <input id="company" name="company" type="text" />
    <label for="title">Current Title</label>
    <input id="title" name="title" type="text" />
    <label for="years">Years of experience</label>
    <input id="years" name="years" type="number" />
    <button type="button">Back</button>
    <button type="button">Next</button>
  </form>
`;

/**
 * Sanitized approximation of Temporal's live Ashby application form
 * (jobs.ashbyhq.com/temporal/…/application). Field names/labels mirror the real
 * page structure; no personal data. Covers the fields the reproduction listed:
 * name, email, phone, resume + cover-letter uploads, LinkedIn, website, plus a
 * screening radio, a sensitive demographic select, and a legal attestation.
 */
export const TEMPORAL_ASHBY_FIXTURE = `
  <div class="ashby-application-form-container">
    <form class="_form_xyz1" aria-label="Application">
      <div class="_fieldEntry_abc">
        <label for="_systemfield_name">Name<span aria-hidden="true">*</span></label>
        <input id="_systemfield_name" name="_systemfield_name" type="text" autocomplete="name" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_email">Email<span aria-hidden="true">*</span></label>
        <input id="_systemfield_email" name="_systemfield_email" type="email" autocomplete="email" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_phone">Phone</label>
        <input id="_systemfield_phone" name="_systemfield_phone" type="tel" autocomplete="tel" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="_systemfield_resume">Resume<span aria-hidden="true">*</span></label>
        <input id="_systemfield_resume" name="_systemfield_resume" type="file" accept=".pdf,.doc,.docx" required />
      </div>
      <div class="_fieldEntry_abc">
        <label for="cover_letter">Cover letter</label>
        <input id="cover_letter" name="cover_letter" type="file" accept=".pdf,.doc,.docx" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="q_linkedin">LinkedIn Profile</label>
        <input id="q_linkedin" name="q_linkedin" type="url" placeholder="https://linkedin.com/in/…" />
      </div>
      <div class="_fieldEntry_abc">
        <label for="q_website">Website</label>
        <input id="q_website" name="q_website" type="url" placeholder="https://" />
      </div>
      <fieldset class="_fieldEntry_abc">
        <legend>Are you legally authorized to work in the United States?</legend>
        <label><input type="radio" name="q_work_auth" value="Yes" /> Yes</label>
        <label><input type="radio" name="q_work_auth" value="No" /> No</label>
      </fieldset>
      <fieldset class="_fieldEntry_abc">
        <legend>Voluntary Self-Identification — Gender</legend>
        <select id="q_gender" name="q_gender">
          <option value="">Decline to self-identify</option>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="nonbinary">Non-binary</option>
        </select>
      </fieldset>
      <div class="_fieldEntry_abc">
        <label><input type="checkbox" id="q_attest" name="q_attest" /> I certify that the information provided is accurate.</label>
      </div>
      <!-- Ashby honeypot / hidden anti-spam field -->
      <input type="text" name="_hp_email" tabindex="-1" style="position:absolute;left:-9999px" />
      <button type="submit" class="_submitButton_xyz">Submit Application</button>
    </form>
  </div>
`;

/**
 * Sanitized approximation of an employer-hosted careers-site application form
 * (e.g. MongoDB Careers), on a domain with no dedicated ATS adapter — the
 * generic HTML-form adapter must handle it. Covers preferred name, website,
 * country as a native select, and distinct resume/cover-letter uploads.
 */
export const CUSTOM_EMPLOYER_FIXTURE = `
  <form id="careers-application" aria-label="Apply for Software Engineer">
    <div class="field">
      <label for="firstName">First Name</label>
      <input id="firstName" name="firstName" type="text" autocomplete="given-name" required />
    </div>
    <div class="field">
      <label for="lastName">Last Name</label>
      <input id="lastName" name="lastName" type="text" autocomplete="family-name" required />
    </div>
    <div class="field">
      <label for="preferredName">Preferred Name</label>
      <input id="preferredName" name="preferredName" type="text" />
    </div>
    <div class="field">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="email" required />
    </div>
    <div class="field">
      <label for="phone">Phone</label>
      <input id="phone" name="phone" type="tel" autocomplete="tel" />
    </div>
    <div class="field">
      <label for="country">Country</label>
      <select id="country" name="country">
        <option value="">Select...</option>
        <option value="US">United States</option>
        <option value="IN">India</option>
      </select>
    </div>
    <div class="field">
      <label for="city">Location (City)</label>
      <input id="city" name="city" type="text" autocomplete="address-level2" />
    </div>
    <div class="field">
      <label for="website">Website</label>
      <input id="website" name="website" type="url" />
    </div>
    <div class="field">
      <label for="linkedin">LinkedIn Profile</label>
      <input id="linkedin" name="linkedin" type="url" />
    </div>
    <div class="field">
      <label for="resume">Resume/CV</label>
      <input id="resume" name="resume" type="file" required />
    </div>
    <div class="field">
      <label for="coverLetter">Cover Letter</label>
      <input id="coverLetter" name="coverLetter" type="file" />
    </div>
    <button type="submit">Submit Application</button>
  </form>
`;

/** Same form, rendered inside an iframe document — the top page has no fields
 * of its own (the common "employer wraps an ATS embed" shape). */
export const CUSTOM_EMPLOYER_WRAPPER_FIXTURE = `
  <header><h1>Careers at Acme</h1></header>
  <iframe id="ats-embed" title="Application form"></iframe>
`;

/**
 * Manual fixture (task section L): an Affirm/Greenhouse-style application
 * form covering every dropdown shape the review-widget + dropdown-adapter
 * work needs to handle end to end — native selects, an ARIA combobox for the
 * one logically distinct "future sponsorship" question, and the full set of
 * company-specific / demographic questions. Chandra Prakash / Pandey is used
 * ONLY as illustrative session data in tests, never hardcoded into product
 * logic (see mapping.ts / answer_vault_service.py, which are name-agnostic).
 */
export const DROPDOWN_FIXTURE = `
  <form id="application-form" aria-label="Application for Backend Engineer">
    <div class="field"><label for="firstName">First Name</label>
      <input id="firstName" name="firstName" type="text" autocomplete="given-name" required /></div>
    <div class="field"><label for="lastName">Last Name</label>
      <input id="lastName" name="lastName" type="text" autocomplete="family-name" required /></div>
    <div class="field"><label for="preferredName">Preferred Name</label>
      <input id="preferredName" name="preferredName" type="text" /></div>
    <div class="field"><label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="email" required /></div>
    <div class="field"><label for="phone">Phone</label>
      <input id="phone" name="phone" type="tel" autocomplete="tel" /></div>
    <div class="field"><label for="country">Country</label>
      <select id="country" name="country"><option value="">Select…</option><option value="US">United States</option><option value="CA">Canada</option></select></div>
    <div class="field"><label for="state">State/Province</label>
      <select id="state" name="state"><option value="">Select…</option><option value="AZ">AZ</option><option value="CA">CA</option><option value="NY">NY</option></select></div>
    <div class="field"><label for="linkedin">LinkedIn Profile</label>
      <input id="linkedin" name="linkedin" type="url" /></div>
    <div class="field"><label for="company">Current Company</label>
      <input id="company" name="company" type="text" /></div>
    <div class="field"><label for="resume">Resume/CV</label>
      <input id="resume" name="resume" type="file" required /></div>
    <div class="field"><label for="coverLetter">Cover Letter</label>
      <input id="coverLetter" name="coverLetter" type="file" /></div>

    <div class="field"><label for="pronouns">Pronouns</label>
      <select id="pronouns" name="pronouns"><option value="">Select…</option><option>He/him</option><option>She/her</option><option>They/them</option><option>Prefer not to say</option></select></div>

    <div class="field"><label for="sponsorNow">Do you currently require employer sponsorship to maintain work authorization?</label>
      <select id="sponsorNow" name="sponsorNow"><option value="">Select…</option><option>Yes</option><option>No</option></select></div>

    <div class="field">
      <label id="sponsorFuture-label" for="sponsorFuture">Will you require sponsorship in the future?</label>
      <input id="sponsorFuture" role="combobox" aria-labelledby="sponsorFuture-label" aria-autocomplete="list"
             aria-controls="sponsorFuture-listbox" aria-expanded="false" type="text" />
      <ul id="sponsorFuture-listbox" role="listbox">
        <li role="option">Yes, I will require sponsorship</li>
        <li role="option">No, I will not require sponsorship</li>
      </ul>
    </div>

    <div class="field"><label for="referral">How did you hear about this position?</label>
      <select id="referral" name="referral"><option value="">Select…</option><option>LinkedIn</option><option>Employee referral</option><option>Company website</option><option>Other</option></select></div>

    <div class="field"><label for="privacyPolicy">Candidate Privacy Policy</label>
      <select id="privacyPolicy" name="privacyPolicy"><option value="">Select…</option><option>I acknowledge that I have read and understood the Candidate Privacy Policy.</option></select></div>

    <div class="field"><label for="aiAttestation">Candidate AI Usage Attestation</label>
      <select id="aiAttestation" name="aiAttestation" required><option value="">Select…</option><option>I agree that all submitted materials are my original work and were completed without AI tools.</option></select></div>

    <div class="field"><label for="prevEmployed">Have you previously been employed by this company?</label>
      <select id="prevEmployed" name="prevEmployed"><option value="">Select…</option><option>Yes</option><option>No</option></select></div>

    <fieldset class="field"><legend>Voluntary Self-Identification — Gender</legend>
      <select id="gender" name="gender"><option value="">Decline to self-identify</option><option>Female</option><option>Male</option><option>Non-binary</option></select></fieldset>
    <fieldset class="field"><legend>Voluntary Self-Identification — Veteran Status</legend>
      <select id="veteran" name="veteran"><option value="">Decline to self-identify</option><option>I am a veteran</option><option>I am not a veteran</option></select></fieldset>
    <fieldset class="field"><legend>Voluntary Self-Identification — Race/Ethnicity</legend>
      <select id="race" name="race"><option value="">Decline to self-identify</option><option>Asian</option><option>Black or African American</option><option>Hispanic or Latino</option><option>White</option></select></fieldset>

    <button id="submit_app" type="submit">Submit Application</button>
  </form>
`;

/**
 * Sanitized approximation of the CURRENT Samsara / job-boards.greenhouse.io
 * application (the primary regression case for the field-ledger work). Covers
 * every control class the report listed as silently dropped:
 *   - required text (first/last name, city), preferred-name fields, email/phone
 *   - a CUSTOM div-based React-Select country combobox (no native input/select)
 *   - an ARIA combobox for future immigration sponsorship
 *   - native selects: work authorization, referral source, previously-worked
 *   - a MULTI-SELECT listbox ("Where have you learned about Samsara?")
 *   - a conditional "Other" follow-up (hidden until "Other" is chosen)
 *   - required consent + AI-policy acknowledgement checkboxes
 *   - optional demographic (EEO) selects
 *   - resume/cover-letter file inputs, LinkedIn, ZIP
 *   - a honeypot and a disabled field (must be excluded WITH a reason)
 * No live markup and no personal data.
 */
export const SAMSARA_GREENHOUSE_FIXTURE = `
  <main>
    <script src="https://job-boards.greenhouse.io/embed/job_board/js"></script>
    <form id="application-form" aria-label="Application for Software Engineer at Samsara">
      <div class="field"><label for="first_name">First Name *</label>
        <input id="first_name" name="job_application[first_name]" type="text" required /></div>
      <div class="field"><label for="last_name">Last Name *</label>
        <input id="last_name" name="job_application[last_name]" type="text" required /></div>
      <div class="field"><label for="pref_first">Preferred First Name</label>
        <input id="pref_first" name="job_application[preferred_first_name]" type="text" /></div>
      <div class="field"><label for="pref_last">Preferred Last Name</label>
        <input id="pref_last" name="job_application[preferred_last_name]" type="text" /></div>
      <div class="field"><label for="email">Email *</label>
        <input id="email" name="job_application[email]" type="email" autocomplete="email" required /></div>
      <div class="field"><label for="phone">Phone *</label>
        <input id="phone" name="job_application[phone]" type="tel" autocomplete="tel" required /></div>

      <!-- Country: a custom React-Select div control (NOT a native select) with a
           portal-style listbox referenced by aria-controls. -->
      <div class="field" data-required="true">
        <span id="country-label">Country *</span>
        <div id="country" class="select__control" role="combobox" aria-labelledby="country-label"
             aria-expanded="false" aria-controls="country-menu">
          <span class="select__placeholder">Select...</span>
        </div>
        <ul id="country-menu" role="listbox">
          <li role="option">United States</li>
          <li role="option">Canada</li>
          <li role="option">India</li>
        </ul>
      </div>

      <div class="field"><label for="city">Location (City) *</label>
        <input id="city" name="job_application[city]" type="text" autocomplete="address-level2" required /></div>
      <div class="field"><label for="zip">Postal / ZIP Code</label>
        <input id="zip" name="job_application[zip]" type="text" /></div>
      <div class="field"><label for="linkedin">LinkedIn Profile</label>
        <input id="linkedin" name="job_application[linkedin]" type="url" /></div>

      <div class="field"><label for="resume">Resume/CV *</label>
        <input id="resume" name="job_application[resume]" type="file" accept=".pdf,.doc,.docx" required /></div>
      <div class="field"><label for="cover">Cover Letter</label>
        <input id="cover" name="job_application[cover_letter]" type="file" accept=".pdf,.doc,.docx" /></div>

      <div class="field"><label for="work_auth">Do you have current legal authorization to work in the United States? *</label>
        <select id="work_auth" name="job_application[work_auth]" required>
          <option value="">Select...</option><option>Yes</option><option>No</option></select></div>

      <!-- Future immigration sponsorship: ARIA combobox, required. -->
      <div class="field" data-required="true">
        <label id="sponsor-label" for="sponsor">Will you now or in the future require Samsara to sponsor an immigration case? *</label>
        <input id="sponsor" name="job_application[sponsor]" role="combobox" aria-labelledby="sponsor-label"
               aria-autocomplete="list" aria-controls="sponsor-menu" aria-expanded="false" type="text" required />
        <ul id="sponsor-menu" role="listbox">
          <li role="option">Yes</li><li role="option">No</li></ul>
      </div>

      <div class="field"><label for="referral">How did you hear about this opportunity?</label>
        <select id="referral" name="job_application[referral]">
          <option value="">Select...</option><option>LinkedIn</option><option>Job board</option>
          <option>Employee referral</option><option>Company website</option></select></div>
      <div class="field"><label for="prev_samsara">Have you previously worked at Samsara? *</label>
        <select id="prev_samsara" name="job_application[prev_samsara]" required>
          <option value="">Select...</option><option>Yes</option><option>No</option></select></div>

      <!-- Multi-select: "Where have you learned about Samsara?" -->
      <div class="field" data-required="true">
        <span id="learned-label">Where have you learned about Samsara? (select all that apply) *</span>
        <div id="learned" role="listbox" aria-multiselectable="true" aria-labelledby="learned-label">
          <div role="option">LinkedIn</div><div role="option">Instagram</div>
          <div role="option">Friend or colleague</div><div role="option">Industry event</div>
          <div role="option">Other</div>
        </div>
      </div>
      <!-- Conditional follow-up: only applicable once "Other" is chosen above. -->
      <div class="field" id="other-field" style="display:none">
        <label for="learned_other">If you selected Other, please provide additional details</label>
        <input id="learned_other" name="job_application[learned_other]" type="text" />
      </div>

      <div class="field">
        <label><input id="consent" name="job_application[consent]" type="checkbox" required />
          I acknowledge the Processing of Personal Data notice *</label></div>
      <div class="field">
        <label><input id="ai_policy" name="job_application[ai_policy]" type="checkbox" required />
          I have read the AI Policy for Interviewers *</label></div>

      <div class="field"><label for="pronouns">Pronouns</label>
        <select id="pronouns" name="job_application[pronouns]">
          <option value="">Select...</option><option>He/him</option><option>She/her</option>
          <option>They/them</option><option>Prefer not to say</option></select></div>

      <fieldset class="field"><legend>Voluntary Self-Identification</legend>
        <label for="gender">Gender identity</label>
        <select id="gender" name="job_application[gender]">
          <option value="">Decline to self-identify</option><option>Female</option><option>Male</option><option>Non-binary</option></select>
        <label for="race">Race / Ethnicity</label>
        <select id="race" name="job_application[race]">
          <option value="">Decline to self-identify</option><option>Asian</option><option>White</option><option>Hispanic or Latino</option></select>
        <label for="veteran">Veteran status</label>
        <select id="veteran" name="job_application[veteran]">
          <option value="">Decline to self-identify</option><option>I am a veteran</option><option>I am not a veteran</option></select>
        <label for="disability">Disability status</label>
        <select id="disability" name="job_application[disability]">
          <option value="">Decline to self-identify</option><option>Yes</option><option>No</option></select>
      </fieldset>

      <input type="text" name="job_application[_hp_email]" tabindex="-1" style="position:absolute;left:-9999px" />
      <input id="internal_ref" name="job_application[internal_ref]" type="text" disabled />
      <button id="submit_app" type="submit">Submit Application</button>
    </form>
  </main>
`;

/** Build a DiscoveredField-friendly document body from a fixture string. */
export function mountFixture(html: string): Document {
  document.body.innerHTML = html;
  return document;
}

/**
 * Sanitized reproduction of the LIVE Samsara failure shape: the application is a
 * React app that renders NO <form> element at all, while the site chrome DOES
 * contain a real <form> (the global search). The old "pick the <form> with the
 * most inputs" rule therefore selected the search box — producing
 * "Discovered: 1, Filled: 1" with every real field blank.
 */
export const SAMSARA_LIVE_SHAPE_FIXTURE = `
  <header>
    <nav aria-label="Main">
      <form role="search" action="/search" class="site-search">
        <input type="search" id="site-search" name="q" placeholder="Search Samsara" aria-label="Search" />
        <button type="submit">Search</button>
      </form>
    </nav>
  </header>
  <main>
    <h1>Apply for this job</h1>
    <!-- The whole application: React-rendered, NO <form> wrapper. -->
    <div id="application" data-ui="application-form">
      <div class="field"><label for="first_name">First Name *</label>
        <input id="first_name" name="job_application[first_name]" type="text" required /></div>
      <div class="field"><label for="last_name">Last Name *</label>
        <input id="last_name" name="job_application[last_name]" type="text" required /></div>
      <div class="field"><label for="email">Email *</label>
        <input id="email" name="job_application[email]" type="email" required /></div>
      <div class="field"><label for="phone">Phone *</label>
        <input id="phone" name="job_application[phone]" type="tel" required /></div>
      <div class="field"><label for="city">Location (City) *</label>
        <input id="city" name="job_application[city]" type="text" required /></div>
      <div class="field"><label for="zip">Postal / ZIP Code</label>
        <input id="zip" name="job_application[zip]" type="text" /></div>
      <div class="field"><label for="resume">Resume/CV *</label>
        <input id="resume" name="job_application[resume]" type="file" required /></div>
      <div class="field" data-required="true">
        <span id="country-label">Country *</span>
        <div id="country" class="select__control" role="combobox" aria-labelledby="country-label"
             aria-expanded="false" aria-controls="country-menu"><span class="select__placeholder">Select...</span></div>
        <ul id="country-menu" role="listbox"><li role="option">United States</li><li role="option">India</li></ul>
      </div>
      <button id="submit_app" type="button">Submit Application</button>
    </div>
  </main>
  <footer>
    <form class="newsletter" action="/subscribe">
      <label for="nl">Subscribe to our newsletter</label>
      <input id="nl" name="newsletter_email" type="email" />
      <button type="submit">Subscribe</button>
    </form>
  </footer>
`;

/**
 * Sanitized reproduction of the LIVE MongoDB failure shape: a global site search
 * in the header ("Search products, whitepapers, & more...") that was misread as
 * an application question, plus an unrelated marketing checkbox group in page
 * content that surfaced as "This question", alongside the REAL application form.
 */
export const MONGODB_LIVE_SHAPE_FIXTURE = `
  <header class="global-nav">
    <form class="search-form" role="search">
      <input id="global-search" type="text" name="search"
             placeholder="Search products, whitepapers, & more..." aria-label="Search" />
    </form>
  </header>
  <section class="marketing-content">
    <fieldset>
      <legend>Which topics interest you?</legend>
      <label><input type="checkbox" name="topic_atlas" /> Atlas</label>
      <label><input type="checkbox" name="topic_search" /> Search</label>
    </fieldset>
  </section>
  <main>
    <h2>Apply for this job</h2>
    <form id="careers-application" action="/careers/apply">
      <div class="field"><label for="mdb_first">First Name *</label>
        <input id="mdb_first" name="firstName" type="text" required /></div>
      <div class="field"><label for="mdb_last">Last Name *</label>
        <input id="mdb_last" name="lastName" type="text" required /></div>
      <div class="field"><label for="mdb_email">Email *</label>
        <input id="mdb_email" name="email" type="email" required /></div>
      <div class="field"><label for="mdb_resume">Resume/CV *</label>
        <input id="mdb_resume" name="resume" type="file" required /></div>
      <fieldset class="field">
        <legend>Have you previously worked at MongoDB? *</legend>
        <select id="mdb_prev" name="prev" required>
          <option value="">Select...</option><option>Yes</option><option>No</option>
        </select>
      </fieldset>
      <button type="submit">Submit Application</button>
    </form>
  </main>
`;
